#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩比分助手（Mac 版）

每天自动抓取竞彩赛程、胜平负与半全场赔率 → Shin 去水 + Dixon-Coles 模型反解期望进球 →
给出比分概率分布与九档半全场概率 → 赛后自动核对比分、半全场并累计校准指标 → 生成 HTML 日报与汇总页。

数据目录：~/竞彩比分助手/（旧路径 ~/Documents/竞彩比分助手 是指向它的软链接；
launchd 定时任务不能走 Documents，macOS 会拦权限）
  数据/   每日赛前记录 JSON + CSV
  报告/   比分日报_YYYY-MM-DD.html、汇总.html
  运行日志.txt

命令行：
  ./竞彩比分助手 --daily              每日自动流程（赛前封存 + 核对 + 出报告）
  ./竞彩比分助手 --fetch 2026-10-30   只抓取并封存某天
  ./竞彩比分助手 --report 2026-10-30  重出某天报告（--report all 重出全部历史）
  ./竞彩比分助手 --recheck 2026-10-30 用最新赔率复核未开赛场次（不改封存记录）
  ./竞彩比分助手 --parlay 2026-10-30  按模型取前四场做四串一模拟记录
  ./竞彩比分助手 --parlay 2026-10-30 --4x11   同四场，记 4 串 11（每注 2 元）
  ./竞彩比分助手 --parlay-settle      用已核对的比分给模拟串关开奖
  ./竞彩比分助手 --backfill [起 止]    回溯抓取 500 保留期内的历史赛程+赔率+赛果（数据/回溯/）
  ./竞彩比分助手 --backtest           在回溯+封存样本上做口径配对回测，并刷新 ρ 校准（报告/比分口径回测.html）
  ./竞彩比分助手 --pro-daily [日期]    生成四模型日报（Dixon-Coles / Elo / xG / 机器学习）
  ./竞彩比分助手 --pro-backtest       跑四模型走步回测，刷新新版回测页
  ./竞彩比分助手 --pro-report [日期]  打开四模型日报
  ./竞彩比分助手 --pro-score-rank [日期]  生成并打开比分胜率排序页
  ./竞彩比分助手 --pro-recommend [日期]   生成今日推荐页（最稳 5 场，四玩法各一注；--no-open 只生成）
  ./竞彩比分助手 --pro-analysis [日期]    打开当日逐场全解析文档
  ./竞彩比分助手 --gui-pro           打开图形界面并直接进入四模型页
  ./竞彩比分助手 --gui-sim           打开图形界面并直接进入模拟投注页
  ./竞彩比分助手 --open               打开今天的报告
  ./竞彩比分助手                      打开图形界面

只输出概率分布与校准结果，不产生任何投注建议。
"""
import csv
import datetime as dt
import html as H
import itertools
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

APP_NAME = "竞彩比分助手"
BASE = Path(os.environ.get("JCZQ_HOME", str(Path.home() / APP_NAME)))
DATA_DIR = BASE / "数据"
REPORT_DIR = BASE / "报告"
RECHECK_DIR = DATA_DIR / "复核"
BACK_DIR = DATA_DIR / "回溯"  # 回溯样本库，不进日报与汇总
LOG_FILE = BASE / "运行日志.txt"
SOURCE = "https://trade.500.com/jczq/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAXG = 9
GRID = 7
# 半全场：前一位是半场结果、后一位是全场结果，3=主胜 1=平 0=客胜
HF_VALUES = ["3-3", "3-1", "3-0", "1-3", "1-1", "1-0", "0-3", "0-1", "0-0"]
HF_TXT = {"3": "胜", "1": "平", "0": "负"}
HF_SHARE = 0.40                      # 半场进球占全场的比例（191 场回测 0.38—0.40 最优）
HF_GATE = 0.45                       # 半全场高把握门槛：低于此值只展示、不建议出票
HF_MID = 0.38                        # 半全场一般把握下限
# 翻盘格校准：191 场里“半场领先、全场被翻”（胜/负）实际 4.7%、模型 2.5%；
# “半场落后、全场翻盘”（负/胜）实际 4.2%、模型 2.9%。按实测比例只补七成，
# 留三成作小样本噪声缓冲，避免为了 9 场、8 场把分布拧过头。
HF_COMEBACK = {"3-0": 1.60, "0-3": 1.30}
BQC_SOURCE = "https://trade.500.com/jczq/?playid=272&g=2&date=%s"
BF_SOURCE = "https://trade.500.com/jczq/?playid=271&g=2&date=%s"
JQS_SOURCE = "https://trade.500.com/jczq/?playid=270&g=2&date=%s"
BF_ROW_RE = re.compile(r'<tr class="bet-tb-tr[^"]*"')
MODEL_KEYS = ["dc", "poi", "mkt", "tgs", "ens"]
MODEL_TXT = {"dc": "Dixon-Coles", "poi": "泊松", "mkt": "市场比分",
             "tgs": "总进球", "ens": "共识"}
MODEL_SHORT = {"dc": "DC", "poi": "泊松", "mkt": "市场", "tgs": "总进球", "ens": "共识"}
TAIL_KIND = {"胜其它": 0, "平其它": 1, "负其它": 2}   # 0=主胜 1=平 2=客胜
# 共识权重：市场比分（31 档）与总进球（8 档）合计六成，DC 保留低比分修正
ENS_W = {"dc": 0.30, "poi": 0.10, "mkt": 0.35, "tgs": 0.25}

LOG_SINK = None  # GUI 注入的日志回调


def log(msg):
    line = "[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg)
    if sys.stdout is not None:
        print(line, flush=True)
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
    if LOG_SINK:
        try:
            LOG_SINK(line)
        except Exception:
            pass


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 四模型版桥接
PRO_DIR = BASE / "新版"
PRO_CODE_DIR = PRO_DIR / "代码"
PRO_DATA_DIR = PRO_DIR / "数据"
PRO_REPORT_DIR = PRO_DIR / "报告"
PRO_CONFIG = PRO_DIR / "运行配置.json"


def pro_config():
    try:
        cfg = json.loads(PRO_CONFIG.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError("四模型版未安装：读不到 %s（%s）" % (PRO_CONFIG, e))
    py = Path(cfg.get("python") or "")
    code = Path(cfg.get("code") or PRO_CODE_DIR)
    if not py.exists():
        raise RuntimeError("四模型版 Python 不存在：%s" % py)
    if not (code / "jczq_pro" / "__main__.py").exists():
        raise RuntimeError("四模型版代码不存在：%s" % code)
    return py, code


def pro_run(*args, timeout=1800):
    py, code = pro_config()
    cmd = [str(py), "-m", "jczq_pro", *[str(a) for a in args]]
    log("四模型：%s" % " ".join(cmd[2:]))
    p = subprocess.run(cmd, cwd=str(code), capture_output=True, text=True, timeout=timeout)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    for line in out.splitlines()[-30:]:
        log("  " + line)
    if p.returncode != 0:
        tail = out.splitlines()[-1] if out else "无输出"
        raise RuntimeError("四模型命令失败（%d）：%s" % (p.returncode, tail))
    return out


def pro_daily(day=None, report=True, backtest=False):
    day = day or today_str()
    args = ["daily", "--date", day]
    if not backtest:
        args.append("--no-backtest")
    if not report:
        args.append("--no-report")
    pro_run(*args)
    return PRO_DATA_DIR / ("预测_%s.json" % day)


def pro_snapshot(day):
    p = PRO_DATA_DIR / ("预测_%s.json" % day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def pro_report_of(day):
    p = PRO_REPORT_DIR / ("比分日报_四模型_%s.html" % day)
    return p if p.exists() else None


def pro_backtest_report():
    files = sorted(PRO_REPORT_DIR.glob("回测_四模型_*.html"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def pro_score_rank_report(day):
    p = PRO_REPORT_DIR / ("比分胜率排行_%s.html" % day)
    return p if p.exists() else None


def pro_recommend_report(day):
    p = PRO_REPORT_DIR / ("今日推荐_%s.html" % day)
    return p if p.exists() else None


def pro_recommend_data(day):
    p = PRO_DATA_DIR / ("推荐_%s.json" % day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def pro_upset_report(day):
    p = PRO_REPORT_DIR / ("爆冷比分_%s.html" % day)
    return p if p.exists() else None


def pro_upset_data(day):
    p = PRO_DATA_DIR / ("爆冷比分_%s.json" % day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def pro_parlay45_report(day):
    p = PRO_REPORT_DIR / ("爆冷四串五_%s.html" % day)
    return p if p.exists() else None


def pro_parlay45_data(day):
    p = PRO_DATA_DIR / ("爆冷四串五_%s.json" % day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def pro_analysis_report(day):
    p = PRO_REPORT_DIR / ("当日全解析_%s.html" % day)
    return p if p.exists() else None


# ---------------------------------------------------------------- 抓取
def http_get(url, timeout=12, tries=2):
    """先用系统 curl（HTTP/2，通常更快），失败再退回 urllib。"""
    last = None
    for i in range(tries):
        try:
            p = subprocess.run(
                ["curl", "-sL", "--compressed", "-m", str(timeout), "-A", UA,
                 "-H", "Referer: https://www.500.com/", url],
                capture_output=True)
            body = p.stdout or b""
            if len(body) > 20000 and (p.returncode == 0 or body.rstrip().endswith(b"</html>")):
                return body.decode("gb18030", "ignore")
            last = RuntimeError("curl 返回 %s，%d 字节" % (p.returncode, len(p.stdout)))
        except Exception as e:
            last = e
        log("抓取失败（第 %d/%d 次）：%s" % (i + 1, tries, last))
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://www.500.com/",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            if len(raw) > 20000:
                return raw.decode("gb18030", "ignore")
        except Exception as e:
            last = e
        if i < tries - 1:
            time.sleep(1.2 * (i + 1))
    raise last


TR_RE = re.compile(r'<tr class="bet-tb-tr[^"]*"(.*?)</tr>', re.S)


def handicap_of(title):
    """“客队让1球” → 主队 +1；“主队让2球” → -2；“不让球/平手” → 0。"""
    t = (title or "").replace(" ", "")
    if "不让球" in t or "平手" in t:
        return 0
    m = re.match(r"(主队|客队)(受让|让)(\d+)球", t)
    if not m:
        return None
    side, kind, n = m.group(1), m.group(2), int(m.group(3))
    if side == "主队":
        return -n if kind == "让" else n
    return n if kind == "让" else -n
ATTR_RE = re.compile(r'(data-[a-z]+)="([^"]*)"')


def _attrs(tag):
    return dict(ATTR_RE.findall(tag))


def parse_matches(page):
    """从 500 竞彩交易页解析：场次、联赛、对阵、开球、不让球胜平负赔率、完场比分。"""
    out = []
    for tr in TR_RE.findall(page):
        a = _attrs(tr[:tr.find(">") + 1])
        no = a.get("data-matchnum", "").strip()
        if not no:
            continue
        m = re.search(r'class="team-l" title="([^"]+)"', tr)
        n = re.search(r'class="team-r" title="([^"]+)"', tr)
        if not m or not n:
            continue
        lg = re.search(r'zuqiu-\d+/"[^>]*>([^<]+)<', tr)
        lg = lg.group(1).strip() if lg else ""

        # 让球列与赔率行按顺序一一对应；每行取一组胜平负赔率
        rang = re.findall(r'class="[^"]*itm-rangA(\d)"[^>]*title="([^"]*)"', tr)
        rows = re.findall(r'<div class="betbtn-row[^"]*">(.*?)</div>', tr, re.S)
        markets = []
        for idx, title in rang:
            i = int(idx) - 1
            if i >= len(rows):
                continue
            row = [None, None, None]
            for tag in re.findall(r"<p [^>]*>", rows[i]):
                pa = _attrs(tag)
                if pa.get("data-type") not in ("nspf", "spf"):
                    continue
                try:
                    row[{"3": 0, "1": 1, "0": 2}[pa.get("data-value", "")]] = float(pa.get("data-sp", "0"))
                except Exception:
                    continue
            h = handicap_of(title)
            if h is None or not all(x and x > 1.0 for x in row):
                continue
            if any(mk["h"] == h for mk in markets):
                continue
            markets.append({"h": h, "odds": row})
        odds = [None, None, None]
        for mk in markets:
            if mk["h"] == 0:
                odds = mk["odds"]
                break
        hc = next((mk for mk in markets if mk["h"] != 0), None)

        sc = re.search(r'class="score"[^>]*>([^<]*)<', tr)
        score = sc.group(1).strip() if sc else ""
        if not re.match(r"^\d+:\d+$", score or ""):
            score = ""

        kick = ""
        if a.get("data-matchdate") and a.get("data-matchtime"):
            kick = "%s %s" % (a["data-matchdate"], a["data-matchtime"])

        out.append({
            "no": no, "num": re.sub(r"\D", "", no) or no,
            "league": lg, "home": m.group(1), "away": n.group(1),
            "kickoff": kick, "odds": odds, "score": score,
            "fid": a.get("data-fixtureid", ""), "hc": hc,
        })
    # 去重
    seen, uniq = set(), []
    for x in out:
        if x["no"] in seen:
            continue
        seen.add(x["no"])
        uniq.append(x)
    return uniq


def parse_bqc(page):
    """解析半全场页：每场 9 档赔率，完场后 betbtn-ok 标出中奖档。

    返回 {场次: {"odds": [9 个赔率], "result": "3-1"|None}}
    """
    out = {}
    for tr in TR_RE.findall(page):
        a = _attrs(tr[:tr.find(">") + 1])
        no = a.get("data-matchnum", "").strip()
        if not no:
            continue
        vals = {}
        for tag in re.findall(r"<p [^>]*>", tr):
            pa = _attrs(tag)
            if pa.get("data-type") != "bqc":
                continue
            try:
                vals[pa.get("data-value", "")] = float(pa.get("data-sp", "0"))
            except Exception:
                continue
        m = re.search(r'class="[^"]*betbtn-ok[^"]*"[^>]*data-type="bqc"[^>]*data-value="([^"]+)"', tr)
        res = m.group(1) if m else None
        if vals or res:
            out[no] = {"odds": [vals.get(k) for k in HF_VALUES], "result": res}
    return out


def fetch_bqc(day):
    """抓某天的半全场赔率与中奖档；失败返回空 dict，不影响主流程。"""
    for i in range(2):
        try:
            page = http_get(BQC_SOURCE % day, timeout=10, tries=1)
        except Exception:
            continue
        got = parse_bqc(page)
        if got:
            return got
        time.sleep(1)
    return {}


def parse_bf(page):
    """解析比分页：每场 31 档（含“胜/平/负其它”），完场后 betbtn-ok 标出中奖档。

    比分选项放在行内的嵌套小表里，普通 `<tr>...</tr>` 会在第一个内层行截断，
    所以按比赛行的起始位置切片。
    """
    starts = [m.start() for m in BF_ROW_RE.finditer(page)]
    out = {}
    for i, s in enumerate(starts):
        chunk = page[s:starts[i + 1] if i + 1 < len(starts) else len(page)]
        no = _attrs(chunk[:chunk.find(">") + 1]).get("data-matchnum", "").strip()
        if not no:
            continue
        odds = {}
        for tag in re.findall(r'<p [^>]*data-type="bf"[^>]*>', chunk):
            pa = _attrs(tag)
            v = pa.get("data-value", "")
            try:
                sp = float(pa.get("data-sp", "0"))
            except Exception:
                continue
            if v and sp > 1.0:
                odds[v] = sp
        m = re.search(r'class="[^"]*betbtn-ok[^"]*"[^>]*data-type="bf"[^>]*data-value="([^"]+)"', chunk)
        res = m.group(1) if m else None
        if len(odds) >= 20 or res:
            out[no] = {"odds": odds, "result": res}
    return out


def fetch_bf(day):
    """抓某天的比分赔率；失败返回空 dict，不影响主流程。"""
    for i in range(2):
        try:
            page = http_get(BF_SOURCE % day, timeout=10, tries=1)
        except Exception:
            continue
        got = parse_bf(page)
        if got:
            return got
        time.sleep(1)
    return {}


def parse_jqs(page):
    """解析总进球页：每场 0—7+ 共 8 档赔率。返回 {场次: {进球数: 赔率}}。"""
    starts = [m.start() for m in BF_ROW_RE.finditer(page)]
    out = {}
    for i, s in enumerate(starts):
        chunk = page[s:starts[i + 1] if i + 1 < len(starts) else len(page)]
        no = _attrs(chunk[:chunk.find(">") + 1]).get("data-matchnum", "").strip()
        if not no:
            continue
        vals = {}
        for tag in re.findall(r'<p [^>]*data-type="jqs"[^>]*>', chunk):
            pa = _attrs(tag)
            try:
                vals[int(pa.get("data-value", "-1"))] = float(pa.get("data-sp", "0"))
            except Exception:
                continue
        if len(vals) >= 6:
            out[no] = vals
    return out


def fetch_jqs(day):
    """抓某天的总进球赔率；失败返回空 dict，不影响主流程。"""
    for i in range(2):
        try:
            page = http_get(JQS_SOURCE % day, timeout=10, tries=1)
        except Exception:
            continue
        got = parse_jqs(page)
        if got:
            return got
        time.sleep(1)
    return {}


WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def card_day(day):
    return WD[dt.date.fromisoformat(day).weekday()]


def fetch_day(day, quiet=False, attempts=4, wait=5):
    """抓某一天的赛程。

    500 的销售列表每天 11:00 前后整体切换，切换期间会返回“结构正常但没有比赛行”
    的页面。这里按不同 URL 重试并逐次拉长间隔，避免把站点维护误判成页面改版。
    """
    if not quiet:
        log("正在抓取 %s 的赛程与赔率…" % day)
    urls = [
        "%s?date=%s&_=%d" % (SOURCE, day, int(time.time())),
        "https://trade.500.com/jczq/index.php?date=%s" % day,
        "%s?date=%s" % (SOURCE, day),
    ]
    last_err = None
    for i in range(attempts):
        url = urls[min(i, len(urls) - 1)]
        page = ""
        try:
            page = http_get(url)
        except Exception as e:
            last_err = e
            if not quiet:
                log("第 %d/%d 次请求失败：%s" % (i + 1, attempts, e))
        else:
            rows = parse_matches(page)
            if not quiet:
                log("第 %d/%d 次：%d 字节，解析 %d 场" % (i + 1, attempts, len(page), len(rows)))
            if not rows:
                last_err = RuntimeError(
                    "页面 %d 字节但没有比赛行（站点可能在切换销售列表）" % len(page))
            else:
                ok = [r for r in rows if all(o and o > 1.0 for o in r["odds"])]
                # 页面会同时列出正在销售的两三天，只取本比赛日（按“周X”前缀）
                same = [r for r in ok if r["no"][:2] == card_day(day)]
                if same:
                    if not quiet:
                        log("抓取完成：%d 场，其中 %d 场有胜平负赔率" % (len(rows), len(same)))
                    bq = fetch_bqc(day)
                    for r in same:
                        b = bq.get(r["no"])
                        if not b:
                            continue
                        if b.get("odds"):
                            r["bq_odds"] = b["odds"]
                        if b.get("result"):
                            r["bq_result"] = b["result"]
                    if not quiet and bq:
                        got = sum(1 for r in same if r.get("bq_odds")
                                  and all(x and x > 1.0 for x in r["bq_odds"]))
                        log("半全场赔率：%d/%d 场" % (got, len(same)))
                    bf = fetch_bf(day)
                    for r in same:
                        b = bf.get(r["no"])
                        if not b:
                            continue
                        if b.get("odds") and len(b["odds"]) >= 20:
                            r["bf_odds"] = b["odds"]
                        if b.get("result"):
                            r["bf_result"] = b["result"]
                    if not quiet and bf:
                        got = sum(1 for r in same if r.get("bf_odds"))
                        log("比分赔率：%d/%d 场" % (got, len(same)))
                    jq = fetch_jqs(day)
                    for r in same:
                        o = jq.get(r["no"])
                        if o:
                            r["jq_odds"] = {str(k): v for k, v in o.items()}
                    if not quiet and jq:
                        got = sum(1 for r in same if r.get("jq_odds"))
                        log("总进球赔率：%d/%d 场" % (got, len(same)))
                    return same
                last_err = RuntimeError(
                    "页面已列出 %d 场，但还没有%s的比赛（可能尚未开售）"
                    % (len(rows), card_day(day)))
                if not quiet:
                    log(last_err.args[0])
        if i < attempts - 1:
            time.sleep(wait * (i + 1))
    raise last_err or RuntimeError("未解析到比赛（页面结构可能变化）")


# ---------------------------------------------------------------- 模型
def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def pvec(lam, n=GRID):
    return [pois(k, lam) for k in range(n)]


def onex(vh, va):
    sh, sa = sum(vh), sum(va)
    h = d = a = 0.0
    for i in range(len(vh)):
        for j in range(len(va)):
            p = vh[i] * va[j]
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
    z = sh * sa
    return h / z, d / z, a / z


def matrix(lh, la, n=MAXG):
    vh, va = pvec(lh, n), pvec(la, n)
    m = {(i, j): vh[i] * va[j] for i in range(n) for j in range(n)}
    s = sum(m.values())
    return {k: v / s for k, v in m.items()}


def devig(odds):
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [x / s for x in inv], s


def fit(ph, pd, pa):
    """两级网格搜索：粗 0.1 → 细 0.02 → 0.004，返回 (误差, λ主, λ客)。"""
    best = (9e9, 1.3, 1.1)
    lo1, hi1, lo2, hi2, step = 0.05, 3.6, 0.05, 3.6, 0.1
    for _ in range(3):
        b = None
        v = lo1
        while v <= hi1 + 1e-9:
            w = lo2
            vv = pvec(v)
            while w <= hi2 + 1e-9:
                h, d, a = onex(vv, pvec(w))
                e = (h - ph) ** 2 + (d - pd) ** 2 + (a - pa) ** 2
                if b is None or e < b[0]:
                    b = (e, v, w)
                w += step
            v += step
        best = b
        lo1, hi1 = max(0.02, best[1] - step), best[1] + step
        lo2, hi2 = max(0.02, best[2] - step), best[2] + step
        step = step / 5.0
    # 返回误差时用 9×9 复核
    m = matrix(best[1], best[2])
    h, d, a = onex9(m)
    err = (h - ph) ** 2 + (d - pd) ** 2 + (a - pa) ** 2
    return err, best[1], best[2]


def onex9(m):
    h = sum(v for (i, j), v in m.items() if i > j)
    d = sum(v for (i, j), v in m.items() if i == j)
    a = sum(v for (i, j), v in m.items() if i < j)
    return h, d, a


def shin(odds):
    """Shin (1993) 去水：修正热门—冷门偏误，返回 (概率, 原始倒数和)。"""
    q = [1.0 / o for o in odds]
    s = sum(q)
    if s <= 1.0000001:
        return [x / s for x in q], s

    def probs(z):
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / s) - z) / (2 * (1 - z))
                for x in q]

    lo, hi = 0.0, 0.45
    for _ in range(50):
        mid = (lo + hi) / 2
        if sum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2)
    t = sum(p)
    return [x / t for x in p], s


def dc_matrix(lh, la, rho=0.0, n=MAXG):
    """Dixon-Coles 修正的比分矩阵：对 0-0、1-0、0-1、1-1 四个低比分格加权。"""
    m = {}
    for i in range(n):
        for j in range(n):
            p = pois(i, lh) * pois(j, la)
            if rho:
                if i == 0 and j == 0:
                    p *= max(1e-6, 1 - lh * la * rho)
                elif i == 0 and j == 1:
                    p *= max(1e-6, 1 + lh * rho)
                elif i == 1 and j == 0:
                    p *= max(1e-6, 1 + la * rho)
                elif i == 1 and j == 1:
                    p *= max(1e-6, 1 - rho)
            m[(i, j)] = p
    s = sum(m.values())
    return {k: v / s for k, v in m.items()}


def bf_matrix(odds, base):
    """把竞彩比分 31 档赔率转成 9×9 概率矩阵（Shin 去水）。

    “胜其它/平其它/负其它”是未单列的比分（如 4-3、6-0、4-4），
    这部分概率按 base 矩阵里同方向、未单列各格的形状摊开。
    赔率不足 20 档时返回 None。
    """
    if not isinstance(odds, dict):
        if isinstance(odds, (list, tuple)) and len(odds) >= 20:
            odds = {str(k): v for k, v in enumerate(odds)}
        else:
            return None
    labels = [k for k in odds if isinstance(odds[k], (int, float)) and odds[k] > 1.0]
    if len(labels) < 20:
        return None
    try:
        probs, _ = shin([float(odds[k]) for k in labels])
    except Exception:
        return None
    named, tails = {}, {}
    for k, pr in zip(labels, probs):
        if ":" in k:
            try:
                i, j = (int(x) for x in k.split(":"))
            except Exception:
                continue
            if 0 <= i < MAXG and 0 <= j < MAXG:
                named[(i, j)] = named.get((i, j), 0.0) + pr
        elif k in TAIL_KIND:
            tails[k] = pr
    mx = {c: named.get(c, 0.0) for c in base}
    for k, pr in tails.items():
        kind = TAIL_KIND[k]
        cond = (lambda i, j: i > j) if kind == 0 else (
            (lambda i, j: i == j) if kind == 1 else (lambda i, j: i < j))
        cells = [c for c in mx if c not in named and cond(c[0], c[1])]
        w = sum(base.get(c, 0.0) for c in cells)
        if w <= 0:
            continue
        for c in cells:
            mx[c] += pr * base.get(c, 0.0) / w
    s = sum(mx.values())
    if s <= 0:
        return None
    return {c: v / s for c, v in mx.items()}


def tgs_matrix(odds, base):
    """总进球模型：市场 0—7+ 总进球分布 × DC 的“同总进球内”比分形状。

    odds 形如 {"0": 9.0, ...} 或 {0: 9.0, ...}；base 用于分配每个总进球档内
    主胜/平/客胜的具体比分。缺数据返回 None。
    """
    if not odds:
        return None
    items = []
    for k, v in odds.items():
        try:
            items.append((int(k), float(v)))
        except Exception:
            continue
    items = [(k, v) for k, v in items if v > 1.0]
    if len(items) < 6:
        return None
    items.sort()
    try:
        probs, _ = shin([v for _, v in items])
    except Exception:
        return None
    pt = {k: p for (k, _), p in zip(items, probs)}
    key = lambda i, j: 7 if i + j >= 7 else i + j
    mass = {}
    for c, v in base.items():
        mass[key(*c)] = mass.get(key(*c), 0.0) + v
    mx = {}
    for c, v in base.items():
        b = key(*c)
        mx[c] = pt.get(b, 0.0) * (v / mass[b]) if mass.get(b) else 0.0
    s = sum(mx.values())
    if s <= 0:
        return None
    return {c: v / s for c, v in mx.items()}


JQ_TXT = ["0", "1", "2", "3", "4", "5", "6", "7+"]

# 净胜球 7 档：把比分矩阵按“赢几球”合并。单格比分概率通常只有 10% 左右，
# 净胜球档往往 30% 以上，更适合作为“比分方向”的主口径。
MARGIN_TXT = ["主胜3+", "主胜2球", "主胜1球", "平局", "客胜1球", "客胜2球", "客胜3+"]


def margin_index(h, a):
    """(主队进球, 客队进球) → 净胜球档下标。"""
    d = int(h) - int(a)
    if d >= 3:
        return 0
    if d == 2:
        return 1
    if d == 1:
        return 2
    if d == 0:
        return 3
    if d == -1:
        return 4
    if d == -2:
        return 5
    return 6


def margin_dist(mm):
    """比分矩阵 → 净胜球 7 档分布（主3+、主2、主1、平、客1、客2、客3+）。"""
    out = [0.0] * 7
    for (i, j), p in (mm or {}).items():
        out[margin_index(i, j)] += p
    return out


def dir_margin_pick(mm):
    """在预测胜负方向内挑最可能的净胜球档。

    返回 (档位下标, 该档概率, 方向内前二档概率合计)。
    不直接取全矩阵最大档，是因为“平局”会把 0-0、1-1、2-2 等多个比分合并，
    在某些场次会大于单个“主胜 1 球”档，和胜平负首选方向不一致。
    """
    md = margin_dist(mm)
    px = matrix_1x2(mm)
    d = px.index(max(px))
    cand = [0, 1, 2] if d == 0 else ([3] if d == 1 else [4, 5, 6])
    k = max(cand, key=lambda i: md[i])
    cover = sum(sorted((md[i] for i in cand), reverse=True)[:2])
    return k, md[k], cover


MARGIN_SHORT = ["主3+", "主2", "主1", "平", "客1", "客2", "客3+"]


def dir_margin_pair(mm):
    """预测胜负方向内最可能的两个净胜球档，返回 (档位列表, 合计概率, “主1/主2球”文本)。"""
    md = margin_dist(mm)
    px = matrix_1x2(mm)
    d = px.index(max(px))
    cand = [0, 1, 2] if d == 0 else ([3] if d == 1 else [4, 5, 6])
    top = sorted(cand, key=lambda i: -md[i])[:2]
    cover = sum(md[i] for i in top)
    if len(top) == 1:
        txt = MARGIN_TXT[top[0]]
    else:
        num = {0: 3, 1: 2, 2: 1, 3: 0, 4: -1, 5: -2, 6: -3}
        order = sorted(top, key=lambda i: abs(num[i]))
        side = "主胜" if d == 0 else ("客胜" if d == 2 else "")
        labs = ["3+" if num[i] in (3, -3) else str(abs(num[i])) for i in order]
        txt = ("%s%s球" % (side, "—".join(labs))) if len(labs) == 2 and labs == ["1", "2"] else (
            "%s%s球" % (side, "/".join(labs)))
    return top, cover, txt


def jq_index(n):
    """总进球数 → 竞彩 8 档下标（7 及以上归入 7+）。"""
    return 7 if n >= 7 else max(0, int(n))


def fmt_odds(x):
    """赔率显示：15.50 → 15.5，23.00 → 23。"""
    return ("%.2f" % float(x)).rstrip("0").rstrip(".")


def odds_probs(odds, order=None):
    """把一档赔率按给定顺序 Shin 去水，返回概率列表；不足 3 档或含无效值返回 None。"""
    if not odds:
        return None
    try:
        if isinstance(odds, dict):
            keys = list(order) if order else list(odds.keys())
            vals = [float(odds[k]) for k in keys]
        else:
            vals = [float(x) for x in odds]
    except Exception:
        return None
    if len(vals) < 3 or any(v <= 1.0 for v in vals):
        return None
    try:
        p, _ = shin(vals)
    except Exception:
        return None
    return p


def jq_dist(matrix):
    """比分矩阵 → 0—7+ 共 8 档总进球分布。"""
    out = [0.0] * 8
    for (i, j), v in (matrix or {}).items():
        out[jq_index(i + j)] += v
    return out


def bf_key_of(score, keys):
    """把实际比分落到比分赔率的 31 档键上；未单列的比分归入“胜/平/负其它”。"""
    if not score:
        return None
    if score in keys:
        return score
    sc = score_tuple(score)
    if not sc:
        return None
    gh, ga = sc
    tail = "胜其它" if gh > ga else ("平其它" if gh == ga else "负其它")
    return tail if tail in keys else None


def bf_model_probs(ens, keys):
    """把共识比分矩阵摊到 31 档比分键（含“其它”三档）。"""
    out, named = {}, {}
    for k in keys:
        if ":" in k:
            try:
                i, j = (int(x) for x in k.split(":"))
            except Exception:
                continue
            out[k] = ens.get((i, j), 0.0)
            named[(i, j)] = k
    for k in keys:
        if k not in TAIL_KIND:
            continue
        kind = TAIL_KIND[k]
        cond = (lambda i, j: i > j) if kind == 0 else (
            (lambda i, j: i == j) if kind == 1 else (lambda i, j: i < j))
        out[k] = sum(v for c, v in ens.items() if c not in named and cond(*c))
    return out


def model_matrices(m):
    """返回一场比赛的各模型比分矩阵：dc / poi / mkt / tgs / ens。

    旧记录缺市场赔率时，对应模型缺席，共识按可用模型重新归一化。
    """
    lam = m.get("lambda")
    if not lam or len(lam) != 2:
        return {}
    lh, la = float(lam[0]), float(lam[1])
    rho = m.get("rho")
    rho = RHO if rho is None else float(rho)
    mx = {"dc": dc_matrix(lh, la, rho)}
    lam0 = m.get("lambda_poi")
    if lam0 and len(lam0) == 2:
        mx["poi"] = dc_matrix(float(lam0[0]), float(lam0[1]), 0.0)
    else:
        mx["poi"] = dc_matrix(lh, la, 0.0)
    mkt = bf_matrix(m.get("bf_odds"), mx["dc"])
    if mkt:
        mx["mkt"] = mkt
    tgs = tgs_matrix(m.get("jq_odds"), mx["dc"])
    if tgs:
        mx["tgs"] = tgs
    avail = [k for k in ("dc", "poi", "mkt", "tgs") if k in mx]
    tw = sum(ENS_W[k] for k in avail)
    mx["ens"] = {c: sum(ENS_W[k] * mx[k][c] for k in avail) / tw for c in mx["dc"]}
    return mx


def model_pick(mm):
    """返回 (首选比分, 概率, 前三比分)。"""
    top = sorted(mm.items(), key=lambda kv: -kv[1])[:3]
    return "%d-%d" % top[0][0], top[0][1], ["%d-%d" % k for k, _ in top]


def same_pick(mm):
    """返回 (同向比分, 概率, 方向序号)。

    方向取该模型自己 1X2 里最大的一项，再在方向内挑概率最高的比分。
    单看整张矩阵的最高格常常是 1-1，但那个格子和预测胜负方向未必一致。
    """
    px = matrix_1x2(mm)
    idx = px.index(max(px))
    cond = (lambda k: k[0] > k[1]) if idx == 0 else (
        (lambda k: k[0] == k[1]) if idx == 1 else (lambda k: k[0] < k[1]))
    k, v = max(((c, p) for c, p in mm.items() if cond(c)), key=lambda kv: kv[1])
    return "%d-%d" % k, v, idx


def matrix_1x2(mm):
    h = sum(v for (i, j), v in mm.items() if i > j)
    d = sum(v for (i, j), v in mm.items() if i == j)
    a = sum(v for (i, j), v in mm.items() if i < j)
    return [h, d, a]


def model_stats(data):
    """各模型的比分校准。

    hits/exps 是“同向比分”（方向内最高格）的命中与期望；hit/exp 是全矩阵
    最高格的命中与期望，用来对比两种口径的差别。
    """
    out = {k: {"n": 0, "hit": 0, "exp": 0.0, "hits": 0, "exps": 0.0,
               "logp": 0.0, "brier": 0.0} for k in MODEL_KEYS}
    for m in data.get("matches", []):
        sc = split_score(m.get("score", ""))
        if not sc:
            continue
        mx = model_matrices(m)
        if not mx:
            continue
        actual = (sc[0], sc[1])
        for key, mm in mx.items():
            st = out[key]
            top = max(mm.items(), key=lambda kv: kv[1])[0]
            sk, sv, _ = same_pick(mm)
            st["n"] += 1
            st["exp"] += mm[top]
            st["hit"] += top == actual
            st["exps"] += sv
            st["hits"] += sk == "%d-%d" % actual
            st["logp"] += math.log(max(mm.get(actual, 1e-9), 1e-9))
            st["brier"] += sum((v - (1.0 if c == actual else 0.0)) ** 2 for c, v in mm.items())
    return out


def hf_probs(lh, la, share=HF_SHARE, rho=None):
    """半全场 9 种结果的概率，顺序同 HF_VALUES。

    上半场、下半场各自用一版 Dixon-Coles 矩阵（沿用全场的 ρ），
    再按 share 拆期望进球、组合成 3×3 共 9 档。
    两段各修一次低比分，是为了把“半场 0-0 / 1-1”从独立泊松的低估中拉回来；
    平局类组合（平/平、平/胜、平/负）因此会比纯泊松略高，更贴近市场九档赔率。

    最后做一次翻盘格校准：独立相乘默认“领先方下半场照常进攻”，
    会低估“领先方收着踢、落后方压上”的翻盘。按 191 场实测比例，
    把胜/负、负/胜两格分别乘 HF_COMEBACK 的系数再归一化；
    系数只取实测比例的七成，避免小样本（9 场、8 场）过拟合。
    """
    rho = RHO if rho is None else rho
    h1 = dc_matrix(lh * share, la * share, rho, MAXG)
    h2 = dc_matrix(lh * (1 - share), la * (1 - share), rho, MAXG)
    out = [0.0] * 9
    for (i, j), p1 in h1.items():
        base = (0 if i > j else 1 if i == j else 2) * 3
        if p1 <= 0:
            continue
        for (k, l), p2 in h2.items():
            if p2 <= 0:
                continue
            ft = 0 if i + k > j + l else (1 if i + k == j + l else 2)
            out[base + ft] += p1 * p2
    for code, k in HF_COMEBACK.items():
        out[HF_VALUES.index(code)] *= k
    s = sum(out)
    return [x / s for x in out] if s > 0 else out


def hf_label(code):
    """“3-1” → “胜/平”。"""
    code = code or ""
    if len(code) < 3:
        return "—"
    return "%s/%s" % (HF_TXT.get(code[0], "?"), HF_TXT.get(code[2], "?"))


def hf_stats(data):
    """半全场校准：返回 {n, hit, top3, exp, logp, brier}。旧记录缺 hf 时按 lambda 现算。"""
    out = {"n": 0, "hit": 0, "top3": 0, "exp": 0.0, "logp": 0.0, "brier": 0.0}
    for m in data.get("matches", []):
        res = m.get("hf_result")
        if res not in HF_VALUES:
            continue
        hf = m.get("hf")
        if (not hf or len(hf) != 9) and m.get("lambda"):
            hf = hf_probs(m["lambda"][0], m["lambda"][1], rho=m.get("rho", RHO))
        if not hf or len(hf) != 9:
            continue
        ai = HF_VALUES.index(res)
        order = sorted(range(9), key=lambda i: -hf[i])
        out["n"] += 1
        out["exp"] += hf[order[0]]
        out["logp"] += math.log(max(hf[ai], 1e-9))
        out["brier"] += sum((hf[i] - (1.0 if i == ai else 0.0)) ** 2 for i in range(9))
        out["hit"] += order[0] == ai
        out["top3"] += ai in order[:3]
    return out


def hf_tally(data):
    """返回 (已核对半全场场次, 命中场次)。"""
    st = hf_stats(data)
    return st["n"], st["hit"]


def probs_h(m, h=0):
    """给定让球 h，返回 (主胜, 走盘/平, 客胜) 概率。h 是加在主队进球上的数。"""
    win = draw = lose = 0.0
    for (i, j), v in m.items():
        x = i + h - j
        if x > 0:
            win += v
        elif x == 0:
            draw += v
        else:
            lose += v
    return win, draw, lose


def _nelder(f, x0, step, iters=400, tol=1e-13):
    """二维 Nelder-Mead，纯 Python，用于精确求解 (λ主, λ客)。"""
    pts = [list(x0), [x0[0] + step[0], x0[1]], [x0[0], x0[1] + step[1]]]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(3), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if vals[2] - vals[0] < tol:
            break
        cx = [(pts[0][0] + pts[1][0]) / 2.0, (pts[0][1] + pts[1][1]) / 2.0]
        xr = [2 * cx[0] - pts[2][0], 2 * cx[1] - pts[2][1]]
        fr = f(xr)
        if fr < vals[0]:
            xe = [3 * cx[0] - 2 * pts[2][0], 3 * cx[1] - 2 * pts[2][1]]
            fe = f(xe)
            pts[2], vals[2] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[1]:
            pts[2], vals[2] = xr, fr
        else:
            xc = [(cx[0] + pts[2][0]) / 2.0, (cx[1] + pts[2][1]) / 2.0]
            fc = f(xc)
            if fc < vals[2]:
                pts[2], vals[2] = xc, fc
            else:
                for i in (1, 2):
                    pts[i] = [(pts[i][0] + pts[0][0]) / 2.0, (pts[i][1] + pts[0][1]) / 2.0]
                    vals[i] = f(pts[i])
    i = min(range(3), key=lambda k: vals[k])
    return pts[i]


def fit_lam(p1, rho=0.0, x0=None):
    """把市场胜平负概率反解成 (λ主, λ客)，返回 (误差, λ主, λ客)。

    拟合用与后续预测同一个比分矩阵（含 ρ 修正），保证 1X2 不被二次扭曲。
    x0 用于热启动（ρ 扫描时沿用上一点的解），默认不改变原有行为。
    """
    def sse(x):
        lh = max(x[0], 0.02)
        la = max(x[1], 0.02)
        h, d, a = matrix_1x2(dc_matrix(lh, la, rho))
        return (h - p1[0]) ** 2 + (d - p1[1]) ** 2 + (a - p1[2]) ** 2

    start = tuple(x0) if x0 else (1.3, 1.1)
    lh, la = _nelder(sse, start, (0.3, 0.28) if not x0 else (0.08, 0.08))
    lh, la = max(lh, 0.02), max(la, 0.02)
    return sse((lh, la)), lh, la


MODEL = "dc"
DEFAULT_RHO = -0.10
RHO = DEFAULT_RHO           # 全局低比分相关系数，由 refresh_rho() 读 数据/模型参数.json 更新


RHO_FILE = DATA_DIR / "模型参数.json"
RHO_STEP = 0.01
RHO_LO, RHO_HI = -0.20, 0.02
RHO_CLIP = (-0.18, 0.0)


def rho_shrink(n):
    """样本太少时向 0 收缩，避免几场比赛把 ρ 拉偏。"""
    if n >= 200:
        return 1.0
    if n >= 80:
        return 0.75
    return 0.5


def rho_joint_curve(samples, step=RHO_STEP, lo=RHO_LO, hi=RHO_HI):
    """ρ 与 λ 一起拟合的网格似然，同时给出 DC 单源与最终集成两套比分格对数似然。

    只固定 λ 去扫 ρ 会低估 ρ 的作用——λ 是按该 ρ 的矩阵贴合市场胜平负解出来的。
    从 ρ 大到小逐点热启动（沿用上一格 λ），避免优化器停在不同位置把曲线抖成锯齿。
    集成列取实际比分那一格的概率，不取胜平负分组：分组概率被 λ 的反解钉在市场赔率上，
    换 ρ 几乎不动（实测整条曲线只差 3.4），用它选参等于在噪声里挑数。
    """
    base = []
    for m in samples:
        sc = split_score(m.get("score", ""))
        odds = m.get("odds")
        if not sc or not odds:
            continue
        try:
            p1, _ = shin(odds)
            _, l0h, l0a = fit_lam(p1, 0.0)      # 泊松源的 λ 与 ρ 无关，先算一次
        except Exception:
            continue
        base.append((m, p1, sc, (l0h, l0a)))
    curve = {}
    lams = [None] * len(base)
    r = hi
    while r >= lo - 1e-9:
        rr = round(r, 3)
        s_dc = 0.0
        s_ens = 0.0
        for i, (m, p1, sc, lam0) in enumerate(base):
            _, lh, la = fit_lam(p1, rr, x0=lams[i])
            lams[i] = (lh, la)
            s_dc += math.log(max(dc_matrix(lh, la, rr, MAXG).get(sc, 1e-9), 1e-9))
            mt = dict(m)
            mt["lambda"] = [lh, la]
            mt["lambda_poi"] = [round(lam0[0], 3), round(lam0[1], 3)]
            mt["rho"] = rr
            ens = model_matrices(mt).get("ens")
            s_ens += math.log(max(ens.get(sc, 1e-12), 1e-12)) if ens else 0.0
        curve[rr] = (s_dc, s_ens, len(base))
        r = round(r - step, 3)
    return [(k, curve[k][0], curve[k][1], curve[k][2]) for k in sorted(curve)]


def _peak_ci(curve, idx, bi):
    """围绕最优点取连续段作为近似 95% 区间（Δ 对数似然 1.92）。"""
    best = curve[bi][idx]
    j = bi
    while j > 0 and curve[j - 1][idx] >= best - 1.92:
        j -= 1
    k = bi
    while k < len(curve) - 1 and curve[k + 1][idx] >= best - 1.92:
        k += 1
    return (curve[j][0], curve[k][0])


def rho_from_curve(curve):
    """采用值按最终交付的集成分布选，DC 单源 MLE 一并返回作参考。"""
    best = max(c[2] for c in curve)
    bi = next(i for i, c in enumerate(curve) if c[2] == best)
    hat = curve[bi][0]
    ci = _peak_ci(curve, 2, bi)
    n = curve[0][3]
    dcb = max(range(len(curve)), key=lambda i: curve[i][1])
    hat_dc = curve[dcb][0]
    ci_dc = _peak_ci(curve, 1, dcb)
    lo, hi = RHO_CLIP
    return min(hi, max(lo, rho_shrink(n) * hat)), hat, ci, n, hat_dc, ci_dc


def refresh_rho(samples=None):
    """用封存+回溯样本重算 ρ 并落盘，供 prepare_model 读取。"""
    global RHO
    if samples is None:
        samples = load_bt_samples()
    curve = rho_joint_curve(samples)
    if len(curve) < 5 or not curve[0][3]:
        log("样本不足，ρ 保持默认 %.2f" % DEFAULT_RHO)
        return None
    rho, hat, ci, n, hat_dc, ci_dc = rho_from_curve(curve)
    rec = {"at": now_str(), "rho": round(rho, 3), "hat": round(hat, 3),
           "ci": [round(ci[0], 3), round(ci[1], 3)],
           "hat_dc": round(hat_dc, 3), "ci_dc": [round(ci_dc[0], 3), round(ci_dc[1], 3)],
           "n": n, "shrink": rho_shrink(n),
           "curve": [[c[0], round(c[1], 2), round(c[2], 2)] for c in curve]}
    ensure_dirs()
    tmp = RHO_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    Path.replace(tmp, RHO_FILE)
    RHO = rho
    log("ρ 校准：%d 场，集成分布最优 %+.2f（95%% 区间 [%+.2f, %+.2f]），DC 单源 MLE %+.2f（[%+.2f, %+.2f]），收缩 %.2f → 采用 %+.2f" % (
        n, hat, ci[0], ci[1], hat_dc, ci_dc[0], ci_dc[1], rec["shrink"], rho))
    return rec


def load_rho_file():
    try:
        rec = json.loads(RHO_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(rec, dict) or rec.get("rho") is None:
        return None
    return rec


def predict_match(mt):
    p1, s = shin(mt["odds"])
    err, lh, la = fit_lam(p1, RHO)
    _, lh0, la0 = fit_lam(p1, 0.0)
    mt["lambda"] = [round(lh, 3), round(la, 3)]
    mt["lambda_poi"] = [round(lh0, 3), round(la0, 3)]
    mt["rho"] = round(RHO, 3)
    mx = model_matrices(mt)
    ens = mx.get("ens") or dc_matrix(lh, la, RHO, MAXG)
    px = matrix_1x2(ens)
    idx = px.index(max(px))
    name = ["主胜", "平局", "客胜"][idx]
    pick, pick_p, top3 = model_pick(ens)
    top = sorted(ens.items(), key=lambda kv: -kv[1])[:6]
    same_s, same_p, _ = same_pick(ens)
    md = margin_dist(ens)
    mi, mp, m2 = dir_margin_pick(ens)
    mpair, mcover, mtxt = dir_margin_pair(ens)
    hf = hf_probs(lh, la, rho=RHO)
    hi = hf.index(max(hf))
    jq_model = jq_dist(ens)
    ji = max(range(8), key=lambda k: jq_model[k])
    models = {}
    for key, mm in mx.items():
        mk, mp, mt3 = model_pick(mm)
        sk, sp, _ = same_pick(mm)
        models[key] = {"pick": mk, "p": round(mp, 4), "top3": mt3,
                       "same": sk, "same_p": round(sp, 4),
                       "probs": [round(x, 4) for x in matrix_1x2(mm)]}
    mt.update({
        "hf": [round(x, 4) for x in hf],
        "hf_pick": HF_VALUES[hi],
        "hf_pick_p": round(hf[hi], 4),
        "hf_level": ("high" if hf[hi] >= HF_GATE else
                     "mid" if hf[hi] >= HF_MID else "low"),
        "probs": [round(x, 4) for x in px],
        "model": "ens",
        "models": models,
        "pick": name,
        "score": mt.get("score", ""),
        "pick_score": pick,
        "pick_score_p": round(pick_p, 4),
        "same_score": same_s,
        "same_score_p": round(same_p, 4),
        "margin": [round(x, 4) for x in md],
        "margin_pick": mi,
        "margin_pick_p": round(mp, 4),
        "margin_top2_cover": round(m2, 4),
        "margin_pair": mtxt,
        "margin_pair_p": round(mcover, 4),
        "margin_pair_idx": mpair,
        "top3_cover": round(sum(v for _, v in top[:3]), 4),
        "top": [[f"{k[0]}-{k[1]}", round(v, 4)] for k, v in top],
        "top_all": f"{top[0][0][0]}-{top[0][0][1]}",
        "jq_model": [round(x, 4) for x in jq_model],
        "jq_pick": ji,
        "jq_pick_p": round(jq_model[ji], 4),
        "return_rate": round(1.0 / s, 4),
        "fit_err": round(err, 6),
    })
    bq = mt.get("bq_odds")
    if bq and len(bq) == 9 and all(isinstance(x, (int, float)) and x > 1.0 for x in bq):
        q, _ = shin(bq)
        mt["bq_odds"] = [round(float(x), 2) for x in bq]
        mt["bq_probs"] = [round(x, 4) for x in q]
    bf = mt.get("bf_odds")
    if isinstance(bf, dict):
        keys = [k for k in bf if isinstance(bf[k], (int, float)) and bf[k] > 1.0]
        if len(keys) >= 20:
            try:
                q, _ = shin([float(bf[k]) for k in keys])
                mt["bf_odds"] = {k: round(float(bf[k]), 2) for k in keys}
                mt["bf_probs"] = {k: round(p, 4) for k, p in zip(keys, q)}
            except Exception:
                pass
    jq = mt.get("jq_odds")
    if isinstance(jq, dict):
        try:
            pairs = sorted((int(k), float(v)) for k, v in jq.items())
            pairs = [(k, v) for k, v in pairs if v > 1.0]
            if len(pairs) >= 6:
                q, _ = shin([v for _, v in pairs])
                mt["jq_odds"] = {str(k): round(v, 2) for k, v in pairs}
                mt["jq_probs"] = [round(x, 4) for x in q]
        except Exception:
            pass
    sync_result_fields(mt)
    return mt


# ---------------------------------------------------------------- 存取
def path_of(day):
    return DATA_DIR / ("%s.json" % day)


def load_day(day):
    p = path_of(day)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_day(data):
    ensure_dirs()
    p = path_of(data["date"])
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    write_csv(data)


def write_csv(data):
    hdr = ["场次", "联赛", "对阵", "开球", "赔率 主/平/客", "去水概率 主/平/客%",
           "预测胜负", "同向比分", "同向概率", "全场最高", "最高概率", "TOP3 比分",
           "DC 同向", "泊松 同向", "市场同向", "总进球同向", "共识同向",
           "实际比分", "是否命中",
           "半全场首选", "模型概率", "市场概率", "半全场次选", "半全场三选",
           "半全场实际", "半全场命中", "半全场前三命中",
           "比分赔率 31档", "比分市场概率前三",
           "总进球赔率 0-7+", "总进球去水%", "总进球模型%", "总进球首选",
           "总进球实际", "总进球命中",
           "半全场赔率 9档", "半全场去水%"]
    rows = []
    for m in data["matches"]:
        if not m.get("probs"):
            continue
        hf = m.get("hf")
        bq = m.get("bq_probs")
        mdl = m.get("models") or {}
        if hf and len(hf) == 9:
            order = sorted(range(9), key=lambda i: -hf[i])
            hi = order[0]
            hp = "%.1f%%" % (hf[hi] * 100)
            mp = "%.1f%%" % (bq[hi] * 100) if bq and len(bq) == 9 else ""
            h2 = "%s %.1f%%" % (hf_label(HF_VALUES[order[1]]), hf[order[1]] * 100)
            h3 = "%s %.1f%%" % (hf_label(HF_VALUES[order[2]]), hf[order[2]] * 100)
            hres = m.get("hf_result", "")
            hhit = ("中" if hres == HF_VALUES[hi] else "未中") if hres else ""
            ht3 = ("中" if hres in [HF_VALUES[k] for k in order[:3]] else "未中") if hres else ""
        else:
            hp = mp = h2 = h3 = hres = hhit = ht3 = ""
        same = m.get("same_score") or m["pick_score"]
        same_p = m.get("same_score_p", m["pick_score_p"])
        bfo = m.get("bf_odds") or {}
        bfp = m.get("bf_probs") or {}
        bf_txt = ";".join("%s=%.2f" % (k, v) for k, v in bfo.items())
        bf_top = "、".join(
            "%s %.1f%%" % (k, bfp[k] * 100)
            for k in sorted(bfp, key=lambda k: -bfp[k])[:3]) if bfp else ""
        jqo = m.get("jq_odds") or {}
        jqp = m.get("jq_probs") or []
        jqm = m.get("jq_model") or []
        jq_txt = ";".join("%s=%s" % (k, fmt_odds(v)) for k, v in jqo.items())
        jq_mkt = "/".join("%.1f" % (x * 100) for x in jqp) if len(jqp) == 8 else ""
        jq_mdl = "/".join("%.1f" % (x * 100) for x in jqm) if len(jqm) == 8 else ""
        jq_pick = m.get("jq_pick")
        jq_pick_txt = ("%s %.1f%%" % (JQ_TXT[jq_pick], jqm[jq_pick] * 100)
                       if jq_pick is not None and len(jqm) == 8 else "")
        jq_act = m.get("jq_result")
        jq_hit = ("中" if jq_act == jq_pick else "未中") if jq_act is not None else ""
        bqo = m.get("bq_odds") or []
        hf_odds = ";".join("%s=%s" % (hf_label(c), fmt_odds(o))
                           for c, o in zip(HF_VALUES, bqo) if o) if len(bqo) == 9 else ""
        hf_prob = "/".join("%.1f" % (x * 100) for x in bq) if bq and len(bq) == 9 else ""
        rows.append([
            m["no"], m["league"], "%s-%s" % (m["home"], m["away"]), m.get("kickoff", ""),
            "/".join("%.2f" % o for o in m["odds"]),
            "/".join("%.1f" % (x * 100) for x in m["probs"]),
            "%s %.0f%%" % (m["pick"], max(m["probs"]) * 100),
            same, "%.1f%%" % (same_p * 100),
            m["pick_score"], "%.1f%%" % (m["pick_score_p"] * 100),
            "、".join("%s %.1f%%" % (s, v * 100) for s, v in m["top"][:3]),
            (mdl.get("dc") or {}).get("same") or (mdl.get("dc") or {}).get("pick", ""),
            (mdl.get("poi") or {}).get("same") or (mdl.get("poi") or {}).get("pick", ""),
            (mdl.get("mkt") or {}).get("same") or (mdl.get("mkt") or {}).get("pick", ""),
            (mdl.get("tgs") or {}).get("same") or (mdl.get("tgs") or {}).get("pick", ""),
            (mdl.get("ens") or {}).get("pick", ""),
            m.get("score", ""), hit_text(m),
            hf_label(HF_VALUES[hi]) if hf and len(hf) == 9 else "", hp, mp, h2, h3,
            hf_label(m.get("hf_result")), hhit, ht3,
            bf_txt, bf_top,
            jq_txt, jq_mkt, jq_mdl, jq_pick_txt,
            "" if jq_act is None else JQ_TXT[jq_act], jq_hit,
            hf_odds, hf_prob,
        ])
    with open(DATA_DIR / ("比分记录_%s.csv" % data["date"]), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        w.writerows(rows)


def now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return dt.date.today().strftime("%Y-%m-%d")


def ensure_day(day, allow_late=False):
    """有记录就沿用（不覆盖赔率，保证赛前封存）；没有就抓取并预测。"""
    data = load_day(day)
    if data:
        return data
    rows = fetch_day(day)
    now = dt.datetime.now()
    kept, skipped = [], 0
    for r in rows:
        try:
            ko = dt.datetime.strptime(r["kickoff"], "%Y-%m-%d %H:%M")
        except Exception:
            ko = now
        if ko <= now and not allow_late:
            skipped += 1
            continue
        kept.append(predict_match(r))
        log("  预测 %s %s-%s：%s %s（%.1f%%）" % (
            r["no"], r["home"], r["away"], r["pick"], r["pick_score"], r["pick_score_p"] * 100))
    if skipped:
        log("跳过已开赛的 %d 场（不做赛后补预测）" % skipped)
    if not kept:
        raise RuntimeError("全部比赛都已开赛，无法做赛前预测")
    data = {"date": day, "sealed_at": now_str(), "source": SOURCE,
            "late": bool(allow_late), "matches": kept}
    save_day(data)
    return data


def top_up_day(data):
    """开盘后再补：把后来才挂出的、尚未开赛的场次追加进当天记录（不改动已封存的赔率）。"""
    try:
        rows = fetch_day(data["date"], quiet=True)
    except Exception as e:
        log("补抓 %s 失败：%s" % (data["date"], e))
        return 0
    have = {m["no"] for m in data["matches"]}
    now = dt.datetime.now()
    added = 0
    for r in rows:
        if r["no"] in have:
            continue
        try:
            ko = dt.datetime.strptime(r["kickoff"], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if ko <= now:
            continue
        data["matches"].append(predict_match(r))
        added += 1
    if added:
        data["matches"].sort(key=lambda x: x.get("kickoff", ""))
        save_day(data)
        log("补入 %s 新增未开赛场次 %d 场" % (data["date"], added))
    return added


# ---------------------------------------------------------------- 临场复核
RC_CSS = """
:root{--ink:#1e293b;--mute:#64748b;--line:#e2e8f0;--accent:#1f3b63}
*{box-sizing:border-box}
body{margin:0;background:#f5f7fa;color:var(--ink);
  font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1280px;margin:30px auto 60px;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:20px}
h1{font-size:23px;margin:0}
.sub{color:var(--mute);font-size:13px;margin-top:6px}
.stamp{text-align:right;color:var(--mute);font-size:13px;line-height:1.55}
.stamp b{color:var(--accent);font-size:15px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));gap:10px;margin-bottom:20px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card span{display:block;font-size:12px;color:var(--mute)}
.card b{font-size:20px;color:var(--accent);font-variant-numeric:tabular-nums}
.card em{font-style:normal;font-size:12px;color:#94a3b8;margin-left:4px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;
  border:1px solid var(--line);border-radius:12px;overflow:hidden;table-layout:fixed}
th{background:#eef2f7;color:#334155;font-size:12.5px;font-weight:600;text-align:left;
  padding:11px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 8px;border-bottom:1px solid #f1f5f9;vertical-align:middle;font-size:13px}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fbfdff}
.no{font-weight:700;color:var(--accent);white-space:nowrap}
.teams{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.teams span{color:#cbd5e1;margin:0 5px}
.kick{white-space:nowrap;color:#64748b;font-size:12.5px}
.old{color:#94a3b8}
.chg{color:#b45309;font-weight:700}
.tag{display:inline-block;padding:0 6px;border-radius:5px;background:#fff7e6;
  color:#b45309;font-size:11.5px;margin-top:2px}
.delta{font-size:12px;font-variant-numeric:tabular-nums}
.up{color:#15803d}.down{color:#b91c1c}.flat{color:#94a3b8}
.odds{color:#94a3b8;font-size:11.5px;margin-top:2px}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11.5px;
  background:#eef2f7;color:#475569;margin-left:4px}
.note{background:#fff;border:1px solid var(--line);border-radius:12px;
  padding:14px 18px;margin-top:18px;font-size:13px;color:#475569}
.note b{color:#1e293b}
h2{font-size:16px;margin:26px 0 8px}
.hint{color:var(--mute);font-size:12.5px;margin:0 0 10px}
footer{margin-top:26px;color:#94a3b8;font-size:12px;line-height:1.8}
a{color:#1f3b63}
"""


def recheck_path(day):
    return RECHECK_DIR / ("%s.json" % day)


def recheck_report_of(day):
    p = REPORT_DIR / ("临场复核_%s.html" % day)
    return p if p.exists() else None


def load_recheck(day):
    p = recheck_path(day)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _rc_pct(v):
    return "%.0f%%" % (float(v or 0.0) * 100)


def _rc_delta(v):
    return "%+.1fpt" % (float(v or 0.0) * 100)


def _rc_cls(v):
    return "up" if v > 0.0005 else ("down" if v < -0.0005 else "flat")


def _pick_snapshot(m):
    """把一场比赛的预测压缩成可对比的字段（封存记录缺新字段时现场补算）。"""
    ens = None
    try:
        ens = model_matrices(m).get("ens")
    except Exception:
        ens = None
    probs = m.get("probs") or [0.0, 0.0, 0.0]
    same = m.get("same_score") or m.get("pick_score") or ""
    same_p = m.get("same_score_p", m.get("pick_score_p", 0.0))
    margin = m.get("margin_pair")
    margin_p = m.get("margin_pair_p", 0.0)
    if (not margin) and ens:
        try:
            _, mp, mt = dir_margin_pair(ens)
            margin, margin_p = mt, mp
        except Exception:
            margin, margin_p = "—", 0.0
    cover = m.get("top3_cover")
    if cover is None:
        cover = sum(p for _, p in (m.get("top") or [])[:3])
    jq = m.get("jq_pick")
    jq = int(jq) if isinstance(jq, (int, float)) else -1
    return {
        "pick": m.get("pick", ""),
        "probs": [round(float(x), 4) for x in probs],
        "score": same,
        "score_p": round(float(same_p or 0.0), 4),
        "margin": margin or "—",
        "margin_p": round(float(margin_p or 0.0), 4),
        "jq": jq,
        "jq_p": round(float(m.get("jq_pick_p", 0.0)), 4),
        "hf": m.get("hf_pick", ""),
        "hf_p": round(float(m.get("hf_pick_p", 0.0)), 4),
        "cover": round(float(cover or 0.0), 4),
        "rr": m.get("return_rate"),
    }


def run_recheck(day=None, quiet=False):
    """用临场最新赔率重算未开赛场次，只写独立复核快照，不改赛前封存记录。"""
    ensure_dirs()
    day = day or today_str()
    data = load_day(day)
    if not data:
        raise RuntimeError("%s 还没有赛前封存记录，先跑 --fetch 或 --daily" % day)
    log("=== 临场复核 %s ===" % day)
    prepare_model()
    rows = fetch_day(day, quiet=quiet)
    now = dt.datetime.now()
    byno = {r["no"]: r for r in rows}
    sealed = {m["no"]: m for m in data["matches"] if m.get("probs")}
    items, started, missing = [], 0, 0
    for no, m in sealed.items():
        try:
            ko = dt.datetime.strptime(m["kickoff"], "%Y-%m-%d %H:%M")
        except Exception:
            ko = None
        if ko and ko <= now:
            started += 1
            continue
        r = byno.get(no)
        if not r:
            missing += 1
            continue
        r["score"] = ""
        live = predict_match(dict(r))
        old = _pick_snapshot(m)
        new = _pick_snapshot(live)
        delta = [round(new["probs"][i] - old["probs"][i], 4) for i in range(3)]
        k = max(range(3), key=lambda i: abs(delta[i]))
        changed = {
            "pick": old["pick"] != new["pick"],
            "score": old["score"] != new["score"],
            "margin": old["margin"] != new["margin"],
            "jq": old["jq"] != new["jq"],
            "hf": old["hf"] != new["hf"],
        }
        items.append({
            "no": no, "league": m.get("league", ""),
            "home": m.get("home", ""), "away": m.get("away", ""),
            "kickoff": m.get("kickoff", ""),
            "sealed": old, "live": new, "changed": changed,
            "delta": delta,
            "max_move": {"idx": k, "d": delta[k]},
            "odds": {"old": [round(float(x), 2) for x in (m.get("odds") or [])],
                     "new": [round(float(x), 2) for x in (r.get("odds") or [])]},
        })
    items.sort(key=lambda x: x["kickoff"])
    rec = {"date": day, "at": now_str(), "pending": len(items),
           "started": started, "missing": missing, "matches": items}
    RECHECK_DIR.mkdir(parents=True, exist_ok=True)
    p = recheck_path(day)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    rp = render_recheck(rec)
    log("临场复核完成：未开赛 %d 场（已开赛跳过 %d，未抓到 %d）；胜平负改选 %d 场 → %s" % (
        len(items), started, missing,
        sum(1 for x in items if x["changed"]["pick"]), rp))
    return rec, rp


def render_recheck(rec):
    day = rec["date"]
    W = ["主胜", "平局", "客胜"]
    n = rec["pending"]
    cnt = {k: sum(1 for x in rec["matches"] if x["changed"][k])
           for k in ("pick", "score", "margin", "jq", "hf")}
    cards = f"""
  <div class="card"><span>复核时间</span><b>{esc(rec['at'][11:16])}</b></div>
  <div class="card"><span>未开赛</span><b>{n}</b><em>场</em></div>
  <div class="card"><span>已开赛跳过</span><b>{rec['started']}</b><em>场</em></div>
  <div class="card"><span>胜平负改选</span><b>{cnt['pick']}</b><em>场</em></div>
  <div class="card"><span>比分改选</span><b>{cnt['score']}</b><em>场</em></div>
  <div class="card"><span>净胜球前二改选</span><b>{cnt['margin']}</b><em>场</em></div>
  <div class="card"><span>总进球改选</span><b>{cnt['jq']}</b><em>场</em></div>
  <div class="card"><span>半全场改选</span><b>{cnt['hf']}</b><em>场</em></div>"""
    body = []
    for x in rec["matches"]:
        o, l, ch, od = x["sealed"], x["live"], x["changed"], x["odds"]
        oi = W.index(o["pick"]) if o["pick"] in W else 0
        ni = W.index(l["pick"]) if l["pick"] in W else 0

        def pair(old_txt, old_p, new_txt, new_p, changed):
            if changed:
                return ('<b class="chg">%s %s</b> → <span class="chg">%s %s</span>'
                        '<br><span class="tag">改选</span>' % (
                            esc(old_txt), _rc_pct(old_p), esc(new_txt), _rc_pct(new_p)))
            d = (new_p or 0) - (old_p or 0)
            return ('%s <b>%s</b> <span class="delta %s">%s</span>' % (
                esc(new_txt), _rc_pct(new_p), _rc_cls(d), _rc_delta(d)))

        if ch["pick"]:
            wdl = ('<b class="chg">%s %s</b> → <span class="chg">%s %s</span>'
                   '<br><span class="tag">改选</span>' % (
                       esc(o["pick"]), _rc_pct(o["probs"][oi]),
                       esc(l["pick"]), _rc_pct(l["probs"][ni])))
        else:
            d = l["probs"][ni] - o["probs"][oi]
            wdl = ('%s <span class="old">%s</span> → <b>%s</b> '
                   '<span class="delta %s">%s</span>' % (
                       esc(l["pick"]), _rc_pct(o["probs"][oi]),
                       _rc_pct(l["probs"][ni]), _rc_cls(d), _rc_delta(d)))
        odds_txt = ""
        if len(od.get("old") or []) == 3 and len(od.get("new") or []) == 3:
            odds_txt = '<div class="odds">%.2f/%.2f/%.2f → %.2f/%.2f/%.2f</div>' % (
                od["old"][0], od["old"][1], od["old"][2],
                od["new"][0], od["new"][1], od["new"][2])
        k = x["max_move"]["idx"]
        mv = ('<span class="delta %s">%s %s</span>' % (
            _rc_cls(x["max_move"]["d"]), esc(W[k]), _rc_delta(x["max_move"]["d"])))
        jq_old = JQ_TXT[o["jq"]] if 0 <= o["jq"] < len(JQ_TXT) else "—"
        jq_new = JQ_TXT[l["jq"]] if 0 <= l["jq"] < len(JQ_TXT) else "—"
        body.append("""
<tr>
  <td class="no">%s</td>
  <td class="teams"><b>%s</b><span>vs</span>%s</td>
  <td class="kick">%s</td>
  <td>%s%s</td>
  <td>%s</td>
  <td>%s</td>
  <td>%s</td>
  <td>%s</td>
  <td>%s</td>
</tr>""" % (
            esc(x["no"]), esc(x["home"]), esc(x["away"]), esc(x["kickoff"][11:]),
            wdl, odds_txt,
            pair(o["score"], o["score_p"], l["score"], l["score_p"], ch["score"]),
            pair(o["margin"], o["margin_p"], l["margin"], l["margin_p"], ch["margin"]),
            pair(jq_old, o["jq_p"], jq_new, l["jq_p"], ch["jq"]),
            pair(hf_label(o["hf"]), o["hf_p"], hf_label(l["hf"]), l["hf_p"], ch["hf"]),
            mv))
    movers = sorted(rec["matches"], key=lambda x: -abs(x["max_move"]["d"]))[:6]
    mrows = []
    for x in movers:
        d = x["delta"]
        mrows.append("""
<tr><td class="no">%s</td><td class="teams"><b>%s</b><span>vs</span>%s</td>
<td class="kick">%s</td>
<td class="delta %s">%s</td><td class="delta %s">%s</td><td class="delta %s">%s</td>
<td class="kick">%.2f/%.2f/%.2f → %.2f/%.2f/%.2f</td></tr>""" % (
            esc(x["no"]), esc(x["home"]), esc(x["away"]), esc(x["kickoff"][11:]),
            _rc_cls(d[0]), _rc_delta(d[0]), _rc_cls(d[1]), _rc_delta(d[1]),
            _rc_cls(d[2]), _rc_delta(d[2]),
            x["odds"]["old"][0] if len(x["odds"]["old"]) == 3 else 0,
            x["odds"]["old"][1] if len(x["odds"]["old"]) == 3 else 0,
            x["odds"]["old"][2] if len(x["odds"]["old"]) == 3 else 0,
            x["odds"]["new"][0] if len(x["odds"]["new"]) == 3 else 0,
            x["odds"]["new"][1] if len(x["odds"]["new"]) == 3 else 0,
            x["odds"]["new"][2] if len(x["odds"]["new"]) == 3 else 0))
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>临场复核 · {day}</title><style>{RC_CSS}</style></head>
<body><div class="wrap">
<header>
  <div><h1>临场复核 · {day}</h1>
    <div class="sub">用最新赔率重算未开赛场次 · 与赛前封存逐项对照 · 不改动校准记录</div></div>
  <div class="stamp"><b>{esc(day)}</b><br>复核 {esc(rec['at'][11:16])}<br>未开赛 {n} 场</div>
</header>
<div class="cards">{cards}</div>
<p class="hint">胜平负保留“封存 → 临场”两列对照；其余玩法未改选时直接给临场概率与变动，改选时标橙。赔率顺序为主胜/平局/客胜，左侧为封存、右侧为临场。</p>
<table>
<colgroup><col style="width:56px"><col style="width:176px"><col style="width:64px">
<col style="width:190px"><col style="width:132px"><col style="width:132px">
<col style="width:108px"><col style="width:122px"><col style="width:106px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th><th>开球</th><th>胜平负</th><th>比分</th>
<th>净胜球前二</th><th>总进球</th><th>半全场</th><th>最大变动</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>

<h2>胜平负概率变动最大的 6 场</h2>
<p class="hint">变动 = 临场概率 − 赛前封存概率，单位百分点。三项合计为 0；幅度越大，说明这段时间资金或信息对这场比赛的定价改变越多。</p>
<table>
<colgroup><col style="width:56px"><col style="width:220px"><col style="width:64px">
<col style="width:90px"><col style="width:90px"><col style="width:90px"><col style="width:260px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th><th>开球</th><th>主胜</th><th>平局</th><th>客胜</th>
<th>胜平负赔率（封存 → 临场）</th></tr></thead>
<tbody>{''.join(mrows)}</tbody></table>

<div class="note">
<b>口径：</b>临场复核只重算尚未开赛的场次，已开赛场次自动跳过；复核快照单独保存在“数据/复核/”，赛前封存记录和赛后校准口径都不改，避免用临场信息回头污染成绩。<br>
<b>信息范围：</b>复核反映的是最新赔率变化（资金与临场信息的综合结果），不包含伤停、首发等文字情报；赔率没动不代表没有新情况。<br>
<b>读法：</b>“改选”只说明概率第一档换了，不代表新选择一定更好；比分单格通常仍只有 8%—12%，继续结合“前三覆盖”和净胜球前二看。
</div>
<footer>生成于 {esc(rec['at'])} · 数据来源 500.com 竞彩 · 只做概率记录，不构成投注建议。　
<a href="比分日报_{day}.html">当日日报 →</a></footer>
</div></body></html>"""
    p = REPORT_DIR / ("临场复核_%s.html" % day)
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)
    return p


def recheck_banner(day):
    rec = load_recheck(day)
    p = REPORT_DIR / ("临场复核_%s.html" % day)
    if not rec or not p.exists():
        return ""
    cnt = {k: sum(1 for x in rec["matches"] if x["changed"][k])
           for k in ("pick", "score", "margin", "jq", "hf")}
    return f"""<div class="rcb">
<b>临场复核 {esc(rec['at'][11:16])}</b> · 未开赛 {rec['pending']} 场 ·
胜平负改选 <b>{cnt['pick']}</b> · 比分改选 <b>{cnt['score']}</b> ·
净胜球前二改选 <b>{cnt['margin']}</b> · 半全场改选 <b>{cnt['hf']}</b>
<a href="临场复核_{day}.html">查看复核页 →</a></div>"""


def refresh_results(days=10):
    """回抓最近若干天的赛果，写回未结算的比赛；返回有更新的日期列表。"""
    changed = []
    for i in range(days):
        d = (dt.date.today() - dt.timedelta(days=i)).strftime("%Y-%m-%d")
        data = load_day(d)
        if not data:
            continue
        pending = [m for m in data["matches"]
                   if not m.get("score") or (m.get("hf_pick") and not m.get("hf_result"))]
        if not pending:
            continue
        try:
            rows = fetch_day(d, quiet=True)
        except Exception as e:
            log("核对 %s 失败：%s" % (d, e))
            continue
        rmap = {r["no"]: r for r in rows}
        hit = 0
        for m in pending:
            r = rmap.get(m["no"])
            if not r:
                continue
            got = False
            if r.get("score") and not m.get("score"):
                m["score"] = r["score"]
                got = True
            if r.get("bf_result") and not m.get("bf_result"):
                m["bf_result"] = r["bf_result"]
                got = True
            if r.get("bq_result") and not m.get("hf_result"):
                m["hf_result"] = r["bq_result"]
                got = True
            if sync_result_fields(m):
                got = True
            if got:
                hit += 1
        if hit:
            data["checked_at"] = now_str()
            save_day(data)
            log("核对 %s：补齐 %d 场赛果" % (d, hit))
            changed.append(d)
    return changed


# ---------------------------------------------------------------- 评估
def outcome_of(gh, ga):
    return 0 if gh > ga else (1 if gh == ga else 2)


def split_score(s):
    try:
        a, b = s.split(":")
        return int(a), int(b)
    except Exception:
        return None


def score_tuple(s):
    """兼容 1:2 / 1-2 / 1–2 三种写法，返回 (1,2)。"""
    t = split_score(s)
    if t:
        return t
    try:
        a, b = str(s).replace("–", "-").replace("—", "-").replace("：", ":").split("-", 1)
        return int(a), int(b)
    except Exception:
        return None


def sync_result_fields(m):
    """按最终比分补齐四玩法结果：胜平负、比分、总进球（半全场来自官方半场比分）。"""
    sc = split_score(m.get("score", ""))
    if not sc:
        return False
    gh, ga = sc
    want = {
        "wdl_result": ["主胜", "平局", "客胜"][outcome_of(gh, ga)],
        "bf_result": "%d:%d" % (gh, ga),
        "jq_result": jq_index(gh + ga),
    }
    changed = False
    for k, v in want.items():
        if m.get(k) != v:
            m[k] = v
            changed = True
    return changed


def hit_text(m):
    sc = split_score(m.get("score", ""))
    if not sc or not m.get("probs"):
        return ""
    gh, ga = sc
    actual = "%d-%d" % (gh, ga)
    w = "胜负中" if outcome_of(gh, ga) == ["主胜", "平局", "客胜"].index(m["pick"]) else "胜负×"
    if actual == (m.get("same_score") or m["pick_score"]):
        return "比分中 " + w
    if actual == m["pick_score"]:
        return "最高中 " + w
    if actual in [x[0] for x in m["top"][:3]]:
        return "TOP3中 " + w
    return w


def hit_badge(m):
    sc = split_score(m.get("score", ""))
    if not sc:
        return "待赛"
    if not m.get("probs"):
        return ""
    actual = "%d-%d" % sc
    if actual == (m.get("same_score") or m["pick_score"]):
        return "比分中"
    if actual == m["pick_score"]:
        return "最高中"
    if actual in [x[0] for x in m["top"][:3]]:
        return "TOP3中"
    if outcome_of(*sc) == ["主胜", "平局", "客胜"].index(m["pick"]):
        return "胜负中"
    return "未中"


BT_FILE = DATA_DIR / "比分口径回测.json"


def bt_summary():
    """读回测产出的口径结论；没有跑过回测就返回 None。"""
    try:
        d = json.loads(BT_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("n") else None
    except Exception:
        return None


def evaluate(data):
    """返回某天的校准指标。hit1=同向比分命中，hitg=全场最高命中，hit3/hit5=覆盖命中。"""
    res = {"n": 0, "hitw": 0, "hit1": 0, "hitg": 0, "hit3": 0, "hit5": 0, "logp": 0.0,
           "brier": 0.0, "wlogp": 0.0, "wexp": 0.0, "e1": 0.0, "erec": 0.0, "e3": 0.0,
           "e5": 0.0,
           "rr": 0.0, "err": 0.0,
           "jqn": 0, "jqhit": 0, "jqexp": 0.0, "jqlogp": 0.0, "jqbrier": 0.0}
    for m in data["matches"]:
        if not m.get("probs"):
            continue
        res["e1"] += m["pick_score_p"]
        res["erec"] += m.get("same_score_p", m["pick_score_p"])
        res["e3"] += sum(v for _, v in m["top"][:3])
        res["e5"] += sum(v for _, v in m["top"][:5])
        res["wexp"] += max(m["probs"])
        res["rr"] += m.get("return_rate", 0)
        res["err"] = max(res["err"], m.get("fit_err", 0))
        sc = split_score(m.get("score", ""))
        if not sc:
            continue
        gh, ga = sc
        actual = "%d-%d" % (gh, ga)
        mm = model_matrices(m)
        mx = mm.get("ens") or dc_matrix(m["lambda"][0], m["lambda"][1], m.get("rho", 0))
        p = mx.get((gh, ga), 0.0)
        res["logp"] += math.log(max(p, 1e-6))
        res["n"] += 1
        res["hitw"] += outcome_of(gh, ga) == ["主胜", "平局", "客胜"].index(m["pick"])
        res["hit1"] += actual == (m.get("same_score") or m["pick_score"])
        res["hitg"] += actual == m["pick_score"]
        res["hit3"] += actual in [x[0] for x in m["top"][:3]]
        res["hit5"] += actual in [x[0] for x in m["top"][:5]]
        oi = outcome_of(gh, ga)
        res["wlogp"] += math.log(max(m["probs"][oi], 1e-6))
        jm = m.get("jq_model")
        if not jm or len(jm) != 8:
            jm = jq_dist(mx)
        jpick = m.get("jq_pick")
        if jpick is None:
            jpick = max(range(8), key=lambda k: jm[k])
        jact = m.get("jq_result")
        if jact is None:
            jact = jq_index(gh + ga)
        res["jqn"] += 1
        res["jqhit"] += int(jact == jpick)
        res["jqexp"] += jm[jpick]
        res["jqlogp"] += math.log(max(jm[jact], 1e-9))
        res["jqbrier"] += sum((jm[k] - (1.0 if k == jact else 0.0)) ** 2 for k in range(8))
        if not data.get("late"):
            ph, pd_, pa = m["probs"]
            y = [1.0 if outcome_of(gh, ga) == k else 0.0 for k in range(3)]
            res["brier"] += (ph - y[0]) ** 2 + (pd_ - y[1]) ** 2 + (pa - y[2]) ** 2
    cnt = len([m for m in data["matches"] if m.get("probs")])
    res["rr"] = res["rr"] / cnt if cnt else 0
    return res


# ---------------------------------------------------------------- 报告
LC = {"德甲": "#1f6feb", "德乙": "#3b82f6", "法甲": "#7c3aed", "英超": "#0ea5e9",
      "英冠": "#0284c7", "西甲": "#f59e0b", "意甲": "#e11d48", "葡超": "#10b981",
      "挪超": "#ef4444", "荷甲": "#f97316", "荷乙": "#fb923c", "巴西甲": "#84cc16",
      "巴甲": "#84cc16", "日职": "#0d9488", "韩K联": "#6366f1", "澳超": "#0891b2",
      "美职": "#7c3aed"}
OUT = {"主胜": ("#b91c1c", "#fef2f2"), "平局": ("#475569", "#f1f5f9"),
       "客胜": ("#1d4ed8", "#eff6ff")}
CSS = """
:root{--ink:#1e293b;--mute:#64748b;--line:#e2e8f0;--accent:#1f3b63}
*{box-sizing:border-box}
body{margin:0;background:#f5f7fa;color:var(--ink);
  font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1280px;margin:30px auto 60px;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:20px}
h1{font-size:23px;margin:0;letter-spacing:.5px}
.sub{color:var(--mute);font-size:13px;margin-top:6px}
.stamp{text-align:right;color:var(--mute);font-size:13px;line-height:1.55}
.stamp b{color:var(--accent);font-size:15px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;margin-bottom:20px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.card span{display:block;font-size:12px;color:var(--mute)}
.card b{font-size:20px;color:var(--accent);font-variant-numeric:tabular-nums}
.card em{font-style:normal;font-size:12px;color:#94a3b8;margin-left:4px}
.rcb{background:#fff7e6;border:1px solid #f5d9a8;border-radius:10px;padding:10px 14px;
  margin:-8px 0 18px;font-size:13px;color:#7c4a03;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.rcb b{color:#b45309}
.rcb a{margin-left:auto;color:#1f3b63;font-weight:600;text-decoration:none;white-space:nowrap}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;
  border:1px solid var(--line);border-radius:12px;overflow:hidden;table-layout:fixed;
  box-shadow:0 1px 2px rgba(15,23,42,.04)}
th{background:#eef2f7;color:#334155;font-size:12.5px;font-weight:600;text-align:left;
  padding:11px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
td:nth-child(5){padding-left:18px}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fbfdff}
.no{font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums;white-space:nowrap}
.lg{font-size:12px;padding:2px 8px;border-radius:6px;white-space:nowrap;display:inline-block}
.teams{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.teams b{font-weight:600}
.teams span{color:#cbd5e1;margin:0 6px;font-size:12px}
.kick{white-space:nowrap;color:#64748b;font-size:13px;font-variant-numeric:tabular-nums}
.segs{display:flex;height:7px;border-radius:4px;overflow:hidden;background:#f1f5f9}
.seg{display:block;height:100%}
.nums{display:flex;justify-content:space-between;font-size:11.5px;margin-top:4px}
.nums i{font-style:normal;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:4px 9px;border-radius:999px;border:1px solid;
  font-size:13px;white-space:nowrap}
.pill b{margin-left:5px;font-variant-numeric:tabular-nums}
.score{white-space:nowrap}
.score b{font-size:16px;font-variant-numeric:tabular-nums}
.score em{font-style:normal;margin-left:6px;font-size:12px;padding:1px 6px;border-radius:5px;
  background:#eef2f7;color:#475569;font-variant-numeric:tabular-nums}
.score em.hit{background:#e8f7ee;color:#15803d;font-weight:600}
.score em.miss{background:#fdf1f1;color:#b91c1c}
.score .alt{font-size:10.5px;color:#94a3b8;margin-top:2px;font-variant-numeric:tabular-nums;white-space:normal;line-height:1.4;max-width:100%}
.hf{white-space:nowrap;text-align:center;font-variant-numeric:tabular-nums}
.hf b{font-size:14px}
.hf em{font-style:normal;margin-left:4px;font-size:11.5px;color:#64748b}
.hf small{display:block;font-size:10.5px;color:#94a3b8;margin-top:2px}
.hf small.next{color:#64748b;font-size:9.5px;line-height:1.35}
.hf small.ok{color:#15803d;font-weight:600}
.hf small.no{color:#b91c1c}
.chips{display:flex;gap:3px;flex-wrap:wrap}
.chip{white-space:nowrap;font-size:11px;color:#475569;background:#f8fafc;
  border:1px solid var(--line);border-radius:6px;padding:2px 6px;font-variant-numeric:tabular-nums}
.chip b{font-weight:500;color:#94a3b8;margin-left:3px}
.chip.on{background:#fff7e6;border-color:#e9c46a;color:#7a5c00}
.chip.on b{color:#a97f1a}
.act{text-align:center;white-space:nowrap;font-variant-numeric:tabular-nums;color:#94a3b8}
.act.hit{color:#15803d;font-weight:700}
.act.miss{color:#b91c1c;font-weight:700}
.badge{display:inline-block;font-size:11.5px;padding:2px 7px;border-radius:5px;
  background:#f1f5f9;color:#64748b;white-space:nowrap}
.badge.ok{background:#e8f7ee;color:#15803d}
.badge.mid{background:#eef6ff;color:#1d4ed8}
.badge.no{color:#b91c1c;background:#fef2f2;font-weight:600}
footer{margin-top:18px;color:var(--mute);font-size:12.5px;line-height:1.95}
footer b{color:#334155}
.late{display:inline-block;background:#fff7e6;color:#a16207;border:1px solid #f2d08a;
  border-radius:6px;padding:1px 8px;font-size:12px;margin-left:8px}
.h2{font-size:17px;margin:28px 0 6px;color:#1f3b63}
.hint{color:var(--mute);font-size:12.5px;margin:0 0 10px;line-height:1.8}
td.hfc{text-align:center;padding:7px 3px;font-size:11.5px;
  font-variant-numeric:tabular-nums;border-left:1px solid #fff}
.hftop{font-size:11px;color:#475569;line-height:1.55;white-space:nowrap;font-variant-numeric:tabular-nums}
.hftop b{color:#1e3a8a;font-weight:600;margin-left:3px}
.hfstats{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
.hfstats div{background:#fff;border:1px solid var(--line);border-radius:9px;padding:8px 12px;min-width:112px}
.hfstats span{display:block;font-size:11.5px;color:var(--mute)}
.hfstats b{font-size:17px;color:var(--accent);font-variant-numeric:tabular-nums}
.hfstats em{font-style:normal;font-size:11px;color:#94a3b8;margin-left:4px}
.mc{font-size:13px;text-align:center;font-variant-numeric:tabular-nums;white-space:nowrap}
.mc em{display:block;font-style:normal;font-size:10.5px;color:#94a3b8;line-height:1.3}
.mc small{display:block;font-size:9.5px;color:#b6bec9;line-height:1.25}
.mc.ens{background:#f4f8ff}
.oc{text-align:center;font-variant-numeric:tabular-nums;padding:7px 4px;line-height:1.25}
.oc b{display:block;font-size:13.5px}
.oc small{display:block;font-size:9.5px;color:#94a3b8;white-space:nowrap}
.oc.on{background:#f4f8ff}
.ochip{display:inline-block;white-space:nowrap;font-size:10.5px;color:#475569;
  background:#f8fafc;border:1px solid var(--line);border-radius:5px;padding:1px 4px;
  margin:1px 2px 1px 0;font-variant-numeric:tabular-nums}
.ochip b{color:#334155;font-weight:600;margin-left:3px}
.ochip i{font-style:normal;color:#94a3b8;margin-left:3px;font-size:9px}
.ochip.hot{background:#fff7e6;border-color:#e9c46a;color:#7a5c00}
.ochip.hot b{color:#a97f1a}
.ochip.win{background:#d9f2e3;border-color:#86c79a;color:#166534}
.ochip.win b,.ochip.win i{color:#166534}
.odcell{padding:7px 9px;line-height:1.65}
.hfc .od{display:block;font-size:9.5px;color:#7f8ea3;font-weight:400;line-height:1.25;margin-top:1px}
h3.subh{font-size:15px;margin:22px 0 8px;color:#334155;font-weight:600}
@media(max-width:1000px){.card{flex:1 1 46%}}
"""


def esc(s):
    return H.escape(str(s or ""))


def seg_bar(p):
    return "".join(
        f'<span class="seg" style="width:{v*100:.1f}%;background:{OUT[n][0]}"></span>'
        for n, v in zip(["主胜", "平局", "客胜"], p))


def odds_panorama(data):
    """四玩法赔率全景：胜平负 3 档、比分 31 档、总进球 8 档（半全场另表）。"""
    W = ["主胜", "平局", "客胜"]
    wrows, brows, jrows = [], [], []
    cov = {"wdl": 0, "bf": 0, "jq": 0, "hf": 0}
    cal = {k: {"n": 0, "hit": 0, "mhit": 0} for k in ("wdl", "bf", "jq", "hf")}

    def hf_of(m):
        hf = m.get("hf")
        if (not hf or len(hf) != 9) and m.get("lambda"):
            try:
                hf = hf_probs(m["lambda"][0], m["lambda"][1], rho=m.get("rho", RHO))
            except Exception:
                hf = None
        return hf if hf and len(hf) == 9 else None

    def bq_of(m):
        q = m.get("bq_probs")
        if q and len(q) == 9:
            return q
        o = m.get("bq_odds")
        if o and len(o) == 9 and all(isinstance(x, (int, float)) and x > 1.0 for x in o):
            try:
                return shin([float(x) for x in o])[0]
            except Exception:
                return None
        return None

    def verdict(m1, m2, act):
        if not act:
            return '<span class="badge">待赛</span>'
        return ('<div>%s</div><div style="margin-top:2px">%s</div>'
                % (m1, m2))

    for m in data["matches"]:
        if not m.get("probs"):
            continue
        sc = split_score(m.get("score", ""))
        team = ('<td class="teams" title="%s vs %s"><b>%s</b><span>vs</span>%s</td>'
                % (esc(m["home"]), esc(m["away"]), esc(m["home"]), esc(m["away"])))
        no = '<td class="no">%s</td>' % esc(m["no"])

        # ---- 胜平负 3 档
        odds = m.get("odds") or []
        dp = odds_probs(odds) if len(odds) == 3 else None
        if dp:
            cov["wdl"] += 1
            act = m.get("wdl_result") or (W[outcome_of(*sc)] if sc else "")
            pi = W.index(m["pick"]) if m.get("pick") in W else max(range(3), key=lambda i: m["probs"][i])
            mi = min(range(3), key=lambda i: odds[i])
            hit = (act == W[pi]) if act else None
            mhit = (act == W[mi]) if act else None
            if act:
                cal["wdl"]["n"] += 1
                cal["wdl"]["hit"] += int(bool(hit))
                cal["wdl"]["mhit"] += int(bool(mhit))
            cells = "".join(
                '<td class="oc%s"><b>%s</b><small>模型 %.1f%% · 市 %.1f%%</small></td>'
                % (" on" if i == pi else "", fmt_odds(odds[i]),
                   m["probs"][i] * 100, dp[i] * 100)
                for i in range(3))
            chk = verdict(
                '<span class="badge ok">模型中</span>' if hit else '<span class="badge no">模型未中</span>',
                '<span class="badge ok">市场中</span>' if mhit else '<span class="badge no">市场未中</span>',
                act)
            wrows.append(
                "<tr>%s%s%s"
                '<td class="oc">%s <b>%.0f%%</b></td>'
                '<td class="oc">%s <b>%.0f%%</b></td>'
                '<td class="act%s">%s</td><td>%s</td></tr>'
                % (no, team, cells,
                   W[pi], m["probs"][pi] * 100,
                   W[mi], dp[mi] * 100,
                   " hit" if hit else (" miss" if act else ""),
                   esc(act) or "—", chk))

        # ---- 比分 31 档
        bfo = m.get("bf_odds")
        if isinstance(bfo, dict):
            keys = [k for k in bfo if isinstance(bfo[k], (int, float)) and bfo[k] > 1.0]
            if len(keys) >= 20:
                cov["bf"] += 1
                ens = model_matrices(m).get("ens") or {}
                mp = bf_model_probs(ens, keys)
                low = min(keys, key=lambda k: bfo[k])
                act = m.get("bf_result") or ("%d:%d" % sc if sc else "")
                actk = bf_key_of(act, keys)
                same = m.get("same_score") or m["pick_score"]
                same_p = m.get("same_score_p", m["pick_score_p"])
                act_sc = score_tuple(act)
                same_sc = score_tuple(same)
                hit = (act_sc == same_sc) if (act_sc and same_sc) else None
                top3 = (act_sc in [score_tuple(x[0]) for x in m["top"][:3]]) if act_sc else None
                mhit = (actk == low) if act else None
                if act:
                    cal["bf"]["n"] += 1
                    cal["bf"]["hit"] += int(bool(hit))
                    cal["bf"]["mhit"] += int(bool(mhit))
                chips = "".join(
                    '<span class="ochip%s%s" title="%s 赔率 %s · 模型 %.1f%%">%s<b>%s</b>'
                    "<i>%.1f%%</i></span>"
                    % (" hot" if k == low else "", " win" if k == actk else "",
                       esc(k), fmt_odds(bfo[k]), mp.get(k, 0.0) * 100,
                       esc(k), fmt_odds(bfo[k]), mp.get(k, 0.0) * 100)
                    for k in keys)
                if not act:
                    m1 = m2 = ""
                    chk = '<span class="badge">待赛</span>'
                else:
                    m1 = ('<span class="badge ok">模型中</span>' if hit else
                          ('<span class="badge mid">TOP3中</span>' if top3 else
                           '<span class="badge no">模型未中</span>'))
                    m2 = ('<span class="badge ok">市场中</span>' if mhit else
                          '<span class="badge no">市场未中</span>')
                    chk = verdict(m1, m2, act)
                brows.append(
                    "<tr>%s%s"
                    '<td class="odcell">%s</td>'
                    '<td class="oc">%s<small>%.1f%%</small></td>'
                    '<td class="act%s">%s</td><td>%s</td></tr>'
                    % (no, team, chips, same, same_p * 100,
                       " hit" if hit else (" miss" if act else ""),
                       esc(act) or "—", chk))

        # ---- 总进球 8 档
        jqo = m.get("jq_odds")
        if isinstance(jqo, dict):
            try:
                pairs = sorted((int(k), float(v)) for k, v in jqo.items())
            except Exception:
                pairs = []
            pairs = [(k, v) for k, v in pairs if v > 1.0]
            if len(pairs) == 8:
                cov["jq"] += 1
                jmap = dict(pairs)
                jp = m.get("jq_probs")
                if not jp or len(jp) != 8:
                    jp = odds_probs([jmap[k] for k in range(8)])
                jm = m.get("jq_model")
                if not jm or len(jm) != 8:
                    jm = jq_dist(model_matrices(m).get("ens") or {})
                jpick = m.get("jq_pick")
                if jpick is None:
                    jpick = max(range(8), key=lambda k: jm[k])
                act = m.get("jq_result")
                if act is None and sc:
                    act = jq_index(sum(sc))
                low = min(range(8), key=lambda k: jmap[k])
                hit = (act == jpick) if act is not None else None
                mhit = (act == low) if act is not None else None
                if act is not None:
                    cal["jq"]["n"] += 1
                    cal["jq"]["hit"] += int(bool(hit))
                    cal["jq"]["mhit"] += int(bool(mhit))
                cells = ""
                for k in range(8):
                    bg = ""
                    if act is not None and k == act:
                        bg = ' style="background:#d9f2e3;color:#166534"'
                    cells += (
                        '<td class="oc%s"%s><b>%s</b><small>模型 %.0f%%</small></td>'
                        % (" on" if k == jpick else "", bg, fmt_odds(jmap[k]),
                           jm[k] * 100))
                chk = verdict(
                    '<span class="badge ok">模型中</span>' if hit else '<span class="badge no">模型未中</span>',
                    '<span class="badge ok">市场中</span>' if mhit else '<span class="badge no">市场未中</span>',
                    "" if act is None else "x")
                jrows.append(
                    "<tr>%s%s%s"
                    '<td class="oc">%s <b>%.0f%%</b></td>'
                    '<td class="oc">%s <b>%.0f%%</b></td>'
                    '<td class="act%s">%s</td><td>%s</td></tr>'
                    % (no, team, cells,
                       JQ_TXT[jpick], jm[jpick] * 100,
                       JQ_TXT[low], (jp[low] * 100) if jp else 0.0,
                       " hit" if hit else (" miss" if act is not None else ""),
                       "—" if act is None else JQ_TXT[act], chk))

        # ---- 半全场 9 档（仅统计，表在下一节）
        bqo = m.get("bq_odds")
        if bqo and len(bqo) == 9:
            cov["hf"] += 1
        hf = hf_of(m)
        bq = bq_of(m)
        act = m.get("hf_result")
        if hf and act in HF_VALUES:
            ai = HF_VALUES.index(act)
            cal["hf"]["n"] += 1
            cal["hf"]["hit"] += int(max(range(9), key=lambda i: hf[i]) == ai)
            if bq:
                cal["hf"]["mhit"] += int(max(range(9), key=lambda i: bq[i]) == ai)

    if not (wrows or brows or jrows):
        return ""

    def stat(title, k, total):
        c = cal[k]
        if c["n"]:
            b = "%d/%d" % (c["hit"], c["n"])
            em = "市场 %d/%d" % (c["mhit"], c["n"])
        else:
            b, em = "—", "待核对"
        return ('<div><span>%s · 赔率 %d 场</span><b>%s</b><em>模型命中 · %s</em></div>'
                % (title, total, b, em))
    stats = ('<div class="hfstats">%s%s%s%s</div>'
             % (stat("胜平负", "wdl", cov["wdl"]), stat("比分", "bf", cov["bf"]),
                stat("总进球", "jq", cov["jq"]), stat("半全场", "hf", cov["hf"])))
    wdl_tbl = jq_tbl = bf_tbl = ""
    if wrows:
        wdl_tbl = """
<h3 class="subh">胜平负 · 3 档赔率</h3>
<table><colgroup><col style="width:58px"><col style="width:180px">
<col style="width:82px"><col style="width:82px"><col style="width:82px">
<col style="width:88px"><col style="width:88px"><col style="width:58px"><col style="width:94px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th><th style="text-align:center">主胜</th>
<th style="text-align:center">平局</th><th style="text-align:center">客胜</th>
<th style="text-align:center">模型首选</th><th style="text-align:center">市场首选</th>
<th style="text-align:center">实际</th><th style="text-align:center">核对</th></tr></thead>
<tbody>%s</tbody></table>""" % "".join(wrows)
    if brows:
        bf_tbl = """
<h3 class="subh">比分 · 31 档赔率</h3>
<table><colgroup><col style="width:58px"><col style="width:180px"><col>
<col style="width:88px"><col style="width:58px"><col style="width:98px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th><th>比分赔率（小字为共识模型概率）</th>
<th style="text-align:center">模型同向</th><th style="text-align:center">实际</th>
<th style="text-align:center">核对</th></tr></thead>
<tbody>%s</tbody></table>""" % "".join(brows)
    if jrows:
        jq_tbl = """
<h3 class="subh">总进球 · 8 档赔率</h3>
<table><colgroup><col style="width:58px"><col style="width:180px">
%s<col style="width:86px"><col style="width:86px"><col style="width:56px"><col style="width:94px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th>%s
<th style="text-align:center">模型首选</th><th style="text-align:center">市场首选</th>
<th style="text-align:center">实际</th><th style="text-align:center">核对</th></tr></thead>
<tbody>%s</tbody></table>""" % (
            "".join('<col style="width:54px">' for _ in range(8)),
            "".join('<th style="text-align:center">%s</th>' % t for t in JQ_TXT),
            "".join(jrows))
    return """
<h2 class="h2">四玩法赔率全景 · 胜平负 / 比分 / 总进球</h2>
<p class="hint">每个玩法同时给出赔率、共识模型概率、模型首选与市场首选（最低赔率档）以及实际赛果。
比分 31 档里琥珀色是市场最低赔率档、绿色是实际比分，小字是共识模型概率；“模型同向”与主表“同向比分”一致。
总进球绿色是实际档。半全场 9 档赔率见下一节，每格概率下方的小字就是对应赔率。</p>
%s%s%s%s
""" % (stats, wdl_tbl, bf_tbl, jq_tbl)


def render_day(data):
    day = data["date"]
    ev = evaluate(data)
    n = len(data["matches"])
    rows = []
    for m in data["matches"]:
        if not m.get("probs"):
            continue
        p = m["probs"]
        name = m["pick"]
        col, bg = OUT[name]
        sc = split_score(m.get("score", ""))
        w = "hit" if sc and outcome_of(*sc) == ["主胜", "平局", "客胜"].index(name) else (
            "miss" if sc else "")
        s_hit = "hit" if sc and "%d-%d" % sc == (m.get("same_score") or m["pick_score"]) else ""
        same = m.get("same_score") or m["pick_score"]
        same_p = m.get("same_score_p", m["pick_score_p"])
        mtxt = m.get("margin_pair")
        mp = m.get("margin_pair_p")
        midx = m.get("margin_pair_idx")
        if not mtxt or mp is None or not midx:
            try:
                midx, mp, mtxt = dir_margin_pair(model_matrices(m).get("ens") or {})
            except Exception:
                midx, mp, mtxt = None, None, None
        band_hit = bool(sc) and midx is not None and margin_index(*sc) in midx
        alt_bits = []
        if same != m["pick_score"]:
            alt_bits.append("全场最高 %s %.1f%%" % (m["pick_score"], m["pick_score_p"] * 100))
        if mtxt and mp is not None:
            alt_bits.append("净胜球 %s %.0f%%%s" % (
                mtxt, mp * 100,
                (" ✓" if band_hit else " ×") if sc else ""))
        alt = ('<div class="alt">%s</div>' % " · ".join(alt_bits)) if alt_bits else ""
        # 半全场：前三 + 实际结果（9 档全算，只把最有信息量的三档摆出来）
        hf = m.get("hf")
        if (not hf or len(hf) != 9) and m.get("lambda"):
            try:
                hf = hf_probs(m["lambda"][0], m["lambda"][1], rho=m.get("rho", RHO))
            except Exception:
                hf = None
        hf_cell = '<td class="hf">—</td>'
        if hf and len(hf) == 9:
            order = sorted(range(9), key=lambda i: -hf[i])
            hcode = HF_VALUES[order[0]]
            hact = m.get("hf_result") if m.get("hf_result") in HF_VALUES else ""
            if hact:
                hcls = "ok" if hact == hcode else "no"
                hact_html = f'<small class="{hcls}">实际 {hf_label(hact)}</small>'
            else:
                hact_html = '<small>实际 —</small>'
            nxt = ""
            if len(order) > 1:
                nxt = (f'<small class="next">次 {hf_label(HF_VALUES[order[1]])} '
                       f'{hf[order[1]]*100:.1f}%')
                if len(order) > 2:
                    nxt += (f' · 三 {hf_label(HF_VALUES[order[2]])} '
                            f'{hf[order[2]]*100:.1f}%')
                nxt += '</small>'
            hf_cell = (f'<td class="hf"><b>{hf_label(hcode)}</b><em>{hf[order[0]]*100:.1f}%</em>'
                       f'{nxt}{hact_html}</td>')
        t3 = [x[0] for x in m["top"][:3]]
        t3_hit = "hit" if sc and "%d-%d" % sc in t3 else ("miss" if sc else "")
        chips = "".join(
            f'<span class="chip{" on" if i == 0 else ""}">{s}<b>{v*100:.1f}%</b></span>'
            for i, (s, v) in enumerate(m["top"][:3]))
        if not sc:
            badge = '<span class="badge">待赛</span>'
        elif "%d-%d" % sc == same:
            badge = '<span class="badge ok">比分中</span>'
        elif "%d-%d" % sc == m["pick_score"]:
            badge = '<span class="badge ok">最高中</span>'
        elif "%d-%d" % sc in t3:
            badge = '<span class="badge ok">TOP3中</span>'
        elif w == "hit":
            badge = '<span class="badge mid">胜负中</span>'
        else:
            badge = '<span class="badge no">未中</span>'
        rows.append(f"""
      <tr>
        <td class="no">{esc(m['no'])}</td>
        <td><span class="lg" style="background:{LC.get(m['league'],'#64748b')}1a;color:{LC.get(m['league'],'#64748b')}">{esc(m['league'])}</span></td>
        <td class="teams" title="{esc(m['home'])} vs {esc(m['away'])}"><b>{esc(m['home'])}</b><span>vs</span>{esc(m['away'])}</td>
        <td class="kick" title="{esc(m.get('kickoff',''))}">{esc((m.get('kickoff','') or '')[5:16])}</td>
        <td><div class="segs">{seg_bar(p)}</div>
          <div class="nums"><i style="color:{OUT['主胜'][0]}">{p[0]*100:.0f}%</i><i style="color:{OUT['平局'][0]}">{p[1]*100:.0f}%</i><i style="color:{OUT['客胜'][0]}">{p[2]*100:.0f}%</i></div></td>
        <td><span class="pill" style="color:{col};background:{bg};border-color:{col}33">{name}<b>{max(p)*100:.0f}%</b></span></td>
        <td class="score"><b>{same}</b><em class="{s_hit}">{same_p*100:.1f}%</em>{alt}</td>
        {hf_cell}
        <td><div class="chips">{chips}</div></td>
        <td class="act {w}">{esc(m.get('score','')) or '—'}</td>
        <td>{badge}</td>
      </tr>""")
    body = "".join(rows)

    # ---- 多模型对照
    ms = model_stats(data)
    mrows = []
    agree = 0
    for m in data["matches"]:
        mx = model_matrices(m)
        if not mx:
            continue
        sc = split_score(m.get("score", ""))
        actual = "%d-%d" % sc if sc else ""
        picks = [same_pick(mx[k])[0] for k in ("dc", "poi", "mkt", "tgs") if k in mx]
        if picks and len(set(picks)) == 1:
            agree += 1
        mh = [same_pick(mx[k])[0] for k in ("dc", "poi", "mkt", "tgs", "ens") if k in mx]
        hit_any = bool(actual) and actual in mh
        cells = ""
        for k in ("dc", "poi", "mkt", "tgs", "ens"):
            if k not in mx:
                cells += '<td class="mc">—</td>'
                continue
            pk, pp, _ = model_pick(mx[k])
            sk, sp, _ = same_pick(mx[k])
            style = (' style="background:#d9f2e3;color:#166534"' if sk == actual else "")
            cells += (f'<td class="mc{" ens" if k == "ens" else ""}"{style}><b>{sk}</b>'
                      f'<em>{sp*100:.1f}%</em><small>最高 {pk} {pp*100:.0f}%</small></td>')
        badge = ('<span class="badge ok">中</span>' if hit_any else
                 ('<span class="badge no">未中</span>' if actual else '<span class="badge">待赛</span>'))
        mrows.append(f"""
      <tr><td class="no">{esc(m['no'])}</td>
        <td class="teams" title="{esc(m['home'])} vs {esc(m['away'])}"><b>{esc(m['home'])}</b><span>vs</span>{esc(m['away'])}</td>
        {cells}<td class="act">{esc(m.get('score','')) or '—'}</td>
        <td style="text-align:center">{badge}</td></tr>""")
    model_block = ""
    if mrows:
        strips = ""
        for k in MODEL_KEYS:
            st = ms[k]
            if not st["n"]:
                continue
            ln = st["n"]
            strips += f"""
  <div><span>{MODEL_TXT[k]}</span><b>{st['hits']}/{ln}</b><em>同向命中 · 期望 {st['exps']:.2f} · 最高格 {st['hit']}/{ln} · log {st['logp']/ln:.3f} · Brier {st['brier']/ln:.3f}</em></div>"""
        model_block = f"""
<h2 class="h2">多模型对照 · 共 {len(mrows)} 场</h2>
<p class="hint">每个模型各算一张比分矩阵，浅蓝底色那一列是共识：DC = 用胜平负赔率反解期望进球再做 Dixon-Coles 低比分修正；
泊松 = 同一套拟合但去掉修正；市场 = 比分玩法 31 档赔率 Shin 去水；总进球 = 市场 0—7+ 总进球分布 × DC 的同进球档内比分形状。
共识按 DC 30% / 泊松 10% / 市场 35% / 总进球 25% 加权。
表内大字是各模型自己胜负方向内的最高比分（同向比分），小字才是整张矩阵的最高格。
四个模型同向比分完全一致的场次：{agree} 场。单格概率 10% 上下是常态，1-1 常是“最高的一格”，但不代表它大概率发生。</p>
<div class="hfstats">{strips}</div>
<table><colgroup><col style="width:58px"><col style="width:186px">
<col style="width:92px"><col style="width:92px"><col style="width:92px"><col style="width:92px"><col style="width:92px">
<col style="width:58px"><col style="width:52px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th><th style="text-align:center">DC</th><th style="text-align:center">泊松</th>
<th style="text-align:center">市场</th><th style="text-align:center">总进球</th><th style="text-align:center">共识</th>
<th style="text-align:center">实际</th><th style="text-align:center">核对</th></tr></thead>
<tbody>{''.join(mrows)}</tbody></table>"""

    # ---- 半全场区块：9 档概率热力表
    hf_rows, hf_exp, hf_done, hf_hit, hf_mkt, hf_exp_done = [], 0.0, 0, 0, 0, 0.0
    hf_top1 = {}
    for m in data["matches"]:
        hf = m.get("hf")
        if not hf or len(hf) != 9:
            lam = m.get("lambda")
            hf = hf_probs(lam[0], lam[1], rho=m.get("rho", RHO)) if lam else None
        if not hf:
            continue
        order = sorted(range(9), key=lambda i: -hf[i])
        hi = order[0]
        pick = HF_VALUES[hi]
        hf_exp += hf[hi]
        hf_top1[pick] = hf_top1.get(pick, 0) + 1
        mk = m.get("bq_probs")
        if (not mk or len(mk) != 9) and m.get("bq_odds"):
            try:
                mk = shin([float(x) for x in m["bq_odds"]])[0]
            except Exception:
                mk = None
        if mk and len(mk) == 9:
            hf_mkt += 1
            dv = (hf[hi] - mk[hi]) * 100
            dv_txt = ("%+.1f" % dv)
            dv_col = "#1d4ed8" if dv >= 0 else "#b45309"
        else:
            dv_txt, dv_col = "—", "#94a3b8"
        act = m.get("hf_result") if m.get("hf_result") in HF_VALUES else ""
        if act:
            hf_done += 1
            hf_exp_done += hf[hi]
            if act == pick:
                hf_hit += 1
        bqo = m.get("bq_odds")
        bqo = bqo if isinstance(bqo, list) and len(bqo) == 9 else None
        rank = {hi: "①", order[1]: "②", order[2]: "③"} if len(order) > 2 else {hi: "①"}
        cells = []
        for i, code in enumerate(HF_VALUES):
            p = hf[i]
            if p >= 0.14:
                bg, fg, fw = "#e8f0fb", "#334155", "400"
            elif p >= 0.09:
                bg, fg, fw = "#f2f7fd", "#475569", "400"
            elif p >= 0.045:
                bg, fg, fw = "#f8fafc", "#64748b", "400"
            else:
                bg, fg, fw = "#ffffff", "#94a3b8", "400"
            ring = ""
            if i == hi:
                bg, fg, fw = "#dbeafe", "#1e3a8a", "700"
                ring = "box-shadow:inset 0 0 0 2px #1e3a8a"
            if act and code == act:
                bg, fg, fw = "#d9f2e3", "#166534", "700"
            rk = rank.get(i, "")
            rk_html = f'<span style="font-size:9px;vertical-align:super">{rk}</span>' if rk else ""
            od = (f'<span class="od">{fmt_odds(bqo[i])}</span>'
                  if bqo and bqo[i] and bqo[i] > 1.0 else "")
            ttl = (f"{hf_label(code)} 模型 {p*100:.1f}%"
                   + (f" · 赔率 {fmt_odds(bqo[i])}" if od else ""))
            cells.append(
                f'<td class="hfc" style="background:{bg};color:{fg};font-weight:{fw};{ring}"'
                f' title="{ttl}">{rk_html}{p*100:.1f}%{od}</td>')
        if not act:
            badge = '<span class="badge">待赛</span>'
        elif act == pick:
            badge = '<span class="badge ok">中</span>'
        else:
            badge = '<span class="badge no">未中</span>'
        if mk and len(mk) == 9:
            mi = max(range(9), key=lambda i: mk[i])
            mkt_cell = (f'<td class="hfc">{hf_label(HF_VALUES[mi])}'
                        f'<small class="od">{mk[mi]*100:.1f}%</small></td>')
        else:
            mkt_cell = '<td class="hfc">—</td>'
        top3 = "".join(
            f'<div>{rk}{hf_label(HF_VALUES[k])} <b>{hf[k]*100:.1f}%</b></div>'
            for rk, k in zip("①②③", order[:3]))
        hf_rows.append(f"""
      <tr>
        <td class="no">{esc(m['no'])}</td>
        <td class="teams" title="{esc(m['home'])} vs {esc(m['away'])}"><b>{esc(m['home'])}</b><span>vs</span>{esc(m['away'])}</td>
        <td class="hftop">{top3}</td>
        {''.join(cells)}
        {mkt_cell}
        <td class="hfc" style="color:{dv_col};font-weight:600">{dv_txt}</td>
        <td class="hfc" style="color:{'#166534' if act and act == pick else '#475569'};font-weight:{'700' if act and act == pick else '400'}">{hf_label(act) if act else '—'}</td>
        <td style="text-align:center">{badge}</td>
      </tr>""")
    hf_head = "".join(f'<th style="text-align:center;padding:9px 2px">{hf_label(c)}</th>' for c in HF_VALUES)
    hf_block = ""
    if hf_rows:
        hs = hf_stats(data)
        hl = (hs["logp"] / hs["n"]) if hs["n"] else None
        hb = (hs["brier"] / hs["n"]) if hs["n"] else None
        hl_col = "#94a3b8" if hl is None else ("#15803d" if hl > -math.log(9) else "#b45309")
        hb_col = "#94a3b8" if hb is None else ("#15803d" if hb < 8 / 9 else "#b45309")
        hf_rate = (f"{hs['hit']/hs['n']*100:.0f}% · 期望 {hf_exp_done:.2f}" if hs["n"] else "")
        hf_t3 = (f"{hs['top3']/hs['n']*100:.0f}% · {hs['top3']}/{hs['n']}" if hs["n"] else "—")
        stats = f"""
<div class="hfstats">
  <div><span>已核对</span><b>{hs['n']}</b><em>场</em></div>
  <div><span>首选命中</span><b>{hs['hit'] if hs['n'] else '—'}</b><em>{hf_rate}</em></div>
  <div><span>前三覆盖</span><b>{hs['top3'] if hs['n'] else '—'}</b><em>{hf_t3}</em></div>
  <div><span>平均 log</span><b style="color:{hl_col}">{'—' if hl is None else f'{hl:.3f}'}</b><em>盲猜 −2.197</em></div>
  <div><span>Brier</span><b style="color:{hb_col}">{'—' if hb is None else f'{hb:.3f}'}</b><em>盲猜 0.889</em></div>
  <div><span>全日期望命中</span><b>{hf_exp:.2f}</b><em>场</em></div>
  <div><span>有赔率对照</span><b>{hf_mkt}</b><em>场</em></div>
</div>"""
        hf_block = f"""
<h2 class="h2">半全场 · 9 档赔率与概率</h2>
<p class="hint">9 种“半场/全场”组合全部参与计算，行内 9 格大字是模型概率、下方小字是对应赔率；① ② ③ 标出概率最高的三档，深蓝为第一档，绿格为实际结果。
“市场首选”是九档赔率最低的一档，“市场差”= 模型第一档概率 − 九档赔率 Shin 去水后同一档概率，正数表示模型比市场更看好这一档。</p>
{stats}
<table><colgroup><col style="width:58px"><col style="width:180px"><col style="width:146px">
{"".join('<col style="width:52px">' for _ in HF_VALUES)}<col style="width:70px"><col style="width:50px"><col style="width:58px"><col style="width:52px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th><th>前三</th>{hf_head}<th style="text-align:center">市场首选</th><th style="text-align:center">市场差</th><th style="text-align:center">实际</th><th style="text-align:center">核对</th></tr></thead>
<tbody>{''.join(hf_rows)}</tbody></table>"""

    late = '<span class="late">事后补测，不计入校准</span>' if data.get("late") else ""
    odds_block = odds_panorama(data)
    rho = next((m.get("rho", 0.0) for m in data["matches"] if m.get("rho") is not None), 0.0)
    hf_top1_txt = "、".join("%s %d 场" % (hf_label(k), v)
                           for k, v in sorted(hf_top1.items(), key=lambda kv: -kv[1]))
    mk_same = mk_n = 0
    for m in data["matches"]:
        bq = m.get("bq_probs")
        if not bq or len(bq) != 9:
            continue
        mk_n += 1
        if HF_VALUES[max(range(9), key=lambda i: bq[i])] in ("3-3", "0-0"):
            mk_same += 1
    hf_final = hf_stats(data)
    hf_cal_txt = ("模型第一档命中 %d/%d、前三覆盖 %d/%d" %
                  (hf_final["hit"], hf_final["n"], hf_final["top3"], hf_final["n"])
                  if hf_final["n"] else "本日尚未开赛核对，累计数据见汇总页")
    wd = "一二三四五六日"[dt.date.fromisoformat(day).weekday()]
    cards = f"""
  <div class="card"><span>场次</span><b>{n}</b></div>
  <div class="card"><span>返还率</span><b>{ev['rr']*100:.1f}%</b></div>
  <div class="card"><span>已核对</span><b>{ev['n']}</b><em>场</em></div>"""
    if ev["n"]:
        cards += f"""
  <div class="card"><span>胜负命中</span><b>{ev['hitw']}/{ev['n']}</b><em>{ev['hitw']/ev['n']*100:.0f}%</em></div>
  <div class="card"><span>比分命中</span><b>{ev['hit1']}/{ev['n']}</b><em>最高格 {ev['hitg']} · TOP3 {ev['hit3']}</em></div>
  <div class="card"><span>比分前三覆盖</span><b>{ev['hit3']}/{ev['n']}</b><em>{ev['hit3']/ev['n']*100:.0f}%</em></div>
  <div class="card"><span>比分前五覆盖</span><b>{ev['hit5']}/{ev['n']}</b><em>{ev['hit5']/ev['n']*100:.0f}%</em></div>
  <div class="card"><span>总进球命中</span><b>{ev['jqhit']}/{ev['jqn']}</b><em>{ev['jqhit']/ev['jqn']*100:.0f}%</em></div>"""
    bt = bt_summary()
    bt_txt = ("回测 %d 场：前三 %.1f%%、前五 %.1f%%，每命中一场约需 %.1f/%.1f 注" % (
        bt["n"], bt["top3_rate"] * 100, bt["top5_rate"] * 100,
        bt["top3_cost"], bt["top5_cost"])) if bt else "回测页尚未生成（跑一次 --backtest）"
    cover_note = f"""
<div class="rcb">📊 <b>覆盖口径（本日 {n} 场）：</b>每场取概率前三共 <b>{3 * n} 注</b>，期望命中
<b>{ev['e3']:.2f} 场</b>；取前五共 <b>{5 * n} 注</b>，期望命中 <b>{ev['e5']:.2f} 场</b>。{bt_txt}。
覆盖率随注数线性上升，单注期望不变。<a href="比分口径回测.html">口径依据与检验</a></div>"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>竞彩比分日报 · {day}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <div><h1>竞彩比分概率日报{late}</h1>
    <div class="sub">Shin 去水 · DC / 泊松 / 市场比分 / 总进球 四模型共识 · 只做概率分布与校准记录</div></div>
  <div class="stamp"><b>{day}（周{wd}）</b><br>封存 {esc(data.get('sealed_at',''))}<br>样本 {n} 场</div>
</header>
<div class="cards">{cards}</div>
{cover_note}
{recheck_banner(day)}
<table>
<colgroup><col style="width:50px"><col style="width:52px"><col style="width:168px">
<col style="width:82px"><col style="width:140px"><col style="width:82px">
<col style="width:170px"><col style="width:140px"><col style="width:160px"><col style="width:60px"><col style="width:70px"></colgroup>
<thead><tr><th>场次</th><th>联赛</th><th>对阵</th><th>开球</th><th>胜 / 平 / 负</th>
<th>预测胜负</th><th>同向比分</th><th>半全场</th><th>概率前三</th><th>实际</th><th>核对</th></tr></thead>
<tbody>{body}</tbody></table>
{odds_block}
{hf_block}
{model_block}
<footer>
<b>读法：</b>“同向比分”是限定在“预测胜负”方向内的最高比分，和胜负判断一致；“净胜球”把比分矩阵按赢几球合并，单档通常 30%—45%，比单格稳；“全场最高”是整张比分矩阵里概率最高的一格，常是 1-1，但一般只有 10%—14%——它是最高的一格，不是大概率事件。TOP3 仍按整张矩阵取，“前三覆盖”是这三格概率之和，通常 28%—35%。<br>
<b>半全场栏：</b>大字是第一档（概率最高的“半场/全场”），下面“次 / 三”是第二、三档，最下面是实际结果；绿=中、红=未中，待赛显示“实际 —”。9 种组合全部参与计算，完整分布与市场对照在下方“半全场分布”表。半全场单格天然只有三成左右命中，低于 45% 只作参考，不串关；高把握档按单关出票。<br>
<b>半全场第一档分布：</b>本日 {hf_top1_txt or '—'}。胜/胜、负/负 占多数不是模型只算这两种，而是 9 档里它们通常最高；有九档赔率的 {mk_n} 场里，市场赔率最低的一档也有 {mk_same} 场落在这两类。其余组合常在 2%—14%，会作为第二、三档出现。<br>
<b>量级：</b>同向比分概率多在 7%—12%，{n} 场合计期望命中 {ev['erec']:.2f} 场（全场最高格期望 {ev['e1']:.2f} 场）；TOP3 合计 {ev['e3']:.2f} 场、TOP5 合计 {ev['e5']:.2f} 场。胜负命中率正常区间 50%—55%。模型拟合误差 {ev['err']:.5f}（越小越贴近市场隐含概率）；低比分相关系数 ρ={rho:.2f}，用于把 0-0、1-0、0-1、1-1 四格重新分配，且拟合时一并纳入，避免二次放大小比分。<br>
<b>多模型：</b>DC、泊松、市场比分、总进球四种口径各出一张矩阵，主表与卡片用共识（40% 模型 + 60% 市场口径）。历史记录已按同一套输入（赛前赔率）重算，不用赛果反推。<br>
<b>半全场：</b>把全场期望进球按 {HF_SHARE*100:.0f}% / {100-HF_SHARE*100:.0f}% 拆到上下半场，两段各用一版 Dixon-Coles 矩阵（同一个 ρ={rho:.2f}，修正半场 0-0、1-1 被独立泊松低估的问题），再组合成 9 档。191 场同窗口回测把半场占比从 45% 调到 40%（log 1.853、Brier 0.7871，均优于 45% 的 1.867 / 0.7880）；翻盘格再按实测做七成收缩校准：胜/负由 2.5% 提到 3.9%、负/胜由 2.9% 提到 3.8%（实际 4.7% / 4.2%），补上“领先方收着踢、落后方压上”的低估。首选命中率仍在 33% 左右，这是 9 选 1 的结构上限。所以半全场只在高把握（≥45%）时按单关出票（校准后回测 23 注 15 中、ROI 105.8%），2 串 1 联合概率就掉到 20% 以下，3 串 1 只剩 5%—10%，这是之前“总不中”的主因。{hf_cal_txt}；前三覆盖更能说明“九种结果都在算”，而不是只押两种。市场概率由 9 档赔率 Shin 去水得到，不是官方口径，只作对照。平均 log 取实际结果那一档概率的自然对数再平均，越高越好；九档 Brier 是 9 档概率与实际结果的平方误差之和再平均，越低越好。两者盲猜基准分别为 −2.197 与 0.889，绿字优于盲猜、橙字不如盲猜。<br>
<b>四玩法赔率：</b>胜平负 3 档、比分 31 档（含“胜/平/负其它”）、总进球 0—7+ 八档、半全场 9 档，四类赔率都在赛前抓取并随记录封存，报告里逐场列出原始赔率、去水概率、共识模型概率、模型首选与市场最低赔率档。总进球实际值 7 球及以上统一记为 7+；比分比对时未单列的比分归入对应的“其它”档。<br>
<b>声明：</b>本页是概率分布与赛前记录，不构成任何投注建议。　<a href="汇总.html" style="color:#1f3b63">累计校准 →</a>
</footer>
</div></body></html>"""


def render_index(days):
    tot = {"n": 0, "hitw": 0, "hit1": 0, "hitg": 0, "hit3": 0, "hit5": 0, "logp": 0.0, "brier": 0.0,
           "wlogp": 0.0, "wexp": 0.0,
           "e1": 0.0, "erec": 0.0, "e3": 0.0, "e5": 0.0, "rr": 0.0, "hfn": 0, "hfh": 0, "hft3": 0,
           "hflogp": 0.0, "hfbrier": 0.0, "hfexp": 0.0,
           "jqn": 0, "jqhit": 0, "jqexp": 0.0, "jqlogp": 0.0, "jqbrier": 0.0}
    mtot = {k: {"n": 0, "hit": 0, "exp": 0.0, "hits": 0, "exps": 0.0,
                "logp": 0.0, "brier": 0.0} for k in MODEL_KEYS}
    rows = []
    for data in sorted(days, key=lambda d: d["date"], reverse=True):
        ev = evaluate(data)
        if data.get("late"):
            continue
        hs = hf_stats(data)
        ev["hfn"], ev["hfh"] = hs["n"], hs["hit"]
        ev["hft3"] = hs["top3"]
        ev["hflogp"], ev["hfbrier"] = hs["logp"], hs["brier"]
        ev["hfexp"] = hs["exp"]
        mst = model_stats(data)
        for k in MODEL_KEYS:
            for f in mtot[k]:
                mtot[k][f] += mst[k][f]
        for k in ("n", "hitw", "hit1", "hitg", "hit3", "hit5", "logp", "brier", "e1", "erec", "e3", "e5", "rr",
                  "wlogp", "wexp", "hfn", "hfh", "hft3", "hflogp", "hfbrier", "hfexp",
                  "jqn", "jqhit", "jqexp", "jqlogp", "jqbrier"):
            tot[k] += ev[k]
        rate = lambda a, b: ("%.1f%%" % (a / b * 100)) if b else "—"
        hl = (ev["hflogp"] / ev["hfn"]) if ev["hfn"] else None
        hb = (ev["hfbrier"] / ev["hfn"]) if ev["hfn"] else None
        hl_col = ("#15803d" if hl > -math.log(9) else "#b45309") if hl is not None else "#94a3b8"
        hb_col = ("#15803d" if hb < 8 / 9 else "#b45309") if hb is not None else "#94a3b8"
        rows.append(f"""<tr>
  <td class="no"><a href="比分日报_{data['date']}.html">{data['date']}</a></td>
  <td>{len(data['matches'])}</td><td>{ev['n']}</td>
  <td>{ev['hitw']} <span class="badge">{rate(ev['hitw'],ev['n'])}</span></td>
  <td>{ev['hit1']} <span class="badge">{rate(ev['hit1'],ev['n'])}</span></td>
  <td>{ev['hitg']} <span class="badge">{rate(ev['hitg'],ev['n'])}</span></td>
  <td>{ev['hit3']} <span class="badge">{rate(ev['hit3'],ev['n'])}</span></td>
  <td>{ev['hit5']} <span class="badge">{rate(ev['hit5'],ev['n'])}</span></td>
  <td>{ev['hfh']} <span class="badge">{rate(ev['hfh'],ev['hfn'])}</span></td>
  <td>{ev['hft3']} <span class="badge">{rate(ev['hft3'],ev['hfn'])}</span></td>
  <td style="color:{hl_col}">{"%.3f" % hl if hl is not None else '—'}</td>
  <td style="color:{hb_col}">{"%.4f" % hb if hb is not None else '—'}</td>
  <td>{"%.3f" % (ev['logp']/ev['n']) if ev['n'] else '—'} / {math.log(1/81):.3f}</td>
  <td>{"%.4f" % (ev['brier']/ev['n']) if ev['n'] else '—'}</td></tr>""")
    n = tot["n"]
    cards = f"""
  <div class="card"><span>累计已核对</span><b>{n}</b><em>场</em></div>
  <div class="card"><span>胜负命中</span><b>{("%.1f%%" % (tot['hitw']/n*100)) if n else "—"}</b><em>{tot['hitw']}/{n}</em></div>
  <div class="card"><span>同向比分命中</span><b>{("%.1f%%" % (tot['hit1']/n*100)) if n else "—"}</b><em>{tot['hit1']}/{n}</em></div>
  <div class="card"><span>全场最高格命中</span><b>{("%.1f%%" % (tot['hitg']/n*100)) if n else "—"}</b><em>{tot['hitg']}/{n}</em></div>
  <div class="card"><span>TOP3 命中</span><b>{("%.1f%%" % (tot['hit3']/n*100)) if n else "—"}</b><em>{tot['hit3']}/{n}</em></div>
  <div class="card"><span>TOP5 命中</span><b>{("%.1f%%" % (tot['hit5']/n*100)) if n else "—"}</b><em>{tot['hit5']}/{n}</em></div>
  <div class="card"><span>总进球命中</span><b>{("%.1f%%" % (tot['jqhit']/tot['jqn']*100)) if tot['jqn'] else "—"}</b><em>{tot['jqhit']}/{tot['jqn']}</em></div>
  <div class="card"><span>总进球 log / Brier</span><b>{("%.3f" % (tot['jqlogp']/tot['jqn'])) if tot['jqn'] else "—"}</b><em>{("%.4f" % (tot['jqbrier']/tot['jqn'])) if tot['jqn'] else "—"}</em></div>
  <div class="card"><span>半全场命中</span><b>{("%.1f%%" % (tot['hfh']/tot['hfn']*100)) if tot['hfn'] else "—"}</b><em>{tot['hfh']}/{tot['hfn']}</em></div>
  <div class="card"><span>半全场前三</span><b>{("%.1f%%" % (tot['hft3']/tot['hfn']*100)) if tot['hfn'] else "—"}</b><em>{tot['hft3']}/{tot['hfn']}</em></div>
  <div class="card"><span>半全场 log / Brier</span><b>{("%.3f" % (tot['hflogp']/tot['hfn'])) if tot['hfn'] else "—"}</b><em>{("%.4f" % (tot['hfbrier']/tot['hfn'])) if tot['hfn'] else "—"}</em></div>
  <div class="card"><span>1X2 Brier</span><b>{("%.4f" % (tot['brier']/n)) if n else "—"}</b></div>"""
    mrows = []
    for k in MODEL_KEYS:
        st = mtot[k]
        if not st["n"]:
            continue
        mm = st["n"]
        lv = st["logp"] / mm
        bv = st["brier"] / mm
        mrows.append(f"""<tr><td>{MODEL_TXT[k]}</td><td>{mm}</td>
  <td>{st['hits']}</td><td>{st['hits']/mm*100:.1f}%</td><td>{st['exps']:.2f}</td>
  <td>{st['hit']}/{mm}</td>
  <td>{lv:.3f}</td>
  <td>{bv:.4f}</td></tr>""")
    model_tbl = f"""
<h2 class="h2">多模型累计校准</h2>
<p class="hint">“同向命中”是各模型在自己预测的胜负方向内挑最高比分后的命中；“最高格命中”是整张矩阵最高格（常为 1-1）的命中。
log 与 Brier 衡量整张矩阵，和挑哪一格无关；均匀盲猜基准为 log −4.394、Brier 0.9877，log 越高、Brier 越低越好。
市场、总进球只统计有对应赔率的场次。共识是 DC 30% / 泊松 10% / 市场 35% / 总进球 25% 的加权矩阵。</p>
<table><colgroup><col style="width:130px"><col style="width:76px"><col style="width:80px">
<col style="width:76px"><col style="width:80px"><col style="width:92px"><col style="width:90px"><col style="width:88px"></colgroup>
<thead><tr><th>模型</th><th>已核对</th><th>同向命中</th><th>命中率</th><th>期望命中</th><th>最高格命中</th><th>平均 log</th><th>Brier</th></tr></thead>
<tbody>{''.join(mrows)}</tbody></table>""" if mrows else ""

    def frow(name, nn, hit, exp, logp, blog, brier, bbrier):
        if not nn:
            return (f'<tr><td>{name}</td><td>—</td><td>—</td><td>—</td><td>—</td>'
                    f'<td>—</td><td>{blog:.3f}</td><td>—</td><td>{bbrier:.4f}</td></tr>')
        lc = "#15803d" if logp > blog else "#b45309"
        bc = "#15803d" if brier < bbrier else "#b45309"
        return (f'<tr><td>{name}</td><td>{nn}</td><td>{hit}</td><td>{hit/nn*100:.1f}%</td>'
                f'<td>{exp:.2f}</td><td style="color:{lc}">{logp:.3f}</td>'
                f'<td>{blog:.3f}</td><td style="color:{bc}">{brier:.4f}</td>'
                f'<td>{bbrier:.4f}</td></tr>')

    four_rows = [
        frow("胜平负", n, tot["hitw"], tot["wexp"],
             (tot["wlogp"] / n if n else 0), -math.log(3),
             (tot["brier"] / n if n else 0), 2 / 3),
        frow("比分", n, tot["hit1"], tot["erec"],
             (mtot["ens"]["logp"] / mtot["ens"]["n"] if mtot["ens"]["n"] else 0), math.log(1 / 81),
             (mtot["ens"]["brier"] / mtot["ens"]["n"] if mtot["ens"]["n"] else 0), 80 / 81),
        frow("总进球", tot["jqn"], tot["jqhit"], tot["jqexp"],
             (tot["jqlogp"] / tot["jqn"] if tot["jqn"] else 0), -math.log(8),
             (tot["jqbrier"] / tot["jqn"] if tot["jqn"] else 0), 7 / 8),
        frow("半全场", tot["hfn"], tot["hfh"], tot["hfexp"],
             (tot["hflogp"] / tot["hfn"] if tot["hfn"] else 0), -math.log(9),
             (tot["hfbrier"] / tot["hfn"] if tot["hfn"] else 0), 8 / 9),
    ]
    four_tbl = f"""
<h2 class="h2">四玩法累计校准</h2>
<p class="hint">胜平负取三个方向里概率最高的一档；比分“首选”用同向比分（预测胜负方向内的最高比分），log / Brier 按整张 9×9 矩阵计；
总进球取 0—7+ 八档里概率最高的一档；半全场取九档里概率最高的一档。绿字为优于均匀盲猜、橙字为不如盲猜。</p>
<table><colgroup><col style="width:110px"><col style="width:66px"><col style="width:76px">
<col style="width:66px"><col style="width:74px"><col style="width:78px"><col style="width:74px">
<col style="width:74px"><col style="width:80px"></colgroup>
<thead><tr><th>玩法</th><th>已核对</th><th>首选命中</th><th>命中率</th><th>期望命中</th>
<th>平均 log</th><th>盲猜 log</th><th>Brier</th><th>盲猜 Brier</th></tr></thead>
<tbody>{''.join(four_rows)}</tbody></table>"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>竞彩比分助手 · 累计校准</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><div><h1>累计校准汇总</h1>
  <div class="sub">逐日核对比分、胜负、总进球与半全场；样本越多，命中率越接近模型给出的期望值</div></div>
  <div class="stamp"><b>{len([d for d in days if not d.get('late')])} 个比赛日</b><br>{now_str()}</div></header>
<div class="cards">{cards}</div>
<table><colgroup><col style="width:104px"><col style="width:50px"><col style="width:60px">
<col style="width:88px"><col style="width:88px"><col style="width:88px"><col style="width:86px"><col style="width:86px"><col style="width:88px">
<col style="width:86px"><col style="width:78px"><col style="width:84px"><col style="width:140px"><col style="width:76px"></colgroup>
<thead><tr><th>日期</th><th>场次</th><th>已核对</th><th>胜负命中</th><th>同向比分</th>
<th>全场最高</th><th>TOP3 命中</th><th>TOP5 命中</th><th>半全场命中</th><th>半全场前三</th><th>半全场 log</th><th>半全场 Brier</th>
<th>平均 log 概率 / 均匀盲猜</th><th>Brier</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="14" style="padding:24px;color:#94a3b8">暂无记录</td></tr>'}</tbody></table>
{four_tbl}
{model_tbl}
<footer>
<b>基准值：</b>整张 9×9 比分矩阵均匀盲猜（81 格各 1.23%）的平均 log 为 −4.394、Brier 为 0.9877；表内 log 在 −3.1 左右、Brier 在 0.93—0.95，已经好于盲猜。−2.137 是 1-1 单格（11.8%）的 log 价格，不是整张矩阵的平均 log 基准，两者不能直接对比。胜负 Brier 分数 0.60 上下属于典型水平。<br>
<b>比分口径：</b>“同向比分”是限定在预测胜负方向内的最高比分；“全场最高”是整张矩阵的最高格，常为 1-1，但单格通常只有 10%—14%。两个命中率不要混着看。<br>
<b>多模型：</b>“同向命中”按各模型自己的胜负方向取比分；“最高格命中”取整张矩阵最高格。市场比分与总进球两个口径只统计有对应赔率的场次，与 DC/泊松的样本数可能不同；比较 log、Brier 时先看样本数。<br>
<b>半全场基准：</b>九档均匀猜的平均 log 约 −2.197、Brier 0.889；log 越高（越接近 0）越好，Brier 越低越好。绿字表示优于盲猜，橙字表示还不如盲猜。半全场命中只看第一档；半全场前三看实际结果是否落在概率最高的三档里，更接近“九种组合都在算”的真实用法。<br>
<b>四玩法基准：</b>胜平负三档盲猜 log −1.099、Brier 0.667；比分 81 格盲猜 log −4.394、Brier 0.9877；总进球八档盲猜 log −2.079、Brier 0.875；半全场九档盲猜 log −2.197、Brier 0.889。比分行的 log / Brier 按整张矩阵计，命中按同向比分计。<br>
<b>命中率参考：</b>同向比分 7%—12%，全场最高格 10%—14%，TOP3 28%—34%，TOP5 42%—50%，胜负 50%—55%，半全场首选 20%—35%，半全场前三 65%—75%。半全场单格是 9 选 1，天然只有三成左右；191 场里模型≥42% 的高把握档单关命中 55.8%（24/43），≥50% 档 65%（13/20），所以低把握场次不再串关。低于或高于都是正常波动，样本到 200 场以上才有统计意义。覆盖口径的检验见 <a href="比分口径回测.html" style="color:#1f3b63">比分口径回测</a>。<br>
<b>声明：</b>只做概率与校准记录，不构成任何投注建议。
</footer></div></body></html>"""


def load_all():
    days = []
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        # 数据/ 里还有串关模拟.json 等非比赛日文件，只收比赛日结构
        if isinstance(d, dict) and d.get("date") and isinstance(d.get("matches"), list):
            days.append(d)
    return days


def prepare_model():
    """读取 ρ 校准结果（由 --backtest 刷新写盘）；没有校准文件时用默认值。"""
    global RHO
    rec = load_rho_file()
    if rec:
        RHO = float(rec["rho"])
        log("模型：Dixon-Coles，ρ=%.2f（%d 场集成分布最优 %+.2f，DC 单源 %+.2f，校准于 %s）" % (
            RHO, int(rec.get("n", 0)), float(rec.get("hat", RHO)),
            float(rec.get("hat_dc", RHO)), str(rec.get("at", ""))[:10]))
    else:
        RHO = DEFAULT_RHO
        log("模型：Dixon-Coles，ρ=%.2f（尚未用历史样本校准，跑一次 --backtest 自动生成）" % RHO)
    return RHO


def write_reports(day=None):
    ensure_dirs()
    days = load_all()
    if not days:
        raise RuntimeError("还没有任何记录")
    targets = [d for d in days if d["date"] == day] if day else days
    if not targets:
        # 当天还没抓过（例如 11:00 前点了“生成报告”）：退到最近一个有记录的比赛日
        log("没有 %s 的记录，改生成最近一个比赛日" % day)
        targets = sorted(days, key=lambda d: d["date"])[-1:]
    for d in targets:
        out = REPORT_DIR / ("比分日报_%s.html" % d["date"])
        out.write_text(render_day(d), encoding="utf-8")
        log("报告：%s" % out)
    idx = REPORT_DIR / "汇总.html"
    idx.write_text(render_index(days), encoding="utf-8")
    log("汇总：%s" % idx)
    return REPORT_DIR / ("比分日报_%s.html" % (targets[-1]["date"]))


def open_path(p):
    try:
        subprocess.Popen(["open", str(p)])
    except Exception as e:
        log("打开失败：%s" % e)


# ---------------------------------------------------------------- 四串一模拟
PARLAY_FILE = DATA_DIR / "串关模拟.json"
PARLAY_HTML = REPORT_DIR / "串关模拟.html"
PARLAY_STAKE = 100.0


def load_parlays():
    if not PARLAY_FILE.exists():
        return []
    try:
        return json.loads(PARLAY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_parlays(bets):
    ensure_dirs()
    tmp = PARLAY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(bets, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PARLAY_FILE)


def system_combos(bet):
    """4 串 11 = 6 个 2 串 1 + 4 个 3 串 1 + 1 个 4 串 1。"""
    legs = bet["legs"]
    out = []
    for r in range(2, len(legs) + 1):
        for idx in itertools.combinations(range(len(legs)), r):
            odds = 1.0
            for i in idx:
                odds *= legs[i]["odds"]
            out.append({"idx": idx, "r": r, "odds": odds})
    return out


def combo_state(bet, c):
    """('win', 返还) / ('dead', 0) / ('pending', None)。"""
    legs = bet["legs"]
    scored_all = True
    for i in c["idx"]:
        got = split_score(legs[i].get("score", ""))
        if not got:
            scored_all = False
            continue
        if outcome_of(*got) != legs[i]["k"]:
            return "dead", 0.0
    if scored_all:
        return "win", bet["unit"] * c["odds"]
    return "pending", None


def settle_parlays(quiet=False):
    """有比分就开奖：单关任一腿错即“未中”；4 串 11 至少两腿对才有返还。只记账，不动钱。"""
    bets = load_parlays()
    changed = False
    for b in bets:
        if b.get("status") != "待开奖":
            continue
        data = load_day(b["day"])
        if not data:
            continue
        smap = {m["no"]: m.get("score", "") for m in data["matches"]}
        done, misses = 0, 0
        for leg in b["legs"]:
            sc = smap.get(leg["no"], "")
            leg["score"] = sc
            got = split_score(sc)
            if not got:
                continue
            done += 1
            if outcome_of(*got) != leg["k"]:
                misses += 1
        if b.get("system"):
            # 差到只剩 1 腿能对就不可能中奖了，可以提前判死
            settle_now = done == len(b["legs"]) or misses > len(b["legs"]) - 2
            if not settle_now:
                continue
            payout = sum(pay for c in system_combos(b)
                         for st, pay in [combo_state(b, c)] if st == "win")
            b["payout"] = round(payout, 2)
            b["status"] = "中奖" if payout > 0 else "未中"
        else:
            if not (misses or done == len(b["legs"])):
                continue
            b["status"] = "未中" if misses else "中"
            b["payout"] = 0.0 if misses else round(b["stake"] * b["odds"], 2)
        b["settled"] = now_str()
        changed = True
        if not quiet:
            log("串关 %s：%s，返还 %.2f 元" % (b["id"], b["status"], b["payout"]))
    if changed:
        save_parlays(bets)
    return changed


def make_parlay(day, stake=PARLAY_STAKE, n=4, system=None, unit=2.0):
    """按模型概率取当天最高的 n 场组成 n 串 1（同概率按开球时间）。

    system=11 时记 4 串 11：每注 unit 元、共 11 注，本金 = unit × 11。
    """
    data = load_day(day)
    if not data:
        raise RuntimeError("%s 还没有封存记录，先跑一次 --daily" % day)
    cand = [m for m in data["matches"]
            if m.get("probs") and len(m.get("odds") or []) == 3
            and all(o and o > 1.0 for o in m["odds"])]
    if len(cand) < n:
        raise RuntimeError("当天只有 %d 场可用胜平负，凑不满 %d 串 1" % (len(cand), n))
    cand.sort(key=lambda m: (-max(m["probs"]), m.get("kickoff", "")))
    legs = []
    for m in cand[:n]:
        k = max(range(3), key=lambda i: m["probs"][i])
        legs.append({"no": m["no"], "match": "%s-%s" % (m["home"], m["away"]),
                     "pick": ["主胜", "平局", "客胜"][k], "k": k,
                     "p": round(m["probs"][k], 4), "odds": m["odds"][k],
                     "kickoff": m.get("kickoff", ""), "score_pred": m.get("pick_score", ""),
                     "score": ""})
    odds, prob = 1.0, 1.0
    for leg in legs:
        odds *= leg["odds"]
        prob *= leg["p"]
    bets = load_parlays()
    bet = {"id": "P%s-%02d" % (day.replace("-", ""), len(bets) + 1), "day": day,
           "created": now_str(), "play": "胜平负 %d 串 1" % n, "legs": legs,
           "odds": round(odds, 4), "p": round(prob, 6), "stake": float(stake),
           "status": "待开奖", "payout": None, "settled": ""}
    if system == 11 and n == 4:
        bet["system"] = 11
        bet["unit"] = float(unit)
        bet["stake"] = round(unit * 11, 2)
        bet["play"] = "胜平负 4 串 11（6×2串1 + 4×3串1 + 1×4串1）"
        bet["id"] = "S%s-%02d" % (day.replace("-", ""),
                                  len([x for x in bets if x.get("system")]) + 1)
        # 最高返还（四腿全中）与模型期望
        combos = system_combos(bet)
        bet["max_return"] = round(sum(unit * c["odds"] for c in combos), 2)
        q = [leg["p"] * leg["odds"] for leg in legs]
        ev = 0.0
        for c in combos:
            prod = unit
            for i in c["idx"]:
                prod *= q[i]
            ev += prod
        bet["ev"] = round(ev / bet["stake"] - 1, 4)
    bets.append(bet)
    save_parlays(bets)
    return bet


def render_parlays(bets=None):
    bets = load_parlays() if bets is None else bets
    settled = [b for b in bets if b.get("status") in ("中", "中奖", "未中")]
    pnl = sum((b.get("payout") or 0) - b["stake"] for b in settled)
    cur = bets[-1] if bets else None

    def leg_rows(b):
        out = []
        for leg in b["legs"]:
            got = split_score(leg.get("score", ""))
            if not got:
                act, td_cls, bd_cls = "待赛", "act", "badge"
            elif outcome_of(*got) == leg["k"]:
                act, td_cls, bd_cls = "命中", "act hit", "badge ok"
            else:
                act, td_cls, bd_cls = "未中", "act miss", "badge no"
            out.append(f"""<tr>
  <td class="no">{H.escape(leg['no'])}</td><td class="teams">{H.escape(leg['match'])}</td>
  <td><span class="pill">{H.escape(leg['pick'])}<b>{leg['p']*100:.1f}%</b></span></td>
  <td>{leg['odds']:.2f}</td><td>{H.escape(leg.get('score_pred') or '—')}</td>
  <td class="score">{H.escape(leg.get('score') or '—')}</td>
  <td class="{td_cls}"><span class="{bd_cls}">{act}</span></td></tr>""")
        return "".join(out)

    def sys_rows(b):
        out = []
        for c in system_combos(b):
            st, pay = combo_state(b, c)
            bd, text = {"win": ("badge ok", "中"), "dead": ("badge no", "未中"),
                        "pending": ("badge", "待赛")}[st]
            label = " + ".join(b["legs"][i]["no"] for i in c["idx"])
            out.append(f"""<tr><td>{c['r']} 串 1</td><td class="teams">{label}</td>
  <td>{c['odds']:.2f}</td><td>{('%s' % format(pay, '.2f')) if pay is not None else '—'}</td>
  <td class="act"><span class="{bd}">{text}</span></td></tr>""")
        return "".join(out)

    def locked(b):
        if not b.get("system"):
            return b.get("payout") or 0.0
        return sum(pay for c in system_combos(b)
                   for st, pay in [combo_state(b, c)] if st == "win")

    hist = "".join(f"""<tr>
  <td class="no">{b['id']}</td><td>{b['day']}<br><span class="badge">{'4串11' if b.get('system') else '4串1'}</span></td>
  <td class="teams">{H.escape('、'.join(l['no'] for l in b['legs']))}</td>
  <td>{b['odds']:.2f}</td><td>{b['p']*100:.1f}%</td>
  <td>{b['stake']:.0f}</td>
  <td class="act {'hit' if b['status'] in ('中', '中奖') else ('miss' if b['status']=='未中' else '')}">{b['status']}</td>
  <td>{('%.2f' % b['payout']) if b.get('payout') is not None else '—'}</td>
  <td>{('%+.2f' % (b['payout']-b['stake'])) if b.get('payout') is not None else '—'}</td>
</tr>""" for b in reversed(bets))

    if cur:
        lk = locked(cur)
        if cur.get("system"):
            cards = f"""
  <div class="card"><span>玩法</span><b>4 串 11</b><em>11 注</em></div>
  <div class="card"><span>单注 / 合计</span><b>{cur['unit']:.0f} / {cur['stake']:.0f}</b><em>元</em></div>
  <div class="card"><span>四腿全中返还</span><b>{cur['max_return']:.2f}</b><em>元</em></div>
  <div class="card"><span>已锁定返还</span><b>{lk:.2f}</b><em>元</em></div>
  <div class="card"><span>模型期望</span><b>{cur['ev']*100:+.1f}%</b></div>
  <div class="card"><span>状态</span><b>{cur['status']}</b></div>"""
        else:
            ev = cur["p"] * cur["odds"] - 1
            cards = f"""
  <div class="card"><span>串关赔率</span><b>{cur['odds']:.2f}</b><em>倍</em></div>
  <div class="card"><span>模型联合概率</span><b>{cur['p']*100:.1f}%</b></div>
  <div class="card"><span>模拟本金</span><b>{cur['stake']:.0f}</b><em>元</em></div>
  <div class="card"><span>期望回报</span><b>{ev*100:+.1f}%</b></div>
  <div class="card"><span>状态</span><b>{cur['status']}</b><em>{cur['play']}</em></div>"""
        body = f"""
<h2 style="font-size:17px;margin:26px 0 10px">当前记录 · {cur['id']}</h2>
<table><colgroup><col style="width:110px"><col><col style="width:130px">
<col style="width:80px"><col style="width:90px"><col style="width:100px"><col style="width:90px"></colgroup>
<thead><tr><th>场次</th><th>对阵</th><th>选择</th><th>赔率</th><th>预测比分</th><th>实际比分</th><th>结果</th></tr></thead>
<tbody>{leg_rows(cur)}</tbody></table>
<p class="sub" style="margin-top:10px">建单 {cur['created']} · 取当天模型概率最高的 {len(cur['legs'])} 场。</p>"""
        if cur.get("system"):
            body += f"""
<h2 style="font-size:17px;margin:26px 0 10px">11 注明细</h2>
<table><colgroup><col style="width:110px"><col><col style="width:100px">
<col style="width:110px"><col style="width:90px"></colgroup>
<thead><tr><th>类型</th><th>组合</th><th>组合赔率</th><th>返还（元）</th><th>结果</th></tr></thead>
<tbody>{sys_rows(cur)}</tbody></table>
<p class="sub" style="margin-top:10px">4 串 11 = 6 个 2 串 1 + 4 个 3 串 1 + 1 个 4 串 1，每注 {cur['unit']:.0f} 元。
中几场就结算对应的子注：中 2 场只有 1 个 2 串 1，中 3 场是 3 个 2 串 1 + 1 个 3 串 1。</p>"""
    else:
        cards = ""
        body = '<p class="sub">还没有模拟记录。运行 --parlay 生成。</p>'

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>串关模拟记录</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><div><h1>串关模拟记录</h1>
  <div class="sub">按模型概率排序取前四场；只记账，不投注，本金为虚拟金额</div></div>
  <div class="stamp"><b>累计盈亏 {pnl:+.2f} 元</b><br>{now_str()}</div></header>
<div class="cards">{cards}</div>{body}
<h2 style="font-size:17px;margin:28px 0 10px">历史</h2>
<table><colgroup><col style="width:130px"><col style="width:110px"><col><col style="width:80px">
<col style="width:90px"><col style="width:70px"><col style="width:80px"><col style="width:90px"><col style="width:90px"></colgroup>
<thead><tr><th>编号</th><th>比赛日</th><th>场次</th><th>赔率</th><th>联合概率</th><th>本金</th>
<th>结果</th><th>返还</th><th>盈亏</th></tr></thead>
<tbody>{hist or '<tr><td colspan="9" style="padding:24px;color:#94a3b8">暂无记录</td></tr>'}</tbody></table>
<footer><b>口径：</b>“按模型排序”= 当天模型概率最高的四场胜平负，同概率按开球时间。
4 串 1 要四腿全中；4 串 11 中两场就有返还，但两场只结算一个 2 串 1，通常远低于本金。<br>
<b>声明：</b>纯模拟记账，不构成任何投注建议。</footer></div></body></html>"""
    ensure_dirs()
    PARLAY_HTML.write_text(doc, encoding="utf-8")
    return PARLAY_HTML


def report_of(day):
    p = REPORT_DIR / ("比分日报_%s.html" % day)
    return p if p.exists() else None


# ---------------------------------------------------------------- 每日流程
def daily(day=None, do_open=False):
    ensure_dirs()
    day = day or today_str()
    log("=== 每日流程 %s ===" % day)
    prepare_model()
    data = None
    for attempt in range(2):
        try:
            data = ensure_day(day)
            break
        except Exception as e:
            if attempt == 0:
                log("抓取失败：%s；90 秒后自动重试（11:00 前后站点会切换销售列表）" % e)
                time.sleep(90)
            else:
                log("抓取仍失败：%s；14:00 / 18:30 的自动任务会再试" % e)
                raise
    top_up_day(data)
    ds = refresh_results(10)
    if PARLAY_FILE.exists() and settle_parlays():
        render_parlays()
    data = load_day(day)
    ev = evaluate(data)
    p = write_reports(day)
    try:
        import upset_core
        up = upset_core.build(day)
        log("爆冷雷达：%s" % up)
    except Exception as e:  # 爆冷页失败不影响主流程
        log("爆冷雷达生成失败：%s" % e)
    if do_open:
        open_path(p)
    log("完成：%d 场，已核对 %d 场，胜负 %d，比分 %d；更新比赛日 %d 个" % (
        len(data["matches"]), ev["n"], ev["hitw"], ev["hit1"], len(ds)))
    if do_open:
        open_path(p)
    return ev


# ---------------------------------------------------------------- GUI
def run_gui(start_day=None, selftest=False, tab=None):
    import calendar
    import queue
    import tkinter as tk
    from tkinter import ttk, messagebox
    global LOG_SINK

    BG, CARD, INK, MUTE, ACCENT = "#f5f7fa", "#ffffff", "#1e293b", "#64748b", "#1f3b63"

    class App(tk.Tk):
        def __init__(self, day=None, selftest=False, tab=None):
            global LOG_SINK
            super().__init__()
            self.title("竞彩比分助手")
            self.geometry("1250x800")
            self.minsize(1100, 680)
            self.configure(bg=BG)
            self.start_day = day or today_str()
            self.q = queue.Queue()
            self.busy = False
            self._style()
            self._build()
            LOG_SINK = self.push
            self.after(150, self._drain)
            self.reload(self.start_day)
            if tab is not None:
                self.nb.select(tab)
            if selftest:
                self.after(700, self.act_update)
                self.after(4200, lambda: self.nb.select(2))

        def _style(self):
            s = ttk.Style(self)
            s.theme_use("clam")
            s.configure(".", background=BG, foreground=INK, font=("PingFang SC", 13))
            s.configure("TFrame", background=BG)
            s.configure("Card.TFrame", background=CARD, relief="flat")
            s.configure("TLabel", background=BG, foreground=INK)
            s.configure("H1.TLabel", font=("PingFang SC", 22, "bold"), foreground=ACCENT)
            s.configure("Sub.TLabel", font=("PingFang SC", 12), foreground=MUTE)
            s.configure("CardTitle.TLabel", font=("PingFang SC", 11), foreground=MUTE, background=CARD)
            s.configure("CardNum.TLabel", font=("PingFang SC", 22, "bold"), foreground=ACCENT, background=CARD)
            s.configure("TButton", padding=(14, 7), font=("PingFang SC", 13))
            s.configure("Accent.TButton", background=ACCENT, foreground="white",
                        borderwidth=0, focuscolor=ACCENT)
            s.map("Accent.TButton", background=[("active", "#2c5488"), ("disabled", "#94a3b8")])
            s.configure("Bar.TButton", padding=(9, 5), font=("PingFang SC", 12))
            s.configure("BarAccent.TButton", padding=(11, 5), font=("PingFang SC", 12),
                        background=ACCENT, foreground="white", borderwidth=0, focuscolor=ACCENT)
            s.map("BarAccent.TButton", background=[("active", "#2c5488"), ("disabled", "#94a3b8")])
            s.configure("TNotebook", background=BG, borderwidth=0)
            s.configure("TNotebook.Tab", padding=(18, 8), font=("PingFang SC", 13))
            s.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)])
            s.configure("Treeview", background=CARD, fieldbackground=CARD, rowheight=28,
                        font=("PingFang SC", 12), borderwidth=0)
            s.configure("Four.Treeview", background=CARD, fieldbackground=CARD, rowheight=28,
                        font=("PingFang SC", 11), borderwidth=0)
            s.configure("Treeview.Heading", font=("PingFang SC", 12, "bold"),
                        background="#eef2f7", foreground="#334155", padding=(6, 6))
            s.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", "#0f172a")])

        def _build(self):
            top = ttk.Frame(self, padding=(22, 18, 22, 8))
            top.pack(fill="x")
            ttk.Label(top, text="竞彩比分助手", style="H1.TLabel").pack(side="left")
            ttk.Label(top, text="  抓取 → 预测 → 赛后核对 → 日报", style="Sub.TLabel").pack(side="left", pady=(8, 0))
            self.stamp = ttk.Label(top, text="", style="Sub.TLabel")
            self.stamp.pack(side="right", pady=(8, 0))
            self.state = ttk.Label(top, text="就绪", style="Sub.TLabel")
            self.state.pack(side="right", padx=(0, 16), pady=(8, 0))
            self.spin = ttk.Progressbar(top, mode="indeterminate", length=72)

            bar = ttk.Frame(self, padding=(22, 0, 22, 8))
            bar.pack(fill="x")
            ttk.Label(bar, text="比赛日").pack(side="left")
            self.day = tk.StringVar(value=self.start_day)
            e = ttk.Entry(bar, textvariable=self.day, width=11, font=("PingFang SC", 13))
            e.pack(side="left", padx=(6, 3))
            e.bind("<Return>", lambda _e: self.reload(self.day.get().strip()))
            ttk.Button(bar, text="◀", width=2, style="Bar.TButton",
                       command=lambda: self.shift_day(-1)).pack(side="left", padx=1)
            ttk.Button(bar, text="▶", width=2, style="Bar.TButton",
                       command=lambda: self.shift_day(1)).pack(side="left", padx=(1, 3))
            ttk.Button(bar, text="选日期", style="Bar.TButton",
                       command=self.open_picker).pack(side="left", padx=(0, 10))
            self.btn_update = ttk.Button(bar, text="一键更新", style="BarAccent.TButton",
                                         command=self.act_update)
            self.btn_update.pack(side="left", padx=(0, 4))
            ttk.Button(bar, text="核对赛果", style="Bar.TButton",
                       command=self.act_verify).pack(side="left", padx=3)
            ttk.Button(bar, text="生成报告", style="Bar.TButton",
                       command=self.act_report).pack(side="left", padx=3)
            ttk.Button(bar, text="打开报告", style="Bar.TButton",
                       command=self.act_open).pack(side="left", padx=3)
            ttk.Button(bar, text="数据文件夹", style="Bar.TButton",
                       command=lambda: open_path(BASE)).pack(side="left", padx=3)

            self.cards = ttk.Frame(self, padding=(22, 4, 22, 10))
            self.cards.pack(fill="x")

            nb = ttk.Notebook(self)
            nb.pack(fill="both", expand=True, padx=22, pady=(0, 16))
            self.nb = nb

            # 今日推荐（首页）
            f0 = ttk.Frame(nb, padding=8)
            nb.add(f0, text=" 今日推荐 ")
            bar0 = ttk.Frame(f0)
            bar0.pack(fill="x", pady=(0, 8))
            self.rec_hint = ttk.Label(bar0, text="", style="Sub.TLabel",
                                      wraplength=760, justify="left")
            self.rec_hint.pack(side="left")
            self.btn_rec = ttk.Button(bar0, text="刷新推荐", style="BarAccent.TButton",
                                      command=self.act_rec_refresh)
            self.btn_rec.pack(side="right", padx=(6, 0))
            ttk.Button(bar0, text="打开推荐页", style="Bar.TButton",
                       command=self.act_rec_open).pack(side="right", padx=4)
            ttk.Button(bar0, text="当日全解析", style="Bar.TButton",
                       command=self.act_analysis_open).pack(side="right", padx=4)
            wrap0 = ttk.Frame(f0)
            wrap0.pack(fill="both", expand=True)
            self.rec_canvas = tk.Canvas(wrap0, bg=BG, highlightthickness=0)
            vs0 = ttk.Scrollbar(wrap0, orient="vertical", command=self.rec_canvas.yview)
            self.rec_canvas.configure(yscrollcommand=vs0.set)
            self.rec_canvas.pack(side="left", fill="both", expand=True)
            vs0.pack(side="right", fill="y")
            self.rec_body = ttk.Frame(self.rec_canvas)
            self.rec_win = self.rec_canvas.create_window(
                (0, 0), window=self.rec_body, anchor="nw")
            self.rec_body.bind(
                "<Configure>",
                lambda _e: self.rec_canvas.configure(
                    scrollregion=self.rec_canvas.bbox("all")))
            self.rec_canvas.bind(
                "<Configure>",
                lambda e: self.rec_canvas.itemconfigure(self.rec_win, width=e.width))

            def _wheel(e):
                self.rec_canvas.yview_scroll(int(-1 * (e.delta / 60)), "units")

            self.rec_canvas.bind("<Enter>", lambda _e: self.rec_canvas.bind_all("<MouseWheel>", _wheel))
            self.rec_canvas.bind("<Leave>", lambda _e: self.rec_canvas.unbind_all("<MouseWheel>"))
            self._rec_auto = set()
            nb.bind("<<NotebookTabChanged>>", self._on_tab)

            # 当日
            f1 = ttk.Frame(nb, padding=8)
            nb.add(f1, text=" 当日预测 ")
            self.day_note = ttk.Label(
                f1, style="Sub.TLabel", wraplength=1120, justify="left",
                text=("四玩法预测：胜平负=共识胜负首选；比分=同向最高格（单格通常仅 8%—12%，"
                      "另看“净胜球前二”与“前三覆盖”）；总进球=0—7+首选；半全场=9 档首选。"
                      "绿底=四玩法全中，橙底=命中三项以上；完整赔率与去水概率见“四玩法赔率”页。"))
            self.day_note.pack(anchor="w", pady=(0, 6))
            self.rc_note = ttk.Label(f1, style="Sub.TLabel", foreground=ACCENT, cursor="hand2")
            self.rc_note.pack(anchor="w", pady=(0, 6))
            self.rc_note.bind("<Button-1>", lambda _e: self.act_recheck_open())
            cols = [("no", "场次", 64), ("teams", "对阵", 162),
                    ("kick", "开球", 88), ("wdl", "胜平负", 94),
                    ("bf", "比分·覆盖", 118), ("mg", "净胜球前二", 112),
                    ("jq", "总进球", 84), ("hf", "半全场", 94), ("act", "实际", 132),
                    ("hit", "核对", 118)]
            wrap = ttk.Frame(f1)
            wrap.pack(fill="both", expand=True)
            self.tv = ttk.Treeview(wrap, columns=[c[0] for c in cols], show="headings",
                                   style="Four.Treeview")
            for k, t, w in cols:
                self.tv.heading(k, text=t)
                self.tv.column(k, width=w, minwidth=48, stretch=True,
                               anchor="w" if k in ("teams", "top3", "act") else "center")
            ys = ttk.Scrollbar(wrap, orient="vertical", command=self.tv.yview)
            xs = ttk.Scrollbar(wrap, orient="horizontal", command=self.tv.xview)
            self.tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            self.tv.grid(row=0, column=0, sticky="nsew")
            ys.grid(row=0, column=1, sticky="ns")
            xs.grid(row=1, column=0, sticky="ew")
            wrap.rowconfigure(0, weight=1)
            wrap.columnconfigure(0, weight=1)
            self.tv.tag_configure("hit", background="#eaf7ee")
            self.tv.tag_configure("top3", background="#fffaf0")
            self.tv.tag_configure("part", background="#fffaf0")
            self.tv.tag_configure("miss", background="#fdf1f1")
            self.tv.tag_configure("odd", background="#fbfcfe")

            # 半全场
            f4 = ttk.Frame(nb, padding=8)
            nb.add(f4, text=" 半全场 ")
            self.hf_note = ttk.Label(f4, text="", style="Sub.TLabel")
            self.hf_note.pack(anchor="w", pady=(0, 6))
            hfwrap = ttk.Frame(f4)
            hfwrap.pack(fill="both", expand=True)
            hfwrap.rowconfigure(0, weight=1)
            hfwrap.columnconfigure(0, weight=1)
            left4 = ttk.Frame(hfwrap)
            left4.grid(row=0, column=0, sticky="nsew")
            cols4 = [("no", "场次", 66), ("teams", "对阵", 220), ("kick", "开球", 100),
                     ("pick", "首选", 78), ("pp", "模型", 56), ("mq", "市场", 56),
                     ("dv", "差", 44), ("second", "前三（2/3）", 240), ("act", "实际", 60),
                     ("ok", "核对", 50)]
            self.tv4 = ttk.Treeview(left4, columns=[c[0] for c in cols4], show="headings")
            for k, t, w in cols4:
                self.tv4.heading(k, text=t)
                self.tv4.column(k, width=w, minwidth=40, stretch=(k == "teams"),
                               anchor="w" if k == "teams" else "center")
            y4 = ttk.Scrollbar(left4, orient="vertical", command=self.tv4.yview)
            x4 = ttk.Scrollbar(left4, orient="horizontal", command=self.tv4.xview)
            self.tv4.configure(yscrollcommand=y4.set, xscrollcommand=x4.set)
            self.tv4.grid(row=0, column=0, sticky="nsew")
            y4.grid(row=0, column=1, sticky="ns")
            x4.grid(row=1, column=0, sticky="ew")
            left4.rowconfigure(0, weight=1)
            left4.columnconfigure(0, weight=1)
            self.tv4.tag_configure("hit", background="#eaf7ee")
            self.tv4.tag_configure("miss", background="#fdf1f1")
            self.tv4.tag_configure("odd", background="#fbfcfe")
            self.tv4.bind("<<TreeviewSelect>>", self._hf_detail)
            self.hf_map = {}

            side = tk.Frame(f4, bg="#ffffff",
                            highlightthickness=1, highlightbackground="#e2e8f0")
            side.pack(fill="x", pady=(0, 8), before=hfwrap)
            info = tk.Frame(side, bg="#ffffff")
            info.pack(side="left", fill="both", expand=True)
            self.hf_title = tk.Label(info, text="半全场分布", font=("PingFang SC", 13, "bold"),
                                     bg="#ffffff", fg="#1e293b", anchor="w", justify="left",
                                     wraplength=780)
            self.hf_title.pack(fill="x", padx=14, pady=(8, 1))
            self.hf_sub = tk.Label(info, text="选中左侧一场比赛查看九宫格",
                                   font=("PingFang SC", 11), bg="#ffffff", fg="#64748b",
                                   anchor="w", justify="left", wraplength=780)
            self.hf_sub.pack(fill="x", padx=14)
            self.hf_tip = tk.Label(
                info, text="行=半场结果，列=全场结果。①②③ 标出概率最高三档。上半场占 45% 进球，上下半场各用 DC 矩阵修正低比分，9 种组合全部计算；市场列由 9 档赔率 Shin 去水。",
                font=("PingFang SC", 10), bg="#ffffff", fg="#94a3b8",
                anchor="w", justify="left", wraplength=780)
            self.hf_tip.pack(fill="x", padx=14, pady=(4, 7))
            gridf = tk.Frame(side, bg="#ffffff")
            gridf.pack(side="right", padx=12, pady=6)
            tk.Label(gridf, text="半\\全", font=("PingFang SC", 10), bg="#ffffff",
                     fg="#94a3b8").grid(row=0, column=0, padx=1, pady=1)
            for c, t in enumerate(["主胜", "平局", "客胜"]):
                tk.Label(gridf, text=t, width=6, font=("PingFang SC", 10), bg="#ffffff",
                         fg="#64748b").grid(row=0, column=c + 1, padx=1, pady=1)
            self.hf_cells = []
            for r, t in enumerate(["主胜", "平局", "客胜"]):
                tk.Label(gridf, text=t, width=4, font=("PingFang SC", 10), bg="#ffffff",
                         fg="#64748b").grid(row=r + 1, column=0, padx=1, pady=1)
                row_cells = []
                for c in range(3):
                    lab = tk.Label(gridf, text="—", width=6, height=1,
                                   font=("PingFang SC", 11, "bold"), bg="#f8fafc",
                                   fg="#cbd5e1", relief="flat", bd=0, highlightthickness=0)
                    lab.grid(row=r + 1, column=c + 1, padx=1, pady=1)
                    row_cells.append(lab)
                self.hf_cells.append(row_cells)

            # 四玩法赔率
            f6 = ttk.Frame(nb, padding=8)
            nb.add(f6, text=" 四玩法赔率 ")
            self.four_note = ttk.Label(f6, text="", style="Sub.TLabel",
                                       wraplength=1120, justify="left")
            self.four_note.pack(anchor="w", pady=(0, 6))
            cols6 = [("no", "场次", 68), ("teams", "对阵", 180), ("kick", "开球", 94),
                     ("wdl", "胜平负", 130), ("bf", "比分", 140), ("jq", "总进球", 116),
                     ("hf", "半全场", 136), ("act", "实际", 136), ("ok", "核对", 142)]
            wrap6 = ttk.Frame(f6)
            wrap6.pack(fill="x")
            self.tv6 = ttk.Treeview(wrap6, columns=[c[0] for c in cols6],
                                    show="headings", height=9, style="Four.Treeview")
            for k, t, w in cols6:
                self.tv6.heading(k, text=t)
                self.tv6.column(k, width=w, minwidth=50, stretch=(k == "teams"),
                                anchor="w" if k in ("teams", "act") else "center")
            y6 = ttk.Scrollbar(wrap6, orient="vertical", command=self.tv6.yview)
            self.tv6.configure(yscrollcommand=y6.set)
            self.tv6.pack(side="left", fill="x", expand=True)
            y6.pack(side="right", fill="y")
            self.tv6.tag_configure("hit", background="#eaf7ee")
            self.tv6.tag_configure("miss", background="#fdf1f1")
            self.tv6.tag_configure("odd", background="#fbfcfe")
            self.tv6.bind("<<TreeviewSelect>>", self._four_detail)
            self.four_map = {}
            self.four_title = ttk.Label(f6, text="四玩法赔率明细", style="Sub.TLabel")
            self.four_title.pack(anchor="w", pady=(8, 2))
            box = tk.Frame(f6, bg="#ffffff", highlightthickness=1,
                           highlightbackground="#e2e8f0")
            box.pack(fill="both", expand=True)
            self.four_txt = tk.Text(box, wrap="word", height=8, font=("PingFang SC", 11),
                                    bg="#ffffff", fg="#334155", relief="flat",
                                    padx=12, pady=8, spacing1=1, spacing3=3)
            yt = ttk.Scrollbar(box, orient="vertical", command=self.four_txt.yview)
            self.four_txt.configure(yscrollcommand=yt.set, state="disabled")
            self.four_txt.pack(side="left", fill="both", expand=True)
            yt.pack(side="right", fill="y")

            # 四模型
            f5 = ttk.Frame(nb, padding=8)
            nb.add(f5, text=" 四模型 ")
            bar5 = ttk.Frame(f5)
            bar5.pack(fill="x", pady=(0, 6))
            self.btn_pro = ttk.Button(bar5, text="刷新四模型", style="BarAccent.TButton",
                                      command=self.act_pro_update)
            self.btn_pro.pack(side="right", padx=(4, 0))
            ttk.Button(bar5, text="打开日报", style="Bar.TButton",
                       command=self.act_pro_open).pack(side="right", padx=4)
            ttk.Button(bar5, text="回测页", style="Bar.TButton",
                       command=self.act_pro_backtest_open).pack(side="right", padx=4)
            self.btn_rank = ttk.Button(bar5, text="比分胜率排序", style="Bar.TButton",
                                       command=self.act_pro_score_rank)
            self.btn_rank.pack(side="right", padx=4)
            self.md_note = ttk.Label(bar5, text="", style="Sub.TLabel",
                                     wraplength=720, justify="left")
            self.md_note.pack(side="left", fill="x", expand=True)
            cols5 = [("no", "场次", 62), ("teams", "对阵", 176),
                     ("dc", "DC", 68), ("elo", "Elo", 68), ("xg", "xG", 68),
                     ("ml", "ML", 68), ("ens", "集成", 76),
                     ("pick", "倾向", 52), ("top", "比分 Top3", 210),
                     ("act", "实际", 56), ("ok", "核对", 46)]
            self.tv5 = ttk.Treeview(f5, columns=[c[0] for c in cols5], show="headings")
            for k, t, w in cols5:
                self.tv5.heading(k, text=t)
                self.tv5.column(k, width=w, minwidth=40, stretch=(k == "teams"),
                                anchor="w" if k in ("teams", "top") else "center")
            y5 = ttk.Scrollbar(f5, orient="vertical", command=self.tv5.yview)
            self.tv5.configure(yscrollcommand=y5.set)
            self.tv5.pack(side="left", fill="both", expand=True)
            y5.pack(side="right", fill="y")
            self.tv5.tag_configure("hit", background="#eaf7ee")
            self.tv5.tag_configure("miss", background="#fdf1f1")
            self.tv5.tag_configure("odd", background="#fbfcfe")

            # 汇总
            f2 = ttk.Frame(nb, padding=8)
            nb.add(f2, text=" 历史校准 ")
            cols2 = [("d", "日期", 110), ("n", "场次", 70), ("ok", "已核对", 80),
                     ("w", "胜负命中", 100), ("s1", "比分命中", 100),
                     ("s3", "TOP3", 100), ("s5", "TOP5", 100), ("hf", "半全场", 90),
                     ("lp", "log 概率", 100), ("br", "Brier", 90)]
            self.tv2 = ttk.Treeview(f2, columns=[c[0] for c in cols2], show="headings", height=12)
            for k, t, w in cols2:
                self.tv2.heading(k, text=t)
                self.tv2.column(k, width=w, anchor="center")
            self.tv2.pack(fill="both", expand=True)

            # 模拟投注
            f7 = ttk.Frame(nb, padding=8)
            nb.add(f7, text=" 模拟投注 ")
            self.sim_tab = f7
            bar7 = ttk.Frame(f7)
            bar7.pack(fill="x", pady=(0, 8))
            self.sim_note = ttk.Label(
                bar7, style="Sub.TLabel", wraplength=680, justify="left",
                text=("每天 100 元：正常盘 60（胜平负、总进球单关各 30）＋"
                      "高赔盘 40（比分 2串1、半全场单关各 20，半全场只打≥45%）；"
                      "场次不足时只出能凑齐的注。只记账，不代下单。"))
            ttk.Button(bar7, text="打开战绩页", style="Bar.TButton",
                       command=self.act_sim_open).pack(side="right", padx=3)
            ttk.Button(bar7, text="兑奖结算", style="Bar.TButton",
                       command=self.act_sim_settle).pack(side="right", padx=3)
            ttk.Button(bar7, text="生成今日", style="BarAccent.TButton",
                       command=self.act_sim_plan).pack(side="right", padx=3)
            self.sim_note.pack(side="left")

            self.sim_cards = ttk.Frame(f7)
            self.sim_cards.pack(fill="x", pady=(0, 8))

            cols7 = [("day", "日期", 94), ("id", "注", 44), ("play", "玩法", 70),
                     ("type", "类型", 58), ("sum", "内容", 380), ("odds", "赔率", 66),
                     ("stake", "本金", 60), ("status", "状态", 64),
                     ("payout", "返还", 68), ("pl", "盈亏", 72)]
            wrap7 = ttk.Frame(f7)
            wrap7.rowconfigure(0, weight=1)
            wrap7.columnconfigure(0, weight=1)
            self.tv7 = ttk.Treeview(wrap7, columns=[c[0] for c in cols7],
                                    show="headings", style="Four.Treeview")
            for k, t, w in cols7:
                self.tv7.heading(k, text=t)
                self.tv7.column(k, width=w, minwidth=40,
                                anchor="w" if k == "sum" else "center",
                                stretch=(k == "sum"))
            y7 = ttk.Scrollbar(wrap7, orient="vertical", command=self.tv7.yview)
            self.tv7.configure(yscrollcommand=y7.set)
            self.tv7.grid(row=0, column=0, sticky="nsew")
            y7.grid(row=0, column=1, sticky="ns")
            self.tv7.tag_configure("win", background="#eaf7ee")
            self.tv7.tag_configure("lose", background="#fdf1f1")
            self.tv7.tag_configure("wait", background="#fffaf0")
            self.tv7.tag_configure("odd", background="#fbfcfe")
            self.tv7.bind("<<TreeviewSelect>>", self._sim_detail)
            self.sim_map = {}

            self.sim_title = ttk.Label(f7, text="选择一注查看明细", style="Sub.TLabel")
            box7 = tk.Frame(f7, bg="#ffffff", highlightthickness=1,
                            highlightbackground="#e2e8f0")
            box7.pack(side="bottom", fill="x")
            self.sim_txt = tk.Text(box7, wrap="word", height=4, font=("PingFang SC", 11),
                                   bg="#ffffff", fg="#334155", relief="flat",
                                   padx=12, pady=8, spacing1=1, spacing3=3)
            yt7 = ttk.Scrollbar(box7, orient="vertical", command=self.sim_txt.yview)
            self.sim_txt.configure(yscrollcommand=yt7.set, state="disabled")
            self.sim_txt.pack(side="left", fill="both", expand=True)
            yt7.pack(side="right", fill="y")
            self.sim_title.pack(side="bottom", anchor="w", pady=(8, 2))
            # 表格最后 pack：空间不足时优先压缩表格，明细区始终可见
            wrap7.pack(fill="both", expand=True)

            # 日志
            f3 = ttk.Frame(nb, padding=8)
            nb.add(f3, text=" 运行日志 ")
            self.txt = tk.Text(f3, wrap="word", font=("Menlo", 11), bg="#0f172a",
                               fg="#e2e8f0", insertbackground="#e2e8f0", relief="flat")
            y3 = ttk.Scrollbar(f3, orient="vertical", command=self.txt.yview)
            self.txt.configure(yscrollcommand=y3.set, state="disabled")
            self.txt.pack(side="left", fill="both", expand=True)
            y3.pack(side="right", fill="y")

        # ---- 日志
        def push(self, line):
            self.q.put(line)

        def _drain(self):
            try:
                while True:
                    line = self.q.get_nowait()
                    self.txt.configure(state="normal")
                    self.txt.insert("end", line + "\n")
                    self.txt.see("end")
                    self.txt.configure(state="disabled")
            except queue.Empty:
                pass
            self.after(200, self._drain)

        # ---- 异步
        def run_bg(self, fn, done=None, btn=None, busy="运行中…"):
            if self.busy:
                return
            self.busy = True
            t0 = time.time()
            self.state.configure(text=busy, foreground=ACCENT)
            self.spin.pack(side="right", padx=(0, 10), pady=(10, 0), before=self.state)
            self.spin.start(12)
            if btn:
                btn.configure(state="disabled")
            self.update_idletasks()

            def work():
                err = None
                msg = None
                try:
                    msg = fn()
                except Exception as e:
                    err = e
                    log("出错：%s" % e)
                    log(traceback.format_exc().splitlines()[-1])
                el = time.time() - t0

                def fin():
                    self.busy = False
                    self.spin.stop()
                    self.spin.pack_forget()
                    if btn:
                        btn.configure(state="normal")
                    if done:
                        done()
                    self.reload(self.day.get().strip())
                    if err:
                        self.state.configure(text="出错：%s" % err, foreground="#b91c1c")
                        messagebox.showerror("出错了", str(err))
                    else:
                        self.state.configure(
                            text="%s · %.1fs" % (msg or "完成", el), foreground="#15803d")
                        self._idle_later()
                self.after(0, fin)
            threading.Thread(target=work, daemon=True).start()

        def report_callback_exception(self, exc, val, tb):
            log("界面异常：%s" % val)
            log("".join(traceback.format_exception(exc, val, tb)).strip().splitlines()[-1])
            try:
                messagebox.showerror("界面异常", str(val))
            except Exception:
                pass

        def _stamp(self, day):
            p = path_of(day)
            if not p.exists():
                return "最后更新 —"
            ts = dt.datetime.fromtimestamp(p.stat().st_mtime)
            return "最后更新 %s" % ts.strftime("%m-%d %H:%M:%S")

        def _idle_later(self):
            def reset():
                if not self.busy:
                    self.state.configure(text="就绪", foreground=MUTE)
            self.after(12000, reset)

        # ---- 数据
        def current_day(self):
            d = self.day.get().strip()
            try:
                dt.date.fromisoformat(d)
                return d
            except Exception:
                d = today_str()
                self.day.set(d)
                return d

        def shift_day(self, delta):
            try:
                d = dt.date.fromisoformat(self.current_day()) + dt.timedelta(days=delta)
            except Exception:
                return
            s = d.isoformat()
            self.day.set(s)
            self.reload(s)

        def open_picker(self):
            if getattr(self, "_cal", None) is not None:
                try:
                    self._cal.lift()
                    self._cal.focus_force()
                    return
                except Exception:
                    self._cal = None
            Cal = tk.Toplevel(self)
            self._cal = Cal
            Cal.title("选择比赛日")
            Cal.configure(bg="#ffffff")
            Cal.resizable(False, False)
            try:
                Cal.transient(self)
            except Exception:
                pass
            try:
                init = dt.date.fromisoformat(self.current_day())
            except Exception:
                init = dt.date.today()
            st = {"y": init.year, "m": init.month}

            def close():
                self._cal = None
                try:
                    Cal.destroy()
                except Exception:
                    pass

            Cal.protocol("WM_DELETE_WINDOW", close)

            head = tk.Frame(Cal, bg="#ffffff")
            head.pack(fill="x", padx=12, pady=(12, 4))
            ttk.Button(head, text="‹", width=2,
                       command=lambda: shift_month(-1)).pack(side="left")
            title = tk.Label(head, text="", font=("PingFang SC", 13, "bold"),
                             bg="#ffffff", fg="#1e293b", width=12)
            title.pack(side="left", expand=True)
            ttk.Button(head, text="›", width=2,
                       command=lambda: shift_month(1)).pack(side="right")

            body = tk.Frame(Cal, bg="#ffffff")
            body.pack(padx=12, pady=2)
            foot = tk.Frame(Cal, bg="#ffffff")
            foot.pack(fill="x", padx=12, pady=(6, 12))

            def pick(s):
                self.day.set(s)
                self.reload(s)
                close()

            def draw():
                for w in body.winfo_children():
                    w.destroy()
                title.configure(text="%d 年 %d 月" % (st["y"], st["m"]))
                for c, wd in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
                    tk.Label(body, text=wd, width=3, font=("PingFang SC", 10),
                             bg="#ffffff", fg="#8a94a6").grid(row=0, column=c, padx=1,
                                                              pady=(0, 3))
                weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(st["y"], st["m"])
                today = dt.date.today()
                selected = self.current_day()
                for r, week in enumerate(weeks, start=1):
                    for c, d in enumerate(week):
                        s = d.isoformat()
                        bg, fg = "#ffffff", "#334155"
                        if d.month != st["m"]:
                            fg = "#c3cad8"
                        if s == selected:
                            bg, fg = "#1f3b63", "#ffffff"
                        elif s == today.isoformat():
                            bg, fg = "#e8f0fe", "#1f3b63"
                        elif path_of(s).exists():
                            bg, fg = "#eaf7ee", "#166534"
                        cell = tk.Label(body, text=str(d.day), width=3, height=1,
                                        font=("PingFang SC", 11,
                                              "bold" if s == selected else "normal"),
                                        bg=bg, fg=fg, cursor="hand2",
                                        relief="flat", bd=0, highlightthickness=0)
                        cell.grid(row=r, column=c, padx=1, pady=1)
                        cell.bind("<Button-1>", lambda e, ss=s: pick(ss))
                        if s != selected:
                            cell.bind("<Enter>", lambda e, w=cell, b=bg:
                                      w.configure(bg="#dbeafe"))
                            cell.bind("<Leave>", lambda e, w=cell, b=bg:
                                      w.configure(bg=b))

            def shift_month(delta):
                m = st["m"] + delta
                st["y"] = st["y"] + (m - 1) // 12
                st["m"] = (m - 1) % 12 + 1
                draw()

            def go_today():
                s = today_str()
                self.day.set(s)
                self.reload(s)
                close()

            ttk.Button(foot, text="今天", command=go_today).pack(side="left")
            tk.Label(foot, text="绿底=已有记录", font=("PingFang SC", 10),
                     bg="#ffffff", fg="#8a94a6").pack(side="left", padx=10)
            ttk.Button(foot, text="关闭", command=close).pack(side="right")

            draw()
            Cal.update_idletasks()
            x = self.winfo_rootx() + 60
            y = self.winfo_rooty() + 150
            Cal.geometry("+%d+%d" % (x, y))
            try:
                Cal.grab_set()
            except Exception:
                pass
            return Cal

        def reload(self, day):
            try:
                data = load_day(day)
            except Exception:
                data = None
            self.tv.delete(*self.tv.get_children())
            for w in self.cards.winfo_children():
                w.destroy()
            self._rc_label(day)
            if not data:
                self._cards([("状态", "暂无记录", "点“一键更新”抓取"), ("比赛日", day, "")])
                self.stamp.configure(text=self._stamp(day))
                self._hf(None)
                self._models(None)
                self._four(None)
                self._recommend(day)
                self._simbank()
                return
            ev = evaluate(data)
            hfn, hfh = hf_tally(data)
            self._cards([
                ("场次", str(len(data["matches"])), ""),
                ("返还率", "%.1f%%" % (ev["rr"] * 100), ""),
                ("已核对", str(ev["n"]), "场"),
                ("胜负命中", ("%d/%d" % (ev["hitw"], ev["n"])) if ev["n"] else "—", ""),
                ("比分命中", str(ev["hit1"]) if ev["n"] else "—",
                 ("最高格 %d · TOP3 %d" % (ev["hitg"], ev["hit3"])) if ev["n"] else ""),
                ("比分覆盖 前三/前五",
                 ("%d · %d/%d" % (ev["hit3"], ev["hit5"], ev["n"])) if ev["n"] else "—",
                 ("%.0f%% / %.0f%%" % (ev["hit3"] / ev["n"] * 100, ev["hit5"] / ev["n"] * 100))
                 if ev["n"] else ""),
                ("总进球命中", ("%d/%d" % (ev["jqhit"], ev["jqn"])) if ev["jqn"] else "—", ""),
                ("半全场命中", ("%d/%d" % (hfh, hfn)) if hfn else "—", ""),
            ])
            for i, m in enumerate(data["matches"]):
                if not m.get("probs"):
                    continue
                sc = split_score(m.get("score", ""))
                same = m.get("same_score") or m["pick_score"]
                same_p = m.get("same_score_p", m["pick_score_p"])
                mg_txt = "—"
                if m.get("margin_pair"):
                    mg_txt = "%s %.0f%%" % (m["margin_pair"], m.get("margin_pair_p", 0) * 100)
                else:
                    try:
                        _, mpv, mtxt = dir_margin_pair(model_matrices(m).get("ens") or {})
                        mg_txt = "%s %.0f%%" % (mtxt, mpv * 100)
                    except Exception:
                        pass
                cover = m.get("top3_cover")
                if cover is None:
                    cover = sum(p for _, p in (m.get("top") or [])[:3])
                jpick = m.get("jq_pick")
                jq_p = m.get("jq_pick_p")
                if jpick is None or jq_p is None:
                    try:
                        jm = m.get("jq_model") or jq_dist(model_matrices(m).get("ens") or {})
                        jpick = max(range(8), key=lambda k: jm[k])
                        jq_p = jm[jpick]
                    except Exception:
                        jpick, jq_p = None, 0.0
                hf = self._hf_of(m)
                hpick = m.get("hf_pick")
                hp = m.get("hf_pick_p")
                if hf and hpick not in HF_VALUES:
                    hi = max(range(9), key=lambda k: hf[k])
                    hpick, hp = HF_VALUES[hi], hf[hi]
                elif hpick in HF_VALUES and hp is None and hf:
                    hp = hf[HF_VALUES.index(hpick)]
                jq_txt = ("%s球 %.0f%%" % (JQ_TXT[jpick], jq_p * 100)
                          if jpick is not None else "—")
                hf_txt = ("%s %.0f%%" % (hf_label(hpick), hp * 100)
                          if hpick in HF_VALUES and hp is not None else "—")
                act = []
                if sc:
                    act.append("%d:%d" % sc)
                if m.get("jq_result") is not None:
                    act.append("总%s" % JQ_TXT[m["jq_result"]])
                if m.get("hf_result") in HF_VALUES:
                    act.append(hf_label(m["hf_result"]))
                oks = []
                if m.get("wdl_result"):
                    oks.append("胜" + ("✓" if m["wdl_result"] == m["pick"] else "×"))
                if sc:
                    oks.append("比" + ("✓" if sc == score_tuple(same) else "×"))
                if m.get("jq_result") is not None and jpick is not None:
                    oks.append("总" + ("✓" if m["jq_result"] == jpick else "×"))
                if m.get("hf_result") in HF_VALUES and hpick in HF_VALUES:
                    oks.append("半" + ("✓" if m["hf_result"] == hpick else "×"))
                good = sum(1 for x in oks if x.endswith("✓"))
                tag = ("hit" if oks and good == len(oks)
                       else ("part" if good >= 3 else ("odd" if i % 2 else "")))
                self.tv.insert("", "end", values=(
                    m["no"], "%s vs %s" % (m["home"], m["away"]),
                    (m.get("kickoff", "") or "")[5:16],
                    "%s %.0f%%" % (m["pick"], max(m["probs"]) * 100),
                    "%s %.0f%% · %.0f%%" % (same, same_p * 100, cover * 100), mg_txt,
                    jq_txt, hf_txt,
                    " · ".join(act) or "—",
                    " ".join(oks) or "待赛"), tags=(tag,))
            self._hf(data)
            self._models(data)
            self._four(data)
            self._history()
            self._simbank()
            self._recommend(day)
            self.stamp.configure(text=self._stamp(day))

        def _cards(self, items):
            for i, (t, v, unit) in enumerate(items):
                c = ttk.Frame(self.cards, style="Card.TFrame", padding=(14, 10))
                c.grid(row=0, column=i, padx=(0, 10), sticky="nsew")
                self.cards.columnconfigure(i, weight=1)
                ttk.Label(c, text=t, style="CardTitle.TLabel").pack(anchor="w")
                row = ttk.Frame(c, style="Card.TFrame")
                row.pack(anchor="w")
                ttk.Label(row, text=v, style="CardNum.TLabel").pack(side="left")
                if unit:
                    ttk.Label(row, text=" " + unit, style="CardTitle.TLabel").pack(side="left", pady=(8, 0))

        def _history(self):
            self.tv2.delete(*self.tv2.get_children())
            days = sorted(load_all(), key=lambda d: d["date"], reverse=True)
            days = [d for d in days if not d.get("late")]
            for d in days[:60]:
                ev = evaluate(d)
                n = ev["n"] or 1
                hfn, hfh = hf_tally(d)
                self.tv2.insert("", "end", values=(
                    d["date"], len(d["matches"]), ev["n"],
                    "%d (%.0f%%)" % (ev["hitw"], ev["hitw"] / n * 100),
                    "%d (%.0f%%)" % (ev["hit1"], ev["hit1"] / n * 100),
                    "%d (%.0f%%)" % (ev["hit3"], ev["hit3"] / n * 100),
                    "%d (%.0f%%)" % (ev["hit5"], ev["hit5"] / n * 100),
                    ("%d/%d (%.0f%%)" % (hfh, hfn, hfh / hfn * 100)) if hfn else "—",
                    "%.3f" % (ev["logp"] / n), "%.3f" % (ev["brier"] / n)))

        # ---- 模拟投注
        SIMBANK = BASE / "模拟投注" / "simbank.py"
        SIM_LEDGER = BASE / "模拟投注" / "台账.json"
        SIM_REPORT = REPORT_DIR / "模拟投注战绩.html"

        def _sim_python(self):
            cands = [os.environ.get("SIMBANK_PY"),
                     str(Path.home() / ".local" / "bin" / "python3"),
                     "/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                     "/usr/bin/python3"]
            for p in cands:
                if p and Path(p).exists():
                    return p
            return sys.executable

        def _sim_cmd(self, mode):
            if not self.SIMBANK.exists():
                raise RuntimeError("找不到 simbank.py：%s" % self.SIMBANK)
            cmd = [self._sim_python(), str(self.SIMBANK), mode, "--quiet"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            for ln in out.splitlines()[-10:]:
                if ln.startswith("[") and "] " in ln[:22]:
                    ln = ln.split("] ", 1)[1]
                log(ln)
            if r.returncode != 0:
                raise RuntimeError(out.splitlines()[-1] if out else
                                   "退出码 %d" % r.returncode)
            return out

        def _sim_set(self, text):
            self.sim_txt.configure(state="normal")
            self.sim_txt.delete("1.0", "end")
            self.sim_txt.insert("end", text)
            self.sim_txt.configure(state="disabled")

        def _simbank(self):
            try:
                bets = json.loads(self.SIM_LEDGER.read_text(encoding="utf-8"))
            except Exception:
                bets = []
            bets = [b for b in bets if isinstance(b, dict)]
            for w in self.sim_cards.winfo_children():
                w.destroy()
            self.tv7.delete(*self.tv7.get_children())
            self.sim_map.clear()

            def short(bid):
                return str(bid).rsplit("-", 1)[-1] if "-" in str(bid) else str(bid)

            order = {"H1": 0, "H2": 1, "N1": 2, "N2": 3}
            rows = sorted(bets, key=lambda b: order.get(short(b.get("id")), 9))
            rows = sorted(rows, key=lambda b: b.get("day") or "", reverse=True)
            done = [b for b in bets if (b.get("status") or "") in ("中", "未中")]
            pending = [b for b in bets if (b.get("status") or "") not in ("中", "未中")]
            stake = sum(float(b.get("stake") or 0) for b in done)
            back = sum(float(b.get("payout") or 0) for b in done)
            pl = back - stake
            wins = sum(1 for b in done if b.get("status") == "中")
            roi = (pl / stake * 100) if stake else 0.0
            pl_color = "#15803d" if pl > 0 else ("#b91c1c" if pl < 0 else "#0f172a")

            def card(title, value, color="#0f172a", sub=""):
                c = tk.Frame(self.sim_cards, bg="#ffffff", highlightthickness=1,
                             highlightbackground="#e2e8f0")
                c.pack(side="left", fill="x", expand=True, padx=(0, 8))
                tk.Label(c, text=title, font=("PingFang SC", 10), bg="#ffffff",
                         fg="#64748b", anchor="w").pack(fill="x", padx=10, pady=(6, 0))
                tk.Label(c, text=value, font=("PingFang SC", 16, "bold"), bg="#ffffff",
                         fg=color, anchor="w").pack(fill="x", padx=10)
                tk.Label(c, text=sub or " ", font=("PingFang SC", 9), bg="#ffffff",
                         fg="#94a3b8", anchor="w").pack(fill="x", padx=10, pady=(0, 5))

            card("已结算投入", "¥%.0f" % stake, sub="%d 注" % len(done))
            card("累计返还", "¥%.2f" % back, sub="中 %d 注" % wins)
            card("盈亏", "%+.2f" % pl, pl_color,
                 sub=("回报率 %+.1f%%" % roi) if stake else "暂无已结算")
            card("命中率", ("%.0f%%" % (wins / len(done) * 100)) if done else "—",
                 sub="%d/%d 注" % (wins, len(done)))
            card("待结算", "%d 注" % len(pending),
                 sub=("¥%.0f 在场" % sum(float(b.get("stake") or 0) for b in pending))
                 if pending else "无")
            today = today_str()
            todays = [b for b in bets if (b.get("day") or "") == today]
            card("今日方案",
                 ("%d 注 ¥%.0f" % (len(todays),
                                   sum(float(b.get("stake") or 0) for b in todays)))
                 if todays else "未生成",
                 "#334155", sub="今天 %s" % today[5:])

            for b in rows:
                st = b.get("status") or "待结算"
                tag = "win" if st == "中" else ("lose" if st == "未中" else "wait")
                if st == "中":
                    pay = "%.2f" % float(b.get("payout") or 0)
                    one = float(b.get("payout") or 0) - float(b.get("stake") or 0)
                    pl_txt = "%+.2f" % one
                elif st == "未中":
                    pay, pl_txt = "0.00", "%.2f" % -float(b.get("stake") or 0)
                else:
                    pay, pl_txt = "—", "—"
                summary = " · ".join(
                    ("%s %s" % (l.get("no") or "", l.get("match") or "")).strip()
                    for l in (b.get("legs") or []))
                iid = self.tv7.insert("", "end", values=(
                    (b.get("day") or "")[5:], short(b.get("id")), b.get("play") or "",
                    b.get("type") or "", summary, "%.2f" % float(b.get("odds") or 0),
                    "%.0f" % float(b.get("stake") or 0), st, pay, pl_txt), tags=(tag,))
                self.sim_map[iid] = b
            kids = self.tv7.get_children()
            if kids:
                self.tv7.selection_set(kids[0])
                self._sim_detail()
            else:
                self.sim_title.configure(text="还没有模拟投注记录")
                self._sim_set("每天 11:20 后的定时任务会自动生成当天 100 元方案；"
                              "也可以点右上角“生成今日”立即生成。")

        def _sim_detail(self, _e=None):
            sel = self.tv7.selection()
            if not sel:
                return
            b = self.sim_map.get(sel[0])
            if not b:
                return
            st = b.get("status") or "待结算"
            self.sim_title.configure(text="%s · %s %s · %s · %s" % (
                b.get("day") or "", str(b.get("id") or "").rsplit("-", 1)[-1],
                b.get("play") or "", b.get("type") or "",
                "赔率 %.2f / 本金 %.0f 元" % (float(b.get("odds") or 0),
                                            float(b.get("stake") or 0))))
            lines = []
            for leg in b.get("legs") or []:
                hit = leg.get("hit")
                mark = "✓" if hit is True else ("×" if hit is False else "待赛")
                act = (" ｜ 实际 %s" % leg.get("score")) if leg.get("score") else ""
                lines.append("%s %s · %s %s ｜ 模型 %.1f%% · 赔率 %.2f%s %s" % (
                    leg.get("no") or "", leg.get("match") or "", leg.get("play") or "",
                    leg.get("pick") or leg.get("code") or "",
                    float(leg.get("p") or 0) * 100, float(leg.get("odds") or 0),
                    act, mark))
            if st == "中":
                lines.append("兑奖：返还 %.2f 元（盈利 %+.2f 元）" % (
                    float(b.get("payout") or 0),
                    float(b.get("payout") or 0) - float(b.get("stake") or 0)))
            elif st == "未中":
                lines.append("兑奖：未中，返还 0.00 元")
            else:
                lines.append("待结算：赛后由 09:10 / 11:20 的定时任务自动兑奖。")
            self._sim_set("\n".join(lines) if lines else "这一注没有腿信息。")

        def act_sim_plan(self):
            def job():
                self._sim_cmd("plan")
                return "模拟投注已生成"
            self.run_bg(job, busy="生成模拟投注…")

        def act_sim_settle(self):
            def job():
                self._sim_cmd("settle")
                return "模拟投注兑奖完成"
            self.run_bg(job, busy="兑奖结算…")

        def act_sim_open(self):
            if not self.SIM_REPORT.exists():
                try:
                    self._sim_cmd("report")
                except Exception as e:
                    messagebox.showerror("模拟投注", str(e))
                    return
            open_path(self.SIM_REPORT)

        # ---- 今日推荐
        def _on_tab(self, _e=None):
            try:
                if self.busy:
                    return
                cur = self.nb.index(self.nb.select())
                if cur == 0:
                    self._recommend(self.current_day())
                elif self.nb.select() == str(self.sim_tab):
                    self._simbank()
            except Exception:
                pass

        REC_COLORS = {
            "good": ("#f0fdf4", "#15803d", "#bbf7d0"),
            "mid": ("#fffbeb", "#b45309", "#fde68a"),
            "low": ("#f8fafc", "#334155", "#e2e8f0"),
        }

        def _rec_level(self, p, strong, mid):
            p = round(p, 3)
            if p >= strong:
                return "good", "稳"
            if p >= mid:
                return "mid", "中"
            return "low", "低"

        def _odd_s(self, v):
            try:
                return "%.2f" % float(v)
            except (TypeError, ValueError):
                return ""

        def _rec_cell(self, parent, col, label, answer, sub, level, odds="", note=""):
            bg, fg, border = self.REC_COLORS[level[0]]
            c = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground=border)
            c.grid(row=0, column=col, sticky="nsew", padx=4)
            parent.columnconfigure(col, weight=1)
            top = tk.Frame(c, bg=bg)
            top.pack(fill="x", padx=10, pady=(7, 0))
            tk.Label(top, text=label, bg=bg, fg="#64748b",
                     font=("PingFang SC", 10)).pack(side="left")
            if level[1]:
                tk.Label(top, text=level[1], bg=bg, fg=fg,
                         font=("PingFang SC", 10)).pack(side="right")
            tk.Label(c, text=answer, bg=bg, fg=fg,
                     font=("PingFang SC", 20, "bold")).pack(anchor="w", padx=10)
            if odds:
                row = tk.Frame(c, bg=bg)
                row.pack(anchor="w", padx=10, pady=(0, 8))
                tk.Label(row, text=sub, bg=bg, fg="#475569",
                         font=("PingFang SC", 10)).pack(side="left")
                tk.Label(row, text="  赔率 " + odds, bg=bg, fg="#0f766e",
                         font=("PingFang SC", 10, "bold")).pack(side="left")
            else:
                tk.Label(c, text=sub, bg=bg, fg="#475569",
                         font=("PingFang SC", 10)).pack(anchor="w", padx=10,
                                                       pady=(0, 8 if not note else 2))
            if note:
                tk.Label(c, text=note, bg=bg, fg="#94a3b8",
                         font=("PingFang SC", 9)).pack(anchor="w", padx=10, pady=(0, 8))

        def _recommend(self, day):
            for w in self.rec_body.winfo_children():
                w.destroy()
            data = pro_recommend_data(day)
            if not data or not data.get("rows"):
                self.rec_hint.configure(text="这一天还没有推荐 · 点右上角“刷新推荐”")
                tip = tk.Frame(self.rec_body, bg=CARD, highlightthickness=1,
                               highlightbackground="#e5eaf1")
                tip.pack(fill="x")
                tk.Label(tip, text="今日推荐还没生成\n"
                                   "点“刷新推荐”，或等 11:20 / 14:00 / 18:30 自动生成。",
                         bg=CARD, fg=MUTE, font=("PingFang SC", 13),
                         justify="left", padx=18, pady=22).pack(anchor="w")
                if day not in self._rec_auto and pro_snapshot(day) and not self.busy:
                    self._rec_auto.add(day)
                    self.after(300, lambda: self.act_rec_refresh(auto=True))
                return

            rows = data.get("rows") or []
            top = data.get("top") or {}
            self.rec_hint.configure(
                text="最稳 %d 场 · 生成 %s" % (len(rows), data.get("generated_at", "")))
            if top:
                hero = tk.Frame(self.rec_body, bg=ACCENT)
                hero.pack(fill="x", pady=(0, 10))
                tk.Label(hero, text="今日最稳一注", bg=ACCENT, fg="#c7d7ec",
                         font=("PingFang SC", 11)).pack(anchor="w", padx=18, pady=(8, 0))
                line = tk.Frame(hero, bg=ACCENT)
                line.pack(anchor="w", padx=18, pady=(0, 10))
                tk.Label(line, text="%s vs %s" % (top.get("home", ""), top.get("away", "")),
                         bg=ACCENT, fg="#ffffff",
                         font=("PingFang SC", 18, "bold")).pack(side="left")
                tk.Label(line, text="%s %.1f%%" % (top.get("x2", ""), top.get("x2_p", 0) * 100),
                         bg=ACCENT, fg="#9be7b6",
                         font=("PingFang SC", 18, "bold")).pack(side="left", padx=(16, 0))
                if self._odd_s(top.get("x2_odds")):
                    tk.Label(line, text="@ %s" % self._odd_s(top.get("x2_odds")),
                             bg=ACCENT, fg="#9be7b6",
                             font=("PingFang SC", 15, "bold")).pack(side="left", padx=(8, 0))
                tk.Label(line, text="%s · %s" % (top.get("no", ""), top.get("kickoff", "")),
                         bg=ACCENT, fg="#a9bcd6",
                         font=("PingFang SC", 10)).pack(side="left", padx=(14, 0), pady=(6, 0))

            for i, r in enumerate(rows):
                card = tk.Frame(self.rec_body, bg=CARD, highlightthickness=1,
                                highlightbackground="#e5eaf1")
                card.pack(fill="x", pady=(0, 8))
                hdr = tk.Frame(card, bg=CARD)
                hdr.pack(fill="x", padx=14, pady=(9, 4))
                tk.Label(hdr, text="%d. %s vs %s" % (i + 1, r.get("home", ""), r.get("away", "")),
                         bg=CARD, fg=INK, font=("PingFang SC", 13, "bold")).pack(side="left")
                tk.Label(hdr, text="%s · %s · %s" % (r.get("no", ""), r.get("league", ""),
                                                     r.get("kickoff", "")),
                         bg=CARD, fg=MUTE,
                         font=("PingFang SC", 10)).pack(side="left", padx=(10, 0))
                plays = tk.Frame(card, bg=CARD)
                plays.pack(fill="x", padx=10, pady=(0, 10))
                x2l, x2t = self._rec_level(r.get("x2_p", 0), 0.60, 0.45)
                self._rec_cell(plays, 0, "胜平负", r.get("x2", "—"),
                               "%.1f%%" % (r.get("x2_p", 0) * 100), (x2l, x2t),
                               self._odd_s(r.get("x2_odds")))
                note = ""
                if r.get("score2"):
                    note = "备选 %s %.1f%%" % (r["score2"], r.get("score2_p", 0) * 100)
                    if self._odd_s(r.get("score2_odds")):
                        note += " · 赔率 " + self._odd_s(r.get("score2_odds"))
                self._rec_cell(plays, 1, "比分", r.get("score", "—"),
                               "%.1f%%" % (r.get("score_p", 0) * 100), ("low", "参考"),
                               self._odd_s(r.get("score_odds")), note)
                g = r.get("goals") or {}
                if g:
                    gl, gt = self._rec_level(g.get("cov", 0), 0.55, 0.45)
                    go = " / ".join("%.2f" % v for _, v in (r.get("goals_odds") or []) if v)
                    self._rec_cell(plays, 2, "总进球", "%d–%d 球" % (g["lo"], g["hi"]),
                                   "区间 %.1f%%" % (g["cov"] * 100), (gl, gt), go)
                else:
                    self._rec_cell(plays, 2, "总进球", "—", "无数据", ("low", ""))
                hf = r.get("hf") or {}
                if hf:
                    hl, ht = self._rec_level(hf.get("p", 0), HF_GATE, HF_MID)
                    note = "" if hl == "good" else "低于 45% 只作参考，不串关"
                    self._rec_cell(plays, 3, "半全场", hf.get("pick", "—"),
                                   "%.1f%%" % (hf.get("p", 0) * 100), (hl, ht),
                                   self._odd_s(r.get("hf_odds")), note)
                else:
                    self._rec_cell(plays, 3, "半全场", "—", "无数据", ("low", ""))
            self._upset_block(day)
            self._parlay45_block(day)
            self.rec_canvas.yview_moveto(0)

        def _upset_block(self, day):
            up = pro_upset_data(day) or {}
            picks = up.get("picks") or []
            head = tk.Frame(self.rec_body, bg=BG)
            head.pack(fill="x", pady=(16, 4))
            tk.Label(head, text="今日爆冷比分", bg=BG, fg=INK,
                     font=("PingFang SC", 14, "bold")).pack(side="left")
            tk.Label(head, text="高赔冷门 · %d 注 · 只算概率不下单" % len(picks),
                     bg=BG, fg=MUTE,
                     font=("PingFang SC", 10)).pack(side="left", padx=(10, 0))
            if picks:
                link = tk.Label(head, text="打开爆冷页 ›", bg=BG, fg="#1d4ed8",
                                font=("PingFang SC", 10), cursor="hand2")
                link.pack(side="right")

                def _open(_e=None):
                    p = pro_upset_report(day)
                    if p:
                        open_path(p)
                link.bind("<Button-1>", _open)

            if not picks:
                tip = tk.Frame(self.rec_body, bg=CARD, highlightthickness=1,
                               highlightbackground="#e5eaf1")
                tip.pack(fill="x")
                tk.Label(tip, text="这一天还没有爆冷比分\n"
                                   "点“刷新推荐”会一起生成；没有满足赔率≥12、概率≥3.5% 的冷门时留空。",
                         bg=CARD, fg=MUTE, font=("PingFang SC", 11),
                         justify="left", padx=16, pady=14).pack(anchor="w")
                return

            UBG, ULINE, UACC, UDEEP = "#fff7ed", "#fed7aa", "#c2410c", "#7c2d12"
            grid = tk.Frame(self.rec_body, bg=BG)
            grid.pack(fill="x", pady=(0, 8))
            for i, c in enumerate(picks[:3]):
                grid.columnconfigure(i, weight=1, uniform="up")
                card = tk.Frame(grid, bg=UBG, highlightthickness=1,
                                highlightbackground=ULINE)
                card.grid(row=0, column=i, sticky="nsew", padx=4)
                top = tk.Frame(card, bg=UBG)
                top.pack(fill="x", padx=12, pady=(8, 0))
                tk.Label(top, text="%s · %s" % (c.get("no", ""),
                                                (c.get("kickoff") or "")[5:]),
                         bg=UBG, fg="#9a3412",
                         font=("PingFang SC", 9)).pack(side="left")
                tk.Label(top, text="冷", bg="#ffedd5", fg=UACC,
                         font=("PingFang SC", 9, "bold")).pack(side="right", ipadx=4)
                tk.Label(card, text="%s vs %s" % (c.get("home", ""), c.get("away", "")),
                         bg=UBG, fg=INK, font=("PingFang SC", 12, "bold"),
                         anchor="w").pack(fill="x", padx=12, pady=(2, 0))
                line = tk.Frame(card, bg=UBG)
                line.pack(anchor="w", padx=12)
                tk.Label(line, text=c.get("score", "—"), bg=UBG, fg=UACC,
                         font=("PingFang SC", 22, "bold")).pack(side="left")
                tk.Label(line, text="@ %.2f" % c.get("odds", 0), bg=UBG, fg="#9a3412",
                         font=("PingFang SC", 14, "bold")).pack(side="left",
                                                              padx=(8, 0), pady=(6, 0))
                tk.Label(card, text="模型 %.1f%% · EV %.2f" % (
                             c.get("score_p", 0) * 100, c.get("ev", 0)),
                         bg=UBG, fg=UDEEP,
                         font=("PingFang SC", 10)).pack(anchor="w", padx=12)
                d = c.get("direction", "")
                edge = c.get("edge", 0)
                tk.Label(card, text="%s / 市场热%s %.0f%% · 模型%s市场 %.1f 个点" % (
                             "冷平" if d == "平局" else "冷" + d,
                             c.get("fav", ""), c.get("fav_p", 0) * 100,
                             "高于" if edge >= 0 else "低于", abs(edge) * 100),
                         bg=UBG, fg="#9a3412", font=("PingFang SC", 9),
                         wraplength=300, justify="left").pack(anchor="w", padx=12,
                                                             pady=(0, 9))

        def _parlay45_block(self, day):
            p45 = pro_parlay45_data(day) or {}
            legs = p45.get("legs") or []
            bets = p45.get("bets") or []
            sm = p45.get("summary") or {}
            head = tk.Frame(self.rec_body, bg=BG)
            head.pack(fill="x", pady=(16, 4))
            tk.Label(head, text="爆冷四串五", bg=BG, fg=INK,
                     font=("PingFang SC", 14, "bold")).pack(side="left")
            hint = "4 场 × %d 注 · 每注 2 元" % len(bets) if bets else "4 场 × 5 注"
            if legs:
                hint += " · 任一中奖 %.4f%% · 全中最高 ¥%s" % (
                    sm.get("p_any", 0) * 100,
                    "{:,.0f}".format(sm.get("max_payout", 0)))
            tk.Label(head, text=hint, bg=BG, fg=MUTE,
                     font=("PingFang SC", 10)).pack(side="left", padx=(10, 0))
            if legs:
                link = tk.Label(head, text="打开四串五页 ›", bg=BG, fg="#1d4ed8",
                                font=("PingFang SC", 10), cursor="hand2")
                link.pack(side="right")

                def _open(_e=None):
                    p = pro_parlay45_report(day)
                    if p:
                        open_path(p)
                link.bind("<Button-1>", _open)

            if not legs:
                tip = tk.Frame(self.rec_body, bg=CARD, highlightthickness=1,
                               highlightbackground="#e5eaf1")
                tip.pack(fill="x")
                tk.Label(tip, text=p45.get("note") or
                                   "今天凑不齐四场爆冷，四串五留空；刷新推荐会重算。",
                         bg=CARD, fg=MUTE, font=("PingFang SC", 11),
                         justify="left", padx=16, pady=14).pack(anchor="w")
                return

            UBG, ULINE, UACC = "#fff7ed", "#fed7aa", "#c2410c"
            lrow = tk.Frame(self.rec_body, bg=BG)
            lrow.pack(fill="x")
            for i, c in enumerate(legs[:4]):
                lrow.columnconfigure(i, weight=1, uniform="p45")
                card = tk.Frame(lrow, bg=UBG, highlightthickness=1,
                                highlightbackground=ULINE)
                card.grid(row=0, column=i, sticky="nsew", padx=3)
                top = tk.Frame(card, bg=UBG)
                top.pack(fill="x", padx=11, pady=(7, 0))
                tk.Label(top, text="%s · %s" % (c.get("no", ""),
                                                (c.get("kickoff") or "")[5:16]),
                         bg=UBG, fg="#9a3412",
                         font=("PingFang SC", 9)).pack(side="left")
                tk.Label(top, text="冷", bg="#ffedd5", fg=UACC,
                         font=("PingFang SC", 9, "bold")).pack(side="right", ipadx=4)
                tk.Label(card, text="%s vs %s" % (c.get("home", ""), c.get("away", "")),
                         bg=UBG, fg=INK, font=("PingFang SC", 10, "bold"),
                         anchor="w", wraplength=230, justify="left"
                         ).pack(fill="x", padx=11, pady=(2, 0))
                line = tk.Frame(card, bg=UBG)
                line.pack(anchor="w", padx=11)
                tk.Label(line, text=c.get("score", "—"), bg=UBG, fg=UACC,
                         font=("PingFang SC", 17, "bold")).pack(side="left")
                tk.Label(line, text="@ %.2f" % c.get("odds", 0), bg=UBG, fg="#9a3412",
                         font=("PingFang SC", 11, "bold")).pack(side="left", padx=(6, 0),
                                                              pady=(4, 0))
                d = c.get("direction", "")
                tk.Label(card, text="模型 %.2f%% · %s" % (
                             c.get("score_p", 0) * 100,
                             "冷平" if d == "平局" else "冷" + d),
                         bg=UBG, fg="#7c2d12", font=("PingFang SC", 9)
                         ).pack(anchor="w", padx=11, pady=(0, 8))

            box = tk.Frame(self.rec_body, bg=CARD, highlightthickness=1,
                           highlightbackground="#e5eaf1")
            box.pack(fill="x", pady=(6, 0))
            hdr = tk.Frame(box, bg="#f8fafc")
            hdr.pack(fill="x")
            tk.Label(hdr, text="注项", width=7, bg="#f8fafc", fg="#64748b",
                     font=("PingFang SC", 9), anchor="w"
                     ).pack(side="left", padx=(12, 2), pady=5)
            for t, w in (("EV", 7), ("2元返奖", 12), ("赔率", 10), ("命中", 10)):
                tk.Label(hdr, text=t, width=w, bg="#f8fafc", fg="#64748b",
                         font=("PingFang SC", 9), anchor="e"
                         ).pack(side="right", padx=(2, 12 if t == "EV" else 2), pady=5)
            tk.Label(hdr, text="组合", bg="#f8fafc", fg="#64748b",
                     font=("PingFang SC", 9), anchor="w"
                     ).pack(side="left", expand=True, fill="x", padx=2, pady=5)
            for b in bets:
                is4 = b.get("kind") == "四串一"
                bg = UBG if is4 else CARD
                row = tk.Frame(box, bg=bg)
                row.pack(fill="x")
                combo = " + ".join(x[-3:] for x in b.get("legs") or [])
                pv = b.get("p", 0)
                ptxt = ("%.4f%%" % (pv * 100)) if pv >= 1e-4 else ("%.5f%%" % (pv * 100))
                nums = (("%.2f" % b.get("ev", 0), 7),
                        ("¥" + "{:,.0f}".format(b.get("payout", 0)), 12),
                        ("{:,.0f}".format(b.get("odds", 0)), 10),
                        (ptxt, 10))
                tk.Label(row, text=b.get("kind", ""), width=7, bg=bg, fg=INK,
                         font=("PingFang SC", 9, "bold" if is4 else "normal"),
                         anchor="w").pack(side="left", padx=(12, 2), pady=4)
                for k, (t, w) in enumerate(nums):
                    tk.Label(row, text=t, width=w, bg=bg,
                             fg=UACC if k == 1 else "#475569",
                             font=("PingFang SC", 9, "bold" if is4 else "normal"),
                             anchor="e").pack(side="right",
                                              padx=(2, 12 if k == 0 else 2), pady=4)
                tk.Label(row, text=combo, bg=bg,
                         fg=UACC if is4 else "#475569",
                         font=("PingFang SC", 9, "bold" if is4 else "normal"),
                         anchor="w").pack(side="left", expand=True, fill="x",
                                          padx=2, pady=4)
            tk.Label(box, text="中 3 场只兑对应三串一；中 4 场 5 注全中 · "
                               "期望返奖 ¥%.2f（回报率 %.1f%%）" % (
                                   sm.get("expected_return", 0), sm.get("roi", 0) * 100),
                     bg=CARD, fg="#94a3b8", font=("PingFang SC", 9)
                     ).pack(anchor="w", padx=12, pady=(2, 7))

        def act_rec_refresh(self, auto=False):
            day = self.current_day()
            if not pro_snapshot(day):
                if not auto:
                    messagebox.showinfo("今日推荐",
                                        "这一天还没有四模型快照。\n先点“刷新四模型”生成。")
                return

            def job():
                pro_run("recommend", "--date", day)
                d = pro_recommend_data(day) or {}
                return "推荐 %d 场" % len(d.get("rows") or [])

            def done():
                self._recommend(day)

            self.run_bg(job, btn=self.btn_rec, done=done, busy="生成推荐中…")

        def act_rec_open(self):
            p = pro_recommend_report(self.current_day())
            if p:
                open_path(p)
            else:
                messagebox.showinfo("今日推荐", "这一天还没有推荐页。\n先点“刷新推荐”。")

        def act_analysis_open(self):
            p = pro_analysis_report(self.current_day())
            if p:
                open_path(p)
            else:
                messagebox.showinfo("当日全解析",
                                    "这一天还没有全解析文档。\n"
                                    "先点“刷新推荐”，或等定时任务生成。")

        # ---- 四模型
        def _models(self, data):
            self.tv5.delete(*self.tv5.get_children())
            day = self.current_day()
            snap = pro_snapshot(day)
            if not snap:
                self.md_note.configure(
                    text=("这一天还没有四模型快照。点“刷新四模型”或“一键更新”生成。\n"
                          "四模型 = Dixon-Coles + Elo + xG + 机器学习，集成权重按最近 220 场样本外 "
                          "LogLoss 自动优化。"))
                return
            res = snap.get("result") or {}
            rows = sorted(res.get("matches") or [], key=lambda m: m.get("kickoff", ""))
            old = {m.get("no"): m for m in (data or {}).get("matches", [])}
            for i, m in enumerate(rows):
                models = m.get("models") or {}
                vals = []
                for k in ("dc", "elo", "xg", "ml"):
                    o = models.get(k) or {}
                    if o.get("available") and o.get("probs"):
                        vals.append("%s %.0f%%" % (o.get("pick", ""), max(o["probs"]) * 100))
                    else:
                        vals.append("弃权")
                ens = models.get("ens") or {}
                ensv = ("%s %.0f%%" % (ens.get("pick", ""), max(ens["probs"]) * 100)
                        if ens.get("available") else "—")
                top = " ".join("%s %.0f%%" % (s, p * 100)
                               for s, p in (m.get("score_top", {}).get("ens") or [])[:3])
                om = old.get(m.get("no")) or {}
                sc = split_score(om.get("score", ""))
                act = "%d-%d" % sc if sc else ""
                ok, tag = "", ("odd" if i % 2 else "")
                if act and ens.get("available"):
                    actual = ("主胜" if sc[0] > sc[1]
                              else ("平局" if sc[0] == sc[1] else "客胜"))
                    good = ens.get("pick") == actual
                    ok = "中" if good else "未中"
                    tag = "hit" if good else "miss"
                self.tv5.insert("", "end", values=(
                    m.get("no", ""), "%s vs %s" % (m.get("home", ""), m.get("away", "")),
                    vals[0], vals[1], vals[2], vals[3], ensv,
                    ens.get("pick", "—"), top or "—", act or "—", ok), tags=(tag,))
            name = {"dc": "DC", "elo": "Elo", "xg": "xG", "ml": "ML"}
            prior = res.get("prior") or {}
            weight = " / ".join("%s %.0f%%" % (name.get(k, k), v * 100)
                                for k, v in prior.items() if v >= 0.001)
            note = ("四模型 · 生成 %s · 截断 %s\n权重 %s"
                    % (res.get("generated_at", "—"), res.get("cutoff", "—"), weight or "—"))
            bt = (snap.get("backtest") or {}).get("models") or {}
            if bt.get("ens"):
                e = bt["ens"]
                note += ("\n样本外 %d 场：集成 LogLoss %.4f · 1X2 %.1f%% · 比分 Top1/Top3/Top5 = "
                         "%.1f%%/%.1f%%/%.1f%%；DC 为赔率锚定基线"
                         % (e.get("n", 0), e.get("logloss", 0), e.get("acc", 0) * 100,
                            e.get("score_top1", 0) * 100, e.get("score_top3", 0) * 100,
                            e.get("score_top5", 0) * 100))
            note += ("\nDC 由竞彩赔率反解，与市场同源；xG 只覆盖 9 大联赛，亚冠等会弃权。"
                     "“弃权”表示该模型没有足够样本，不参与集成。")
            self.md_note.configure(text=note)

        # ---- 半全场
        def _hf_of(self, m):
            hf = m.get("hf")
            if (not hf or len(hf) != 9) and m.get("lambda"):
                try:
                    hf = hf_probs(m["lambda"][0], m["lambda"][1], rho=m.get("rho", RHO))
                except Exception:
                    hf = None
            return hf if hf and len(hf) == 9 else None

        def _bq_of(self, m):
            q = m.get("bq_probs")
            if q and len(q) == 9:
                return q
            o = m.get("bq_odds")
            if o and len(o) == 9 and all(isinstance(x, (int, float)) and x > 1.0 for x in o):
                try:
                    return shin(o)[0]
                except Exception:
                    return None
            return None

        def _hf_color(self, p):
            if p >= 0.22:
                return "#1f3b63", "#ffffff"
            if p >= 0.14:
                return "#c9dcf5", "#16324f"
            if p >= 0.09:
                return "#e3edfb", "#1e3a5f"
            if p >= 0.05:
                return "#f2f7fd", "#334155"
            return "#fbfcfe", "#94a3b8"

        def _hf_detail(self, event=None):
            sel = self.tv4.selection()
            m = self.hf_map.get(sel[0]) if sel else None
            if not m:
                return
            self.hf_title.configure(text="%s  %s vs %s" % (m["no"], m["home"], m["away"]))
            hf = self._hf_of(m)
            if not hf:
                self.hf_sub.configure(text="没有可用的半全场数据")
                return
            bq = self._bq_of(m)
            order = sorted(range(9), key=lambda k: -hf[k])
            hi = order[0]
            line = "① %s %.1f%% · ② %s %.1f%% · ③ %s %.1f%%" % (
                hf_label(HF_VALUES[order[0]]), hf[order[0]] * 100,
                hf_label(HF_VALUES[order[1]]), hf[order[1]] * 100,
                hf_label(HF_VALUES[order[2]]), hf[order[2]] * 100)
            if bq:
                line += "\n第一档 vs 市场 %.1f%% · 差 %+.1f 点" % (
                    bq[hi] * 100, (hf[hi] - bq[hi]) * 100)
            else:
                line += "\n无半全场赔率"
            act = m.get("hf_result")
            if act:
                pick = HF_VALUES[hi]
                line += "\n实际 %s%s" % (
                    hf_label(act), "（命中）" if act == pick else
                    ("（前三）" if act in [HF_VALUES[k] for k in order[:3]] else "（未中）"))
            self.hf_sub.configure(text=line)
            for r in range(3):
                for c in range(3):
                    idx = r * 3 + c
                    p = hf[idx]
                    bg, fg = self._hf_color(p)
                    if idx == hi:
                        bg, fg = "#1f3b63", "#ffffff"
                    if act and act == HF_VALUES[idx]:
                        bg, fg = "#d9f2e3", "#166534"
                    rk = "①②③"[order.index(idx)] if idx in order[:3] else ""
                    self.hf_cells[r][c].configure(
                        text="%s%.1f%%" % (rk, p * 100), bg=bg, fg=fg)

        def _hf(self, data):
            self.tv4.delete(*self.tv4.get_children())
            self.hf_map = {}
            rows = [m for m in (data or {}).get("matches", []) if m.get("probs")]
            rows.sort(key=lambda m: m.get("kickoff", ""))
            exp = 0.0
            n_mkt = done = hit = t3hit = 0
            first = None
            for i, m in enumerate(rows):
                hf = self._hf_of(m)
                if not hf:
                    continue
                order = sorted(range(9), key=lambda k: -hf[k])
                hi = order[0]
                top = hf[hi]
                exp += top
                second = (hf[order[1]], order[1])
                third = (hf[order[2]], order[2])
                bq = self._bq_of(m)
                if bq:
                    n_mkt += 1
                dv = "%+.1f" % ((top - bq[hi]) * 100) if bq else "—"
                act = m.get("hf_result")
                ok = ""
                tag = "odd" if i % 2 else ""
                if act:
                    done += 1
                    good = act == HF_VALUES[hi]
                    hit += good
                    t3hit += act in [HF_VALUES[k] for k in order[:3]]
                    ok = "中" if good else "未中"
                    tag = "hit" if good else "miss"
                iid = self.tv4.insert("", "end", values=(
                    m["no"], "%s vs %s" % (m["home"], m["away"]),
                    (m.get("kickoff", "") or "")[5:16],
                    hf_label(HF_VALUES[hi]), "%.1f%%" % (top * 100),
                    ("%.1f%%" % (bq[hi] * 100)) if bq else "—", dv,
                    "② %s %.1f%% · ③ %s %.1f%%" % (
                        hf_label(HF_VALUES[second[1]]), second[0] * 100,
                        hf_label(HF_VALUES[third[1]]), third[0] * 100),
                    hf_label(act), ok), tags=(tag,))
                self.hf_map[iid] = m
                if first is None:
                    first = iid
            if rows:
                t3txt = ("%d（%.0f%%）" % (t3hit, t3hit / done * 100)) if done else "待核对"
                self.hf_note.configure(
                    text="%d 场 · 有半全场赔率 %d 场 · 首选合计期望 %.1f 场 · 已核对 %d 场、首选命中 %d、前三覆盖 %s"
                    % (len(rows), n_mkt, exp, done, hit, t3txt))
            else:
                self.hf_note.configure(text="当天还没有记录")
            if first:
                self.tv4.selection_set(first)
                self.tv4.focus(first)
                self._hf_detail()
            else:
                self.hf_title.configure(text="半全场分布")
                self.hf_sub.configure(text="选中左侧一场比赛查看九宫格")
                for row in self.hf_cells:
                    for lab in row:
                        lab.configure(text="—", bg="#f8fafc", fg="#cbd5e1")

        # ---- 四玩法赔率
        def _four_detail(self, event=None):
            sel = self.tv6.selection()
            m = self.four_map.get(sel[0]) if sel else None
            if not m:
                return
            W = ["主胜", "平局", "客胜"]
            self.four_title.configure(text="%s  %s vs %s" % (m["no"], m["home"], m["away"]))
            sc = split_score(m.get("score", ""))
            hres = m.get("hf_result")
            lines = []
            bits = []
            if sc:
                bits.append("比分 %d:%d" % sc)
                bits.append("总进球 %s" % JQ_TXT[jq_index(sum(sc))])
            if hres in HF_VALUES:
                bits.append("半全场 %s" % hf_label(hres))
            lines.append("赛果：" + (" · ".join(bits) if bits else "待赛"))
            lines.append("")

            odds = m.get("odds") or []
            dp = odds_probs(odds) if len(odds) == 3 else None
            if dp and m.get("probs"):
                pi = W.index(m["pick"]) if m.get("pick") in W else max(range(3), key=lambda i: m["probs"][i])
                mi = min(range(3), key=lambda i: odds[i])
                parts = ["%s %s（市 %.1f%% · 模型 %.1f%%）"
                         % (W[i], fmt_odds(odds[i]), dp[i] * 100, m["probs"][i] * 100)
                         for i in range(3)]
                tail = ("模型首选 %s %.1f%% · 市场首选 %s %.1f%%"
                        % (W[pi], m["probs"][pi] * 100, W[mi], dp[mi] * 100))
                if m.get("wdl_result"):
                    tail += " · 实际 %s → 模型%s · 市场%s" % (
                        m["wdl_result"], "中" if m["wdl_result"] == W[pi] else "未中",
                        "中" if m["wdl_result"] == W[mi] else "未中")
                lines.append("胜平负（3 档）：" + "；".join(parts) + "。")
                lines.append(tail)
                lines.append("")

            bfo = m.get("bf_odds") or {}
            if bfo:
                keys = [k for k in bfo if isinstance(bfo[k], (int, float)) and bfo[k] > 1.0]
                ens = model_matrices(m).get("ens") or {}
                mp = bf_model_probs(ens, keys)
                low = min(keys, key=lambda k: bfo[k])
                parts = ["%s %s（模 %.0f%%）" % (k, fmt_odds(bfo[k]), mp.get(k, 0) * 100)
                         for k in keys]
                same = m.get("same_score") or m["pick_score"]
                same_p = m.get("same_score_p", m["pick_score_p"])
                tail = ("模型同向 %s %.1f%% · 市场最低 %s %s"
                        % (same, same_p * 100, low, fmt_odds(bfo[low])))
                if sc:
                    ba = "%d:%d" % sc
                    same_sc = score_tuple(same)
                    tail += " · 实际 %s → 模型%s · 市场%s" % (
                        ba,
                        "中" if sc == same_sc else
                        ("TOP3中" if ba in [x[0] for x in m["top"][:3]] else "未中"),
                        "中" if bf_key_of(ba, keys) == low else "未中")
                lines.append("比分（31 档）：" + "；".join(parts) + "。")
                lines.append(tail)
                lines.append("")

            md, mi, mp, m2 = m.get("margin"), None, None, None
            if not md or len(md) != 7:
                try:
                    md = margin_dist(model_matrices(m).get("ens") or {})
                except Exception:
                    md = None
            if md and len(md) == 7:
                mi = m.get("margin_pick")
                mp = m.get("margin_pick_p")
                m2 = m.get("margin_top2_cover")
                if mi is None or mp is None or m2 is None:
                    try:
                        mi, mp, m2 = dir_margin_pick(model_matrices(m).get("ens") or {})
                    except Exception:
                        mi, mp, m2 = None, None, None
            mtxt, mcover = m.get("margin_pair"), m.get("margin_pair_p")
            if not mtxt or mcover is None:
                try:
                    _, mcover, mtxt = dir_margin_pair(model_matrices(m).get("ens") or {})
                except Exception:
                    mtxt, mcover = "", m2
            if md and len(md) == 7 and mi is not None:
                tail = "按胜负方向 %s：首选 %s %.1f%% · 前二 %s %.1f%%" % (
                    m.get("pick", ""), MARGIN_TXT[mi], mp * 100, mtxt, mcover * 100)
                if sc:
                    ai = margin_index(sc[0], sc[1])
                    tail += " · 实际 %s → %s" % (
                        MARGIN_TXT[ai], "中" if ai == mi else "未中")
                lines.append("净胜球（7 档）：" + "；".join(
                    "%s %.1f%%" % (MARGIN_TXT[k], md[k] * 100) for k in range(7)) + "。")
                lines.append(tail)
                top = m.get("top") or []
                if top:
                    c1 = top[0][1]
                    c2 = c1 + (top[1][1] if len(top) > 1 else 0)
                    c3 = c2 + (top[2][1] if len(top) > 2 else 0)
                    lines.append("比分覆盖：单选 %.1f%% · 前二 %.1f%% · 前三 %.1f%%。"
                                 "单格超过 20%% 的场次很少，比分玩法建议同时看前三覆盖；"
                                 "净胜球档通常 30%% 以上，方向比单格稳。"
                                 % (c1 * 100, c2 * 100, c3 * 100))
                lines.append("")

            jqo = m.get("jq_odds") or {}
            if jqo:
                try:
                    jmap = {int(k): float(v) for k, v in jqo.items() if float(v) > 1.0}
                except Exception:
                    jmap = {}
                if len(jmap) == 8:
                    jp = m.get("jq_probs")
                    if not jp or len(jp) != 8:
                        jp = odds_probs([jmap[k] for k in range(8)])
                    jm = m.get("jq_model")
                    if not jm or len(jm) != 8:
                        jm = jq_dist(model_matrices(m).get("ens") or {})
                    jpick = m.get("jq_pick")
                    if jpick is None:
                        jpick = max(range(8), key=lambda k: jm[k])
                    low = min(range(8), key=lambda k: jmap[k])
                    parts = ["%s %s（模 %.0f%%%s）"
                             % (JQ_TXT[k], fmt_odds(jmap[k]), jm[k] * 100,
                                (" · 市 %.0f%%" % (jp[k] * 100)) if jp else "")
                             for k in range(8)]
                    tail = ("模型首选 %s %.0f%% · 市场最低 %s %s"
                            % (JQ_TXT[jpick], jm[jpick] * 100, JQ_TXT[low], fmt_odds(jmap[low])))
                    act = m.get("jq_result")
                    if act is None and sc:
                        act = jq_index(sum(sc))
                    if act is not None:
                        tail += " · 实际 %s → 模型%s · 市场%s" % (
                            JQ_TXT[act], "中" if act == jpick else "未中",
                            "中" if act == low else "未中")
                    lines.append("总进球（0—7+）：" + "；".join(parts) + "。")
                    lines.append(tail)
                    lines.append("")

            hf = self._hf_of(m)
            bq = self._bq_of(m)
            bqo = m.get("bq_odds")
            bqo = bqo if isinstance(bqo, list) and len(bqo) == 9 else None
            if hf:
                parts = []
                for i, c in enumerate(HF_VALUES):
                    o = bqo[i] if bqo and bqo[i] else None
                    parts.append("%s %.1f%%%s" % (
                        hf_label(c), hf[i] * 100, (" · 赔 %s" % fmt_odds(o)) if o else ""))
                order = sorted(range(9), key=lambda i: -hf[i])
                tail = ("模型前三 ①%s %.1f%% · ②%s %.1f%% · ③%s %.1f%%"
                        % (hf_label(HF_VALUES[order[0]]), hf[order[0]] * 100,
                           hf_label(HF_VALUES[order[1]]), hf[order[1]] * 100,
                           hf_label(HF_VALUES[order[2]]), hf[order[2]] * 100))
                mi = max(range(9), key=lambda i: bq[i]) if bq else None
                if mi is not None and bqo:
                    tail += " · 市场最低 %s %s" % (hf_label(HF_VALUES[mi]), fmt_odds(bqo[mi]))
                if hres in HF_VALUES:
                    tail += " · 实际 %s → 模型%s · 市场%s" % (
                        hf_label(hres),
                        "中" if hres == HF_VALUES[order[0]] else
                        ("前三" if hres in [HF_VALUES[k] for k in order[:3]] else "未中"),
                        "中" if mi is not None and hres == HF_VALUES[mi] else "未中")
                lines.append("半全场（9 档）：" + "；".join(parts) + "。")
                lines.append(tail)
            lines.append("")
            lines.append("赔率均为赛前封存；括号内“市”为赔率 Shin 去水，“模”为共识模型概率。")
            self.four_txt.configure(state="normal")
            self.four_txt.delete("1.0", "end")
            self.four_txt.insert("1.0", "\n".join(lines))
            self.four_txt.configure(state="disabled")

        def _four(self, data):
            self.tv6.delete(*self.tv6.get_children())
            self.four_map = {}
            W = ["主胜", "平局", "客胜"]
            rows = [m for m in (data or {}).get("matches", []) if m.get("probs")]
            rows.sort(key=lambda m: m.get("kickoff", ""))
            n_wdl = n_bf = n_jq = n_hf = 0
            first = None
            for i, m in enumerate(rows):
                odds = m.get("odds") or []
                dp = odds_probs(odds) if len(odds) == 3 else None
                pi = W.index(m["pick"]) if m.get("pick") in W else 0
                if dp:
                    n_wdl += 1
                    wdl = "%s %.0f%% 赔%s" % (W[pi], m["probs"][pi] * 100, fmt_odds(odds[pi]))
                else:
                    wdl = "—"
                bfo = m.get("bf_odds") or {}
                same = m.get("same_score") or m["pick_score"]
                same_p = m.get("same_score_p", m["pick_score_p"])
                bs = same.replace("-", ":")
                if bfo:
                    n_bf += 1
                bf = ("%s %.0f%% 赔%s" % (same, same_p * 100, fmt_odds(bfo[bs]))
                      if bs in bfo else "%s %.0f%%" % (same, same_p * 100))
                jqo = m.get("jq_odds") or {}
                jpick = m.get("jq_pick")
                if jqo:
                    n_jq += 1
                jq = "—"
                if jpick is not None and str(jpick) in jqo:
                    jq = "%s %.0f%% 赔%s" % (
                        JQ_TXT[jpick], m.get("jq_pick_p", 0) * 100,
                        fmt_odds(jqo[str(jpick)]))
                hf = self._hf_of(m)
                bqo = m.get("bq_odds")
                bqo = bqo if isinstance(bqo, list) and len(bqo) == 9 else None
                if bqo:
                    n_hf += 1
                hf_txt = "—"
                if hf:
                    hi = max(range(9), key=lambda k: hf[k])
                    o = bqo[hi] if bqo and bqo[hi] else None
                    hf_txt = "%s %.0f%%%s" % (
                        hf_label(HF_VALUES[hi]), hf[hi] * 100,
                        (" 赔%s" % fmt_odds(o)) if o else "")
                sc = split_score(m.get("score", ""))
                act = []
                if sc:
                    act.append("%d:%d" % sc)
                if m.get("jq_result") is not None:
                    act.append("总%s" % JQ_TXT[m["jq_result"]])
                if m.get("hf_result") in HF_VALUES:
                    act.append(hf_label(m["hf_result"]))
                oks = []
                if m.get("wdl_result"):
                    oks.append("胜" + ("✓" if m["wdl_result"] == W[pi] else "×"))
                if sc:
                    oks.append("比" + ("✓" if sc == score_tuple(same) else "×"))
                if m.get("jq_result") is not None:
                    oks.append("总" + ("✓" if m["jq_result"] == m.get("jq_pick") else "×"))
                if m.get("hf_result") in HF_VALUES:
                    oks.append("半" + ("✓" if m["hf_result"] == m.get("hf_pick") else "×"))
                good = sum(1 for x in oks if x.endswith("✓"))
                tag = "hit" if oks and good == len(oks) else ("odd" if i % 2 else "")
                iid = self.tv6.insert("", "end", values=(
                    m["no"], "%s vs %s" % (m["home"], m["away"]),
                    (m.get("kickoff", "") or "")[5:16],
                    wdl, bf, jq, hf_txt,
                    " · ".join(act) or "—", " ".join(oks) or "待赛"), tags=(tag,))
                self.four_map[iid] = m
                if first is None:
                    first = iid
            if rows:
                self.four_note.configure(
                    text=("%d 场 · 赔率覆盖：胜平负 %d · 比分 %d · 总进球 %d · 半全场 %d。"
                          "表中“赔”是模型首选那一档的赛前赔率；选中一场，下方列出四玩法全部档位、"
                          "去水概率、模型概率与市场最低赔率档。")
                    % (len(rows), n_wdl, n_bf, n_jq, n_hf))
            else:
                self.four_note.configure(text="当天还没有记录")
            if first:
                self.tv6.selection_set(first)
                self.tv6.focus(first)
                self._four_detail()
            else:
                self.four_title.configure(text="四玩法赔率明细")
                self.four_txt.configure(state="normal")
                self.four_txt.delete("1.0", "end")
                self.four_txt.insert("1.0", "选中上方一场比赛查看胜平负、比分、总进球、半全场的完整赔率。")
                self.four_txt.configure(state="disabled")

        # ---- 动作
        def act_update(self):
            day = self.current_day()

            def job():
                had = load_day(day)
                before = len(had["matches"]) if had else 0
                d0 = ensure_day(day)
                added = top_up_day(d0)
                ds = refresh_results(10)
                write_reports(day)
                data = load_day(day)
                ev = evaluate(data)
                total = len(data["matches"])
                tail = ("核对 %d 场 · 胜负 %d · 比分 %d" % (ev["n"], ev["hitw"], ev["hit1"])
                        if ev["n"] else "暂无赛果")
                try:
                    pro_daily(day, report=True, backtest=False)
                    log("四模型日报已刷新")
                except Exception as e:
                    log("四模型刷新失败：%s" % e)
                msg = "完成 %d 场 · %s" % (total, tail)
                log("完成 %d 场（新增 %d）· %s" % (total, total - before, tail))
                return msg
            self.run_bg(job, btn=self.btn_update, busy="更新中…",
                        done=lambda: open_path(report_of(day) or (REPORT_DIR / "汇总.html")))

        def act_pro_update(self):
            day = self.current_day()

            def job():
                if not load_day(day):
                    raise RuntimeError("这一天还没有记录，先更新赛程")
                pro_daily(day, report=True, backtest=False)
                snap = pro_snapshot(day) or {}
                rows = len((snap.get("result") or {}).get("matches") or [])
                return "四模型 %d 场" % rows
            self.run_bg(job, btn=self.btn_pro, busy="四模型计算中…")

        def act_pro_open(self):
            p = pro_report_of(self.current_day())
            if p:
                open_path(p)
            else:
                messagebox.showinfo("四模型", "这一天还没有四模型日报。\n先点“刷新四模型”生成。")

        def act_pro_backtest_open(self):
            p = pro_backtest_report()
            if p:
                open_path(p)
            else:
                messagebox.showinfo("四模型回测", "还没有回测页。\n周日的自动任务会生成，或运行 --pro-backtest。")

        def act_pro_score_rank(self):
            day = self.current_day()
            if not pro_snapshot(day):
                messagebox.showinfo("比分胜率排序",
                                    "这一天还没有四模型快照。\n先点“刷新四模型”生成。")
                return

            def job():
                pro_run("rank", "--date", day)
                snap = pro_snapshot(day) or {}
                rows = len((snap.get("result") or {}).get("matches") or [])
                return "比分排行 %d 场" % rows

            def done():
                p = pro_score_rank_report(day)
                if p:
                    open_path(p)

            self.run_bg(job, btn=self.btn_rank, done=done, busy="比分排序中…")

        def act_verify(self):
            day = self.current_day()

            def job():
                ds = refresh_results(15)
                write_reports(day)
                data = load_day(day)
                ev = evaluate(data) if data else {"n": 0}
                msg = "核对完成 · 更新 %d 天 · 今日已核对 %d 场" % (len(ds), ev["n"])
                log(msg)
                return msg
            self.run_bg(job, busy="核对中…")

        def act_report(self):
            day = self.current_day()

            def job():
                if not load_day(day):
                    raise RuntimeError("这一天还没有记录")
                write_reports(day)
                return "报告已生成"
            self.run_bg(job, busy="生成中…",
                        done=lambda: open_path(report_of(day) or (REPORT_DIR / "汇总.html")))

        def _rc_label(self, day):
            rec = load_recheck(day)
            if not rec:
                self.rc_note.configure(text="")
                return
            cnt = {k: sum(1 for x in rec["matches"] if x["changed"][k])
                   for k in ("pick", "score")}
            self.rc_note.configure(text=(
                "临场复核 %s · 未开赛 %d 场 · 胜平负改选 %d · 比分改选 %d · 点击查看复核页 ›"
                % (rec["at"][11:16], rec.get("pending", 0), cnt["pick"], cnt["score"])))

        def act_recheck_open(self):
            p = recheck_report_of(self.current_day())
            if p:
                open_path(p)
            else:
                messagebox.showinfo(
                    "临场复核",
                    "这一天还没有复核记录。\n每天 15:20 会自动用最新赔率复核未开赛场次。")

        def act_open(self):
            p = report_of(self.current_day())
            if p:
                open_path(p)
            else:
                open_path(REPORT_DIR / "汇总.html")

    log("图形界面启动")
    app = App(start_day, selftest=selftest, tab=tab)
    app.mainloop()


# ---------------------------------------------------------------- 入口
# ---------------------------------------------------------------- 回溯样本与口径回测
# 回溯样本单独落 数据/回溯/，不进日报、汇总和 GUI 的封存记录；只做概率与模拟记录。

# 竞彩比分 31 档：未单列的比分按方向归入“胜/平/负其它”
CANON = ["1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2",
         "5:0", "5:1", "5:2", "胜其它",
         "0:0", "1:1", "2:2", "3:3", "平其它",
         "0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4",
         "0:5", "1:5", "2:5", "负其它"]
BIDX = {k: i for i, k in enumerate(CANON)}
TAIL_OF = {0: "胜其它", 1: "平其它", 2: "负其它"}


def bucket_of(h, g):
    k = "%d:%d" % (h, g)
    return k if k in BIDX else TAIL_OF[0 if h > g else (1 if h == g else 2)]


def bucket_score(txt):
    """把 "2-1" / "2:1" 这类比分落到 31 档键；无法解析返回 None。"""
    if not txt:
        return None
    t = str(txt).strip().replace("：", ":").replace("-", ":")
    parts = t.split(":")
    if len(parts) != 2:
        return None
    try:
        h, g = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return bucket_of(h, g)


# 固定 9×9 格序，供权重网格搜索复用同一组向量
CELLS = sorted(dc_matrix(1.0, 1.0, 0.0, MAXG).keys())
CELL_DIR = [0 if h > g else (1 if h == g else 2) for h, g in CELLS]
CELL_B = [BIDX[bucket_of(h, g)] for h, g in CELLS]
NB = len(CANON)
SRC_KEYS = ("dc", "poi", "mkt", "tgs")


# ------------------------------------------------------------ 回溯抓取

def daterange(a, b):
    d0 = dt.date.fromisoformat(a)
    d1 = dt.date.fromisoformat(b)
    cur = d0
    while cur <= d1:
        yield cur.strftime("%Y-%m-%d")
        cur += dt.timedelta(days=1)


def backfill(start, end, force=False, gap=0.8):
    BACK_DIR.mkdir(parents=True, exist_ok=True)
    try:
        prepare_model()
    except Exception as e:
        log("ρ 校准跳过：%s" % e)
    days = list(daterange(start, end))
    done = skip = empty = 0
    n_match = 0
    for day in days:
        p = BACK_DIR / ("%s.json" % day)
        if p.exists() and not force:
            try:
                n_match += len(json.loads(p.read_text(encoding="utf-8")).get("matches", []))
            except Exception:
                pass
            skip += 1
            continue
        try:
            rows = fetch_day(day, quiet=True, attempts=2, wait=3)
        except Exception as e:
            empty += 1
            log("%s 无可回溯赛程（%s）" % (day, str(e)[:60]))
            continue
        kept = [predict_match(dict(r)) for r in rows]
        # 500 的历史页面有保留期，超期会退回当前在售列表——用开赛日期核对，
        # 避免把还没赛的比赛当成历史样本写进回溯库。
        nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        onday = [m for m in kept if str(m.get("kickoff", ""))[:10] in (day, nxt)]
        if len(onday) < max(1, len(kept) // 2) or not any(m.get("score") for m in onday):
            empty += 1
            log("%s 页面已超出保留期（返回 %d 场，其中当日 %d 场、有赛果 %d），跳过" % (
                day, len(kept), len(onday), sum(1 for m in onday if m.get("score"))))
            continue
        rec = {"date": day, "backfilled_at": now_str(), "source": SOURCE,
               "note": "回溯样本：赔率为页面留存的历史赔率，非当日赛前封存",
               "matches": onday}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        Path.replace(tmp, p)
        with_res = sum(1 for m in onday if m.get("score"))
        done += 1
        n_match += len(onday)
        log("%s：%d 场（有赛果 %d）" % (day, len(onday), with_res))
        time.sleep(gap)
    log("回溯完成：新抓 %d 天，已有跳过 %d 天，空档 %d 天，累计 %d 场" % (
        done, skip, empty, n_match))
    return done


# ------------------------------------------------------------ 样本装载

def load_bt_samples(sealed_too=True):
    out = []
    files = []
    # 同一场次优先用当日赛前封存的记录，回溯版本只填补没有封存的历史日
    if sealed_too and DATA_DIR.exists():
        files += [p for p in sorted(DATA_DIR.glob("*.json"))
                  if p.parent == DATA_DIR]
    if BACK_DIR.exists():
        files += sorted(BACK_DIR.glob("*.json"))
    seen = set()
    for p in files:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        day = rec.get("date") or p.stem
        if not day or len(day) != 10 or day[4] != "-":
            continue
        for m in rec.get("matches", []):
            if not m.get("score") or not m.get("lambda"):
                continue
            key = (day, m.get("no"))
            if key in seen:
                continue
            seen.add(key)
            m["_day"] = day
            m["_back"] = p.parent == BACK_DIR
            out.append(m)
    out.sort(key=lambda x: (x["_day"], x.get("no", "")))
    return out


# ------------------------------------------------------------ 概率与选号

def bucket_dist(matrix):
    d = [0.0] * len(CANON)
    for (h, g), p in matrix.items():
        d[BIDX[bucket_of(h, g)]] += p
    s = sum(d)
    return [x / s for x in d] if s > 0 else None


def dir_pick(matrix, probs=None):
    """预测胜负方向内概率最高的格子，返回 ("h-g", p, 方向)。"""
    px = matrix_1x2(matrix)
    idx = px.index(max(px))
    cond = ((lambda k: k[0] > k[1]) if idx == 0 else
            (lambda k: k[0] == k[1]) if idx == 1 else (lambda k: k[0] < k[1]))
    (h, g), p = max(((c, v) for c, v in matrix.items() if cond(c)), key=lambda kv: kv[1])
    return "%d-%d" % (h, g), p, ["主胜", "平局", "客胜"][idx]


def global_pick(matrix):
    (h, g), p = max(matrix.items(), key=lambda kv: kv[1])
    return "%d-%d" % (h, g), p


def topk_cells(matrix, k):
    return ["%d-%d" % c for c, _ in sorted(matrix.items(), key=lambda kv: -kv[1])[:k]]


def market_odds_map(m):
    """31 档赔率 → 档名，用于虚拟回报计算。"""
    out = {}
    for k, v in (m.get("bf_odds") or {}).items():
        try:
            if ":" in str(k):
                h, g = (int(x) for x in str(k).split(":"))
                out[bucket_of(h, g)] = float(v)
            elif k in TAIL_OF.values():
                out[k] = float(v)
        except Exception:
            continue
    return out


# ------------------------------------------------------------ 统计

def wilson(k, n, z=1.96):
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - half), min(1.0, c + half))


def mcnemar_exact(b, c):
    """配对二项精确检验：b=只有 A 中的场数，c=只有 B 中的场数。"""
    n = b + c
    if n == 0:
        return 1.0
    lo = min(b, c)
    tail = sum(math.comb(n, i) for i in range(lo + 1)) / (2.0 ** n)
    return min(1.0, 2 * tail)


def logloss(acts, dists):
    """acts 为 31 档下标；返回平均负对数似然（越大越差）。"""
    eps = 1e-6
    return -sum(math.log(max(d[a], eps)) for a, d in zip(acts, dists)) / len(acts)


def brier(acts, dists):
    return sum(sum((p - (1.0 if a == i else 0.0)) ** 2
                   for i, p in enumerate(d)) for a, d in zip(acts, dists)) / len(acts)


# ------------------------------------------------------------ 回测

STRATEGIES = [
    ("现口径·模型同向", "ens_dir"),
    ("模型全场最高", "ens_all"),
    ("纯DC同向", "dc_dir"),
    ("纯市场同向", "mkt_dir"),
    ("市场全场最高", "mkt_all"),
    ("融合(0.5/0.5)同向", "fuse_dir"),
]


def build_bt_rows(samples):
    rows = []
    for m in samples:
        mx = model_matrices(m)
        ens, dc, mkt = mx.get("ens"), mx.get("dc"), mx.get("mkt")
        if not ens:
            continue
        act = bucket_score(m["score"])
        if act is None:
            continue
        fuse = None
        if mkt:
            base = dc or ens
            mix = {c: math.sqrt(max(base[c], 1e-9) * max(mkt[c], 1e-9)) for c in base}
            s = sum(mix.values())
            fuse = {c: v / s for c, v in mix.items()} if s > 0 else None
        picks = {}
        picks["ens_dir"] = dir_pick(ens)
        picks["ens_all"] = global_pick(ens) + ("",)
        if dc:
            picks["dc_dir"] = dir_pick(dc)
        if mkt:
            picks["mkt_dir"] = dir_pick(mkt)
            picks["mkt_all"] = global_pick(mkt) + ("",)
        if fuse:
            picks["fuse_dir"] = dir_pick(fuse)
        cov = {"TOP3": topk_cells(ens, 3), "TOP5": topk_cells(ens, 5),
               "TOP1": picks["ens_dir"][0:1], "TOP10": topk_cells(ens, 10)}
        dists = {}
        for nm, mm in (("dc", dc), ("mkt", mkt), ("ens", ens), ("fuse", fuse)):
            if mm:
                d = bucket_dist(mm)
                if d:
                    dists[nm] = d
        rows.append({"m": m, "act": act, "picks": picks, "cov": cov,
                     "dists": dists, "ens": ens, "day": m["_day"],
                     "id": (m["_day"], m.get("no", "")),
                     "act_i": BIDX[act],
                     "src": {k: [mx[k].get(c, 0.0) for c in CELLS]
                             for k in ("dc", "poi", "mkt", "tgs") if k in mx},
                     "odds": market_odds_map(m)})
    return rows


def summarize_backtest(rows):
    n = len(rows)
    base_hit = {r["id"]: bucket_score(r["picks"]["ens_dir"][0]) == r["act"] for r in rows}
    single = []
    for label, key in STRATEGIES:
        sub = [r for r in rows if key in r["picks"]]
        if not sub:
            continue
        hits = {r["id"]: bucket_score(r["picks"][key][0]) == r["act"] for r in sub}
        k = sum(hits.values())
        lo, hi = wilson(k, len(sub))
        paid = [r["odds"].get(bucket_score(r["picks"][key][0])) or 0.0 for r in sub]
        roi = (sum(o * hits[r["id"]] for o, r in zip(paid, sub)) / len(sub) - 1
               if any(paid) else None)
        if key == "ens_dir":
            mcr = None
        else:
            b = sum(1 for r in sub if hits[r["id"]] and not base_hit[r["id"]])
            c = sum(1 for r in sub if not hits[r["id"]] and base_hit[r["id"]])
            mcr = (b, c, mcnemar_exact(b, c))
        single.append({"label": label, "key": key, "n": len(sub), "hit": k,
                       "rate": k / len(sub), "lo": lo, "hi": hi,
                       "exp": sum(r["picks"][key][1] for r in sub) / len(sub),
                       "roi": roi, "mcr": mcr,
                       "days": sorted({r["day"] for r in sub})})
    cov = []
    for name in ("TOP1", "TOP3", "TOP5", "TOP10"):
        hit = 0
        prob = 0.0
        pay = 0.0
        priced = 0
        kk = 0
        for r in rows:
            buckets = []
            for c in r["cov"][name]:
                b = bucket_score(c)
                if b and b not in buckets:
                    buckets.append(b)
            kk = max(kk, len(buckets))
            bd = r["dists"].get("ens") or bucket_dist(r["ens"])
            prob += sum(bd[BIDX[b]] for b in buckets)
            if r["act"] in buckets:
                hit += 1
            if r["odds"]:
                priced += 1
                pay += sum(r["odds"].get(b, 0.0)
                           for b in buckets if b == r["act"])
        cost = kk * n
        lo, hi = wilson(hit, n)
        cov.append({"name": name, "k": kk, "n": n, "hit": hit, "rate": hit / n,
                    "lo": lo, "hi": hi, "exp": prob / n,
                    "cost_per_hit": (cost / hit) if hit else None,
                    "roi": (pay / cost - 1) if priced == n and cost else None})
    dist = []
    for nm, label in (("dc", "纯模型 DC"), ("mkt", "纯市场比分赔率"),
                      ("ens", "现行集成"), ("fuse", "模型×市场几何平均")):
        sub = [r for r in rows if nm in r["dists"]]
        if len(sub) < 30:
            continue
        acts = [r["act_i"] for r in sub]
        dists = [r["dists"][nm] for r in sub]
        dist.append({"label": label, "n": len(sub),
                     "nll": logloss(acts, dists), "brier": brier(acts, dists)})
    strat = []
    for lo_, hi_, lab in ((0, 0.08, "<8%"), (0.08, 0.10, "8–10%"), (0.10, 9, "≥10%")):
        sub = [r for r in rows if "ens_dir" in r["picks"]
               and lo_ <= r["picks"]["ens_dir"][1] < hi_]
        if not sub:
            continue
        hit = sum(1 for r in sub if bucket_score(r["picks"]["ens_dir"][0]) == r["act"])
        strat.append({"lab": lab, "n": len(sub), "hit": hit,
                      "rate": hit / len(sub),
                      "exp": sum(r["picks"]["ens_dir"][1] for r in sub) / len(sub)})
    wdl_hit = sum(1 for r in rows
                  if r["m"].get("wdl_result") and r["m"].get("pick") == r["m"]["wdl_result"])
    return {"n": n, "single": single, "cov": cov, "dist": dist,
            "strat": strat, "wdl": wdl_hit,
            "days": sorted({r["day"] for r in rows}),
            "back": sum(1 for r in rows if r["m"].get("_back"))}


# ------------------------------------------------------------ 权重网格搜索

MKT_LEVELS = [0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0]
TGS_LEVELS = [0.0, 0.15, 0.25, 0.4, 0.55]
DC_SHARES = [1.0, 0.75, 0.5, 0.25, 0.0]


def combo_stats(rows, w):
    """按权重合成每场分布，分别统计训练段与留出段的 log/Brier/同向命中。"""
    acc = {True: [0.0, 0.0, 0, 0.0, 0], False: [0.0, 0.0, 0, 0.0, 0]}
    # [累计 log, 累计 brier, 命中, 累计首选概率, 场次]
    for r in rows:
        src = r["src"]
        tw = sum(w[k] for k in src)
        if tw <= 0:
            continue
        vec = [0.0] * len(CELLS)
        for k, arr in src.items():
            wk = w[k] / tw
            if wk:
                vec = [v + wk * a for v, a in zip(vec, arr)]
        bd = [0.0] * NB
        dirs = [0.0, 0.0, 0.0]
        best = [-1.0, -1.0, -1.0]
        bi = [0, 0, 0]
        for i, v in enumerate(vec):
            bd[CELL_B[i]] += v
            d = CELL_DIR[i]
            dirs[d] += v
            if v > best[d]:
                best[d] = v
                bi[d] = i
        s = sum(bd)
        if s <= 0:
            continue
        a = r["act_i"]
        sel = bi[max(range(3), key=lambda d: dirs[d])]
        sel_i = CELL_B[sel]
        p_sel = vec[sel] / s
        br = 0.0
        for i, p in enumerate(bd):
            e = (p / s) - (1.0 if i == a else 0.0)
            br += e * e
        side = r["train"]
        acc[side][0] += -math.log(max(bd[a] / s, 1e-6))
        acc[side][1] += br
        acc[side][2] += 1 if sel_i == a else 0
        acc[side][3] += p_sel
        acc[side][4] += 1
    out = {}
    for side, (ll, br, hit, ex, cnt) in acc.items():
        if cnt < 30:
            return None
        out[side] = {"n": cnt, "nll": ll / cnt, "brier": br / cnt,
                     "hit": hit, "rate": hit / cnt, "exp": ex / cnt}
    return out


def weight_grid(rows, split_day):
    for r in rows:
        r["train"] = r["day"] <= split_day
    results = []
    for mkt in MKT_LEVELS:
        for tgs in TGS_LEVELS:
            rest = 1.0 - mkt - tgs
            if rest < -1e-9:
                continue
            for share in DC_SHARES:
                w = {"mkt": mkt, "tgs": tgs, "dc": rest * share,
                     "poi": rest * (1.0 - share)}
                st = combo_stats(rows, w)
                if not st:
                    continue
                results.append((st[True]["nll"], st[False]["nll"], w,
                                st[True], st[False]))
    results.sort(key=lambda x: x[0])
    cur = {"dc": ENS_W["dc"], "poi": ENS_W["poi"],
           "mkt": ENS_W["mkt"], "tgs": ENS_W["tgs"]}
    cur_norm = {k: v / sum(cur.values()) for k, v in cur.items()}
    st = combo_stats(rows, cur_norm)
    cur_row = None
    if st:
        cur_row = (st[True]["nll"], st[False]["nll"], cur_norm, st[True], st[False])
    return results[:8], len([r for r in rows if r["train"]]), \
        len([r for r in rows if not r["train"]]), cur_row



# ------------------------------------------------------------ 输出

def render_backtest(rows, S, grid, out_path, split_day, cur=None, rho_rec=None):
    n = S["n"]
    days = S["days"]
    pct = lambda v: "—" if v is None else "%.1f%%" % (v * 100)

    def roi_cell(v):
        if v is None:
            return '<span class="badge">样本不足</span>'
        cls = "ok" if v > 0 else ("mid" if v > -0.06 else "no")
        return '<span class="badge %s">%+.1f%%</span>' % (cls, v * 100)

    cards = f"""
  <div class="card"><span>回溯样本</span><b>{n}</b><em>场已完赛</em></div>
  <div class="card"><span>覆盖日期</span><b>{len(days)}</b><em>{days[0][5:]} ~ {days[-1][5:]}</em></div>
  <div class="card"><span>回溯/封存</span><b>{S['back']}/{n - S['back']}</b><em>场</em></div>
  <div class="card"><span>现口径命中</span><b>{S['single'][0]['hit'] if S['single'] else '—'}</b><em>{pct(S['single'][0]['rate']) if S['single'] else '—'}</em></div>
  <div class="card"><span>胜平负命中</span><b>{S['wdl']}</b><em>{pct(S['wdl'] / n)}</em></div>"""

    srows = []
    for s in S["single"]:
        mc = "—"
        if s["mcr"]:
            b, c, p = s["mcr"]
            mc = "%d/%d · p=%.3f" % (b, c, p)
        srows.append(f"""<tr>
  <td>{esc(s['label'])}</td><td class="act">{s['hit']}/{s['n']}</td>
  <td class="act"><b>{pct(s['rate'])}</b><br><small>{pct(s['lo'])}–{pct(s['hi'])}</small></td>
  <td class="act">{pct(s['exp'])}</td>
  <td class="act">{roi_cell(s['roi'])}</td>
  <td class="act">{esc(mc)}</td></tr>""")

    crows = []
    for c in S["cov"]:
        crows.append(f"""<tr><td>比分 {c['name']} 覆盖</td>
  <td class="act">{c['hit']}/{c['n']}</td><td class="act"><b>{pct(c['rate'])}</b><br>
  <small>{pct(c['lo'])}–{pct(c['hi'])}</small></td>
  <td class="act">{pct(c['exp'])}</td>
  <td class="act">{c['k']} 注/场</td>
  <td class="act">{('%.1f 注/中' % c['cost_per_hit']) if c['cost_per_hit'] else '—'}</td>
  <td class="act">{roi_cell(c['roi'])}</td></tr>""")

    drows = []
    for d in S["dist"]:
        base_nll = math.log(len(CANON))
        cls = "ok" if d["nll"] < base_nll else "no"
        drows.append(f"""<tr><td>{esc(d['label'])}</td><td class="act">{d['n']}</td>
  <td class="act"><span class="badge {cls}">−{d['nll']:.3f}</span><br><small>盲猜 −{base_nll:.3f}</small></td>
  <td class="act">{d['brier']:.3f}</td></tr>""")

    def grow(tr_nll, te_nll, w, tr, te, label=""):
        return f"""<tr><td class="act">{" / ".join('%.2f' % w[k] for k in ('dc', 'poi', 'mkt', 'tgs'))}{esc(label)}</td>
  <td class="act">−{tr_nll:.3f}</td>
  <td class="act">{pct(tr['rate'])}（{tr['hit']}/{tr['n']}）</td>
  <td class="act"><b>−{te_nll:.3f}</b></td>
  <td class="act">{pct(te['rate'])}（{te['hit']}/{te['n']}）</td></tr>"""

    trows = [grow(*g) for g in grid]
    if cur:
        seen = any(abs(g[2]["mkt"] - cur[2]["mkt"]) < 1e-9
                   and abs(g[2]["tgs"] - cur[2]["tgs"]) < 1e-9 for g in grid)
        trows.append(grow(*cur, label=" ← 现行权重" if not seen else ""))

    hrows = []
    for r in rows[:80]:
        m = r["m"]
        p, pr, dirn = r["picks"]["ens_dir"]
        ok = "hit" if r["act"] == bucket_score(p) else "miss"
        hrows.append(f"""<tr><td class="no">{esc(m.get('no', ''))}</td>
  <td class="kick">{r['day'][5:]}</td>
  <td class="teams">{esc(m.get('home', ''))}<span>vs</span>{esc(m.get('away', ''))}</td>
  <td class="score"><b>{esc(m.get('score', ''))}</b></td>
  <td class="score"><b>{esc(p)}</b><em class="{ok}">{pr * 100:.1f}%</em></td>
  <td class="act">{esc(dirn)}</td></tr>""")

    rrows = []
    if rho_rec:
        best_ens = max(c[2] for c in rho_rec["curve"])
        best_dc = max(c[1] for c in rho_rec["curve"])
        for rv, ll_dc, ll_ens in rho_rec["curve"]:
            if round(rv * 100) % 2 and abs(rv - rho_rec["rho"]) > 5e-3 \
                    and abs(rv - rho_rec["hat_dc"]) > 5e-3:
                continue
            dlt = ll_ens - best_ens
            marks = []
            if abs(rv - rho_rec["rho"]) < 5e-3:
                marks.append("采用")
            if abs(rv - rho_rec["hat_dc"]) < 5e-3:
                marks.append("DC 单源最优")
            cls = "ok" if dlt > -0.01 else ("mid" if dlt >= -1.92 else "no")
            cie = rho_rec["ci"][0] - 1e-9 <= rv <= rho_rec["ci"][1] + 1e-9
            cid = rho_rec["ci_dc"][0] - 1e-9 <= rv <= rho_rec["ci_dc"][1] + 1e-9
            rrows.append(f"""<tr><td class="act">{rv:+.2f}{(' <b>← ' + esc(' · '.join(marks)) + '</b>') if marks else ''}</td>
  <td class="act">−{abs(ll_ens):,.1f}</td>
  <td class="act"><span class="badge {cls}">Δ{dlt:+.1f}</span></td>
  <td class="act"><span class="badge {'ok' if ll_dc - best_dc > -0.01 else ('mid' if ll_dc - best_dc >= -1.92 else 'no')}">Δ{ll_dc - best_dc:+.1f}</span></td>
  <td class="act">集成 {"" if cie else "不"}在区间 · DC {"" if cid else "不"}在区间</td></tr>""")
    rho_txt = (f"""<div class="h2">六、Dixon-Coles ρ 校准（{rho_rec['n']} 场，ρ 与 λ 联合重解）</div>
<div class="hint">每个 ρ 都重新反解期望进球 λ（热启动沿用上一点，避免优化器噪声把曲线抖成锯齿），再算两套实际比分那一格的对数似然：<b>集成列</b>是最终交付的四源加权分布，采用值按它选；<b>DC 单源列</b>只看模型自身的低比分修正，是 ρ 这个参数自己的似然。两列的 Δ 都相对各自最优，Δ≥−1.92 近似 95% 区间。
本次采用 ρ={rho_rec['rho']:+.2f}（集成分布最优 {rho_rec['hat']:+.2f}，区间 [{rho_rec['ci'][0]:+.2f}, {rho_rec['ci'][1]:+.2f}]；DC 单源 MLE {rho_rec['hat_dc']:+.2f}，区间 [{rho_rec['ci_dc'][0]:+.2f}, {rho_rec['ci_dc'][1]:+.2f}]；小样本收缩系数 {rho_rec['shrink']:.2f}）。
两个口径的曲线都很平——整条网格的对数似然跨度只有几个单位，摊到 {rho_rec['n']} 场就是万分位，逐场配对检验里 −0.02 到 −0.15 这段在统计上分不出高下。所以采用值只是集成分布上的最高点，不要当成"真值"；等样本再攒两周、区间收窄之前，这个数还会漂。已封存的历史记录仍带各自赛前的 ρ，不会被这次校准改写；新抓取场次用当前值。</div>
<table><colgroup><col style="width:24%"><col style="width:19%"><col style="width:16%"><col style="width:19%"><col style="width:22%"></colgroup>
<thead><tr><th>ρ</th><th>集成分布比分格对数似然</th><th>与最优差</th><th>DC 单源差</th><th>95% 区间（集成 / DC）</th></tr></thead>
<tbody>{''.join(rrows)}</tbody></table>""" if rrows else "")

    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>竞彩比分口径回测 · {days[0]}~{days[-1]}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><div><h1>比分口径配对回测</h1>
<div class="sub">同一批 {n} 场已完赛比赛上逐场对比各种选号口径；差异用 McNemar 精确检验给出 p 值</div></div>
<div class="stamp">生成 {now_str()}<br><b>{len(days)} 个比赛日</b></div></header>
<div class="cards">{cards}</div>
<div class="rcb">⚠️ <b>样本性质：</b>回溯场次的赔率取自 500.com 历史页面留存，属于开赛前的最终赔率而非我们当日封存的那一版；因此可用于比较口径优劣，不能等同于每日实盘时点。<b>只做概率与模拟记录，不涉及任何投注。</b></div>

<div class="h2">一、单格比分口径对比</div>
<div class="hint">命中率区间为 Wilson 95%；“期望”是所选格子概率的平均值，用来检验模型是否自洽（命中率≈期望即校准良好）；虚拟回报按所选格子的市场赔率计算，每场 1 注，平注。</div>
<table><colgroup><col style="width:24%"><col style="width:10%"><col style="width:14%"><col style="width:11%"><col style="width:14%"><col style="width:27%"></colgroup>
<thead><tr><th>口径</th><th>命中/场次</th><th>命中率(95%区间)</th><th>期望概率</th><th>虚拟回报</th><th>与现口径不一致场数 b/c · p 值</th></tr></thead>
<tbody>{''.join(srows)}</tbody></table>

<div class="h2">二、覆盖口径：多买几格能换来什么</div>
<div class="hint">成本按注数线性增加，“注/中”是平均每命中一场需要投入的注数；覆盖率上升不代表单注更值。“期望”是覆盖格子概率之和的平均，可与命中率对照看校准。</div>
<table><colgroup><col style="width:20%"><col style="width:11%"><col style="width:15%"><col style="width:11%"><col style="width:11%"><col style="width:15%"><col style="width:17%"></colgroup>
<thead><tr><th>口径</th><th>命中/场次</th><th>命中率(95%区间)</th><th>期望</th><th>成本</th><th>注/中</th><th>虚拟回报</th></tr></thead>
<tbody>{''.join(crows)}</tbody></table>

<div class="h2">三、概率源对比（31 档整分布）</div>
<div class="hint">log-loss 越高越好（取负后越小越好），盲猜基准 −ln(31)=−3.434；Brier 为 31 档多分类平方误差，越低越好。</div>
<table><colgroup><col style="width:30%"><col style="width:12%"><col style="width:34%"><col style="width:24%"></colgroup>
<thead><tr><th>概率源</th><th>样本</th><th>平均 log</th><th>多分类 Brier</th></tr></thead>
<tbody>{''.join(drows)}</tbody></table>

<div class="h2">四、集成权重重搜（{esc(split_day)} 之后为留出样本）</div>
<div class="hint">权重按 (dc/poi/mkt/tgs) 网格搜索，先在训练段取 log-loss 最优的 8 组，再看留出段是否守得住。留出段同样本内最优的差距才是可信的量级。</div>
<table><colgroup><col style="width:26%"><col style="width:15%"><col style="width:21%"><col style="width:16%"><col style="width:22%"></colgroup>
<thead><tr><th>dc/poi/mkt/tgs</th><th>训练 log</th><th>训练同向命中</th><th>留出 log</th><th>留出同向命中</th></tr></thead>
<tbody>{''.join(trows) or '<tr><td colspan="5">样本不足，未做权重搜索</td></tr>'}</tbody></table>
<div class="hint">现行权重：{esc(" / ".join('%.2f' % ENS_W[k] for k in ('dc', 'poi', 'mkt', 'tgs')))}</div>

<div class="h2">五、现口径按置信度分层</div>
<table><colgroup><col style="width:20%"><col style="width:14%"><col style="width:16%"><col style="width:22%"><col style="width:28%"></colgroup>
<thead><tr><th>首选概率档</th><th>场次</th><th>命中</th><th>命中率</th><th>层内期望</th></tr></thead>
<tbody>{''.join(f'<tr><td>{esc(x["lab"])}</td><td class="act">{x["n"]}</td><td class="act">{x["hit"]}</td><td class="act"><b>{pct(x["rate"])}</b></td><td class="act">{pct(x["exp"])}</td></tr>' for x in S['strat'])}</tbody></table>

{rho_txt}
<div class="h2">七、逐场明细（前 80 场）</div>
<table><colgroup><col style="width:9%"><col style="width:8%"><col style="width:30%"><col style="width:11%"><col style="width:20%"><col style="width:12%"></colgroup>
<thead><tr><th>场次</th><th>日期</th><th>对阵</th><th>赛果</th><th>现口径首选</th><th>预测方向</th></tr></thead>
<tbody>{''.join(hrows)}</tbody></table>

<footer>
<b>读法：</b>单格比分命中率的物理上限就在 10%–13% 附近——31 档里最高的一格通常只有 8%–14%。换选号规则不会把上限抬高，这次回测要回答的是"现口径有没有明显更差的选法"和"覆盖率/成本怎么换"。<br>
<b>判定：</b>p&lt;0.05 才说得上差异；p 在 0.05–0.2 之间只算方向性线索，需要继续攒样本。命中率与期望概率接近说明模型校准没问题，低的是玩法本身而不是估计。<br>
<b>边界：</b>本工具只做历史数据模拟与概率记录，不预测结果、不代下单。
</footer>
</div></body></html>"""
    out_path.write_text(html, encoding="utf-8")
    return html


def write_backtest_csv(rows, path):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期", "场次", "赛事", "主队", "客队", "开赛", "赛果", "样本类型",
                    "现口径比分", "现口径概率", "方向", "命中",
                    "模型全场最高", "市场同向", "市场第一档概率", "融合比分", "融合概率",
                    "TOP3", "TOP5", "首选赔率"])
        for r in rows:
            m = r["m"]
            p, pr, dirn = r["picks"]["ens_dir"]
            mk = r["picks"].get("mkt_dir", ("", 0, ""))
            fu = r["picks"].get("fuse_dir", ("", 0, ""))
            w.writerow([r["day"], m.get("no", ""), m.get("league", ""),
                        m.get("home", ""), m.get("away", ""), m.get("kickoff", ""),
                        m.get("score", ""), "回溯" if m.get("_back") else "封存",
                        p, "%.4f" % pr, dirn,
                        "中" if bucket_score(p) == r["act"] else "未中",
                        global_pick(r["ens"])[0], mk[0], "%.4f" % mk[1] if mk[1] else "",
                        fu[0], "%.4f" % fu[1] if fu[1] else "",
                        " ".join(r["cov"]["TOP3"]), " ".join(r["cov"]["TOP5"]),
                        r["odds"].get(bucket_score(p), "")])


def run_backtest(split_frac=0.6, open_after=False):
    samples = load_bt_samples()
    if len(samples) < 40:
        log("样本仅 %d 场，先跑 --backfill 抓历史" % len(samples))
    rho_rec = refresh_rho(samples)
    rows = build_bt_rows(samples)
    S = summarize_backtest(rows)
    ordered = sorted({r["day"] for r in rows})
    split_day = ordered[max(0, int(len(ordered) * split_frac) - 1)] if ordered else ""
    grid, ntr, nte, cur = (weight_grid(rows, split_day) if split_day else ([], 0, 0, None))
    out = REPORT_DIR / "比分口径回测.html"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    render_backtest(rows, S, grid, out, split_day, cur, rho_rec)
    csv_path = DATA_DIR / "比分口径回测明细.csv"
    write_backtest_csv(rows, csv_path)
    cov = {c["name"]: c for c in S["cov"]}
    BT_FILE.write_text(json.dumps({
        "at": now_str(), "n": S["n"], "days": S["days"],
        "top1_rate": cov["TOP1"]["rate"], "top3_rate": cov["TOP3"]["rate"],
        "top5_rate": cov["TOP5"]["rate"], "top10_rate": cov["TOP10"]["rate"],
        "top3_cost": cov["TOP3"]["cost_per_hit"], "top5_cost": cov["TOP5"]["cost_per_hit"],
        "single": {s["key"]: {"hit": s["hit"], "n": s["n"], "rate": s["rate"],
                              "exp": s["exp"], "p": (s["mcr"][2] if s["mcr"] else None)}
                   for s in S["single"]},
        "dist": {d["label"]: d["nll"] for d in S["dist"]},
        "rho": ({"rho": rho_rec["rho"], "hat": rho_rec["hat"], "ci": rho_rec["ci"],
                 "hat_dc": rho_rec["hat_dc"], "ci_dc": rho_rec["ci_dc"],
                 "shrink": rho_rec["shrink"],
                 "n": rho_rec["n"], "at": rho_rec["at"]} if rho_rec else None),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log("样本 %d 场 / %d 个比赛日（回溯 %d、封存 %d）" % (
        S["n"], len(S["days"]), S["back"], S["n"] - S["back"]))
    for s in S["single"]:
        mc = "" if not s["mcr"] else "  McNemar b=%d c=%d p=%.3f" % s["mcr"]
        log("  %-16s %3d/%3d = %5.1f%%  期望 %4.1f%%%s" % (
            s["label"], s["hit"], s["n"], s["rate"] * 100, s["exp"] * 100, mc))
    log("  覆盖：" + "  ".join("%s %.1f%%" % (c["name"], c["rate"] * 100) for c in S["cov"]))
    log("  概率源：" + "  ".join("%s log=−%.3f" % (d["label"], d["nll"]) for d in S["dist"]))
    if grid:
        best = grid[0]
        log("  权重重搜（训练 %d 场 / 留出 %d 场，切分日 %s）" % (ntr, nte, split_day))
        log("    训练最优 %s → 训练 log −%.3f，留出 log −%.3f、同向命中 %.1f%%" % (
            "/".join('%.2f' % best[2][k] for k in ("dc", "poi", "mkt", "tgs")),
            best[0], best[1], best[4]["rate"] * 100))
        if cur:
            log("    现行权重 %s → 训练 log −%.3f，留出 log −%.3f、同向命中 %.1f%%" % (
                "/".join('%.2f' % cur[2][k] for k in ("dc", "poi", "mkt", "tgs")),
                cur[0], cur[1], cur[4]["rate"] * 100))
    log("报告：%s" % out)
    log("明细：%s" % csv_path)
    if open_after:
        import subprocess
        subprocess.Popen(["open", str(out)])
    return S, rows, out


def main(argv):
    ensure_dirs()
    args = argv[1:]
    if not args:
        prepare_model()
        run_gui()
        return 0
    cmd = args[0]
    try:
        if cmd == "--daily":
            daily(args[1] if len(args) > 1 and not args[1].startswith("-") else None)
        elif cmd == "--fetch":
            day = args[1] if len(args) > 1 else today_str()
            prepare_model()
            data = ensure_day(day, allow_late="--late" in args)
            write_reports(day)
            log("封存 %s：%d 场" % (day, len(data["matches"])))
        elif cmd == "--verify":
            ds = refresh_results(15)
            if any(DATA_DIR.glob("*.json")):
                write_reports(None)
            if PARLAY_FILE.exists() and settle_parlays():
                render_parlays()
            log("核对完成：更新 %d 个比赛日" % len(ds))
        elif cmd == "--parlay":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            b = make_parlay(day, system=11 if "--4x11" in args else None)
            settle_parlays()
            p = render_parlays()
            open_path(p)
            log("串关 %s：%s，%.2f 倍，模型联合概率 %.1f%%，期望 %+.1f%%" % (
                b["id"], b["play"], b["odds"], b["p"] * 100,
                (b["p"] * b["odds"] - 1) * 100))
        elif cmd == "--parlay-settle":
            settle_parlays()
            p = render_parlays()
            log("串关核对完成：%s" % p)
        elif cmd == "--report":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            if day in ("all", "全部"):
                day = None
            p = write_reports(day)
            open_path(p)
        elif cmd == "--recheck":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            rec, p = run_recheck(day)
            write_reports(day)
            log("临场复核报告：%s" % p)
            if "--open" in args:
                open_path(p)
        elif cmd == "--backfill":
            pre = [a for a in args[1:] if not a.startswith("-")]
            if len(pre) >= 2:
                s, e = pre[0], pre[1]
            elif len(pre) == 1:
                s = e = pre[0]
            else:
                e = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
                s = (dt.date.today() - dt.timedelta(days=33)).strftime("%Y-%m-%d")
            backfill(s, e, force="--force" in args)
        elif cmd == "--backtest":
            run_backtest(open_after="--open" in args)
        elif cmd == "--pro-daily":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            pro_daily(day, report=True, backtest="--no-backtest" not in args)
            log("四模型日报：%s" % (pro_report_of(day) or PRO_REPORT_DIR / "index.html"))
        elif cmd == "--pro-backtest":
            pro_run("backtest")
            p = pro_backtest_report()
            log("四模型回测页：%s" % p if p else "四模型回测完成")
            if p and "--open" in args:
                open_path(p)
        elif cmd == "--pro-report":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            open_path(pro_report_of(day) or (PRO_REPORT_DIR / "index.html"))
        elif cmd == "--pro-analysis":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            p = pro_analysis_report(day)
            if p:
                open_path(p)
            else:
                log("这一天还没有全解析文档：%s" % day)
        elif cmd == "--pro-score-rank":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            pro_run("rank", "--date", day)
            open_path(pro_score_rank_report(day) or (PRO_REPORT_DIR / "index.html"))
        elif cmd == "--pro-recommend":
            day = args[1] if len(args) > 1 and not args[1].startswith("-") else today_str()
            pro_run("recommend", "--date", day)
            if "--no-open" not in args:
                open_path(pro_recommend_report(day) or (PRO_REPORT_DIR / "index.html"))
        elif cmd == "--open":
            day = args[1] if len(args) > 1 else today_str()
            open_path(report_of(day) or (REPORT_DIR / "汇总.html"))
        elif cmd == "--gui":
            prepare_model()
            run_gui(args[1] if len(args) > 1 else None)
        elif cmd == "--gui-pro":
            prepare_model()
            run_gui(None, tab=4)
        elif cmd == "--gui-sim":
            prepare_model()
            run_gui(None, tab=6)
        elif cmd == "--gui-selftest":
            prepare_model()
            run_gui(None, selftest=True)
        elif cmd in ("-h", "--help"):
            print(__doc__)
        else:
            print(__doc__)
            return 2
    except Exception as e:
        log("失败：%s" % e)
        for line in traceback.format_exc().splitlines()[-4:]:
            log("  " + line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
