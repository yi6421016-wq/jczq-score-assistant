#!/bin/zsh
# 催跑：盯 GitHub Actions 的定时有没有真的响。
# 云端是唯一出票/兑奖端（本机已 SIMBANK_READ_ONLY），所以定时器哑火就等于整天没票。
# 11-22 就是如此：早上两个 模拟投注 时段一条 schedule 运行都没有，只剩手动 dispatch。
# 每个时段过了 12 分钟还查不到当天运行，就手动补触发一次；一轮每个工作流最多补一个
# （一次运行就是满盘重算，补三个没意义），触发过就算数，失败的不再重补，免得刷屏。
set -u
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"
export TZ=Asia/Shanghai

ROOT="$HOME/竞彩比分助手"
LOG="$ROOT/云端催跑.log"
REPO="yi6421016-wq/jczq-score-assistant"

if ! command -v gh >/dev/null 2>&1; then
  print -r -- "[$(date '+%F %T')] 找不到 gh，跳过本次" >> "$LOG"; exit 0
fi
if ! gh run list --repo "$REPO" --workflow 模拟投注 --limit 1 >/dev/null 2>&1; then
  print -r -- "[$(date '+%F %T')] gh 取不到运行记录（凭据或网络问题），跳过本次" >> "$LOG"; exit 0
fi

python3 - "$REPO" "$LOG" <<'PY'
import datetime
import json
import os
import subprocess
import sys

repo, logp = sys.argv[1:3]
MIN_WAIT, MAX_LOOK, MAX_ASK = 12, 180, 2       # 过几分钟算哑火 / 只补 3 小时内的 / 单轮上限
CST = datetime.timezone(datetime.timedelta(hours=8))

# 北京时段：赛果核对先补齐跨零点场次，模拟投注再出票兑奖
SLOTS = [("赛果核对", "verify.yml", ("09:10", "09:50", "10:30")),
         ("模拟投注", "simbank.yml", ("11:00", "12:20", "15:00", "19:20"))]

now = datetime.datetime.now(CST)


def log(msg):
    with open(logp, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (now.strftime("%F %T"), msg))


def utc_to_cst(text):
    dt = datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(CST)


asked = 0
for name, file_, times in SLOTS:
    due = []
    for hhmm in times:
        slot = datetime.datetime.combine(now.date(),
                                         datetime.time(int(hhmm[:2]), int(hhmm[3:])), tzinfo=CST)
        age = (now - slot).total_seconds() / 60.0
        if MIN_WAIT <= age <= MAX_LOOK:
            due.append((hhmm, slot))
    if not due:
        continue
    hhmm, slot = due[-1]                       # 只看最近一个到点的时段
    out = subprocess.run(["gh", "run", "list", "--repo", repo, "--workflow", name,
                          "--limit", "25", "--json", "createdAt,status,event"],
                         capture_output=True, text=True)
    if out.returncode:
        log("%s 运行记录读不到：%s" % (name, (out.stderr or out.stdout).strip()[:100]))
        continue
    rows = json.loads(out.stdout)
    if any(r["status"] in ("in_progress", "queued") for r in rows):
        log("%s %s 时段未见运行，但已有任务在排队/执行，本次不补" % (name, hhmm))
        continue
    ran = [utc_to_cst(r["createdAt"]) for r in rows if utc_to_cst(r["createdAt"]) >= slot]
    if ran:
        continue
    if asked >= MAX_ASK:
        log("单轮补触发达上限 %d，%s %s 留给下一轮" % (MAX_ASK, name, hhmm))
        continue
    r = subprocess.run(["gh", "workflow", "run", file_, "--repo", repo],
                       capture_output=True, text=True)
    if r.returncode:
        log("%s %s 补触发失败：%s" % (name, hhmm, (r.stderr or r.stdout).strip()[:120]))
        continue
    asked += 1
    log("%s %s 定时未响，已手动补触发（今天第 %d 次补）" % (name, hhmm, asked))

if asked:
    pull = subprocess.run(["/bin/zsh", os.path.expanduser("~/竞彩比分助手/从云端拉取.command"), "--auto"],
                          capture_output=True, text=True)
    line = [l.strip() for l in pull.stdout.splitlines() if l.strip().startswith("台账：")]
    log("补触发后拉取：%s" % (line[0] if line else "退出码 %d，稍后定时拉取追平" % pull.returncode))
PY
