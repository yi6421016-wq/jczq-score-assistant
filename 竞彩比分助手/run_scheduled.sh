#!/bin/zsh
# 定时任务兜底：先等网络可用再执行，避免刚唤醒/刚开机时 DNS 未就绪直接失败。
# 用法：run_scheduled.sh --verify|--recheck|--daily
# 模拟投注（兑奖+出票）由独立任务 com.sequoia.jczq.simbank 负责，不在这里调用。
set -u
APP="/Users/sequoia/Applications/竞彩比分助手.app/Contents/MacOS/竞彩比分助手"
ROOT="/Users/sequoia/竞彩比分助手"
MODE="${1:---verify}"

log() { print -r -- "[$(date '+%F %T')] [兜底] $*"; }

mins=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))

if [[ "${JCZQ_FORCE:-0}" != "1" ]]; then
  case "$MODE" in
    --verify)
      (( mins < 540 || mins > 720 )) && { log "不在核对时段（09:00—12:00），跳过"; exit 0; }
      ;;
    --recheck)
      (( mins < 920 || mins > 1080 )) && { log "不在复核兜底时段（15:20—18:00），跳过"; exit 0; }
      [[ -f "$ROOT/数据/复核/$(date +%F).json" ]] && { log "今日已有复核快照，跳过"; exit 0; }
      ;;
    --daily)
      (( mins < 680 || mins > 1200 )) && { log "不在日报时段（11:20—20:00），跳过"; exit 0; }
      ;;
    *) log "未知模式 $MODE"; exit 2 ;;
  esac
fi

LOCK="/tmp/jczq_sched_${MODE#--}.lock"
mkdir "$LOCK" 2>/dev/null || { log "已有同类任务在跑，跳过"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

for i in {1..20}; do
  if /usr/bin/curl -sS -m 8 -o /dev/null "https://trade.500.com/jczq/" 2>/dev/null; then
    log "网络就绪（第 $i 次探测）"
    break
  fi
  log "网络未就绪，30 秒后重试（$i/20）"
  sleep 30
  (( i == 20 )) && { log "网络持续不可用，放弃本次"; exit 1; }
done

case "$MODE" in
  --verify) "$APP" --verify; rc=$? ;;
  --recheck) "$APP" --recheck; rc=$? ;;
  --daily)
    "$APP" --daily; rc=$?
    "$APP" --pro-daily --no-backtest
    "$APP" --pro-recommend --no-open
    "/Users/sequoia/竞彩比分助手/手机版/同步到站点.sh" >> "/Users/sequoia/竞彩比分助手/手机版/同步日志.log" 2>&1 || true
    ;;
esac
rc=${rc:-0}
log "执行完成，退出码 $rc"
exit $rc
