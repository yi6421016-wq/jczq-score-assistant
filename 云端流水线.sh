#!/bin/bash
# 云端流水线：GitHub Actions 里按任务跑，跑完重建手机版站点
# 用法：云端流水线.sh verify|recheck|daily|simbank|backtest
set -u
cd "$(dirname "$0")"
export JCZQ_HOME="$PWD/竞彩比分助手"
export SIMBANK_HOME="$JCZQ_HOME"
export PYTHONUNBUFFERED=1
# 运行器默认 UTC，比赛日/日报日期必须按北京时间算
export TZ=Asia/Shanghai
export LC_ALL=C.UTF-8
MODE="${1:---daily}"

APP="$JCZQ_HOME/模拟投注/jczq_app.py"

# 四模型版运行时配置（含本机 python 路径，不提交）
mkdir -p "$JCZQ_HOME/新版"
printf '{"python": "%s", "code": "%s"}\n' "$(command -v python)" "$JCZQ_HOME/新版/代码" \
  > "$JCZQ_HOME/新版/运行配置.json"

case "$MODE" in
  verify)
    python "$APP" --verify; rc=$?
    ;;
  recheck)
    python "$APP" --recheck; rc=$?
    ;;
  daily)
    python "$APP" --daily; rc=$?
    python "$APP" --pro-daily --no-backtest
    python "$APP" --pro-recommend --no-open
    ;;
  simbank)
    python "$JCZQ_HOME/模拟投注/simbank.py" daily; rc=$?
    ;;
  backtest)
    python "$APP" --backfill && python "$APP" --backtest; rc=$?
    python "$APP" --pro-backtest
    ;;
  *)
    echo "未知模式 $MODE"; exit 2
    ;;
esac
rc=${rc:-0}

# 重建手机版站点，并修掉静态站点没有 api/data 接口的问题
python "$JCZQ_HOME/手机版/build_mobile.py"
perl -pi -e 's{api/data}{data.json}g' "$JCZQ_HOME/手机版/site/index.html"

# 站点里附上安卓安装包，手机浏览器可直接下载（路径由 .gitignore 之外单独存放）
APK="$JCZQ_HOME/手机版/下载/竞彩比分助手.apk"
[ -f "$APK" ] && cp -f "$APK" "$JCZQ_HOME/手机版/site/jczq.apk"

echo "流水线结束，退出码 $rc"
exit $rc
