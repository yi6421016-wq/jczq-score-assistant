#!/bin/zsh
# 云端为准：本机不出票、不兑奖，只把拉下来的台账重画成页面并同步发布目录
set -u
HERE="/Users/sequoia/竞彩比分助手/模拟投注"
export SIMBANK_READ_ONLY=1
"/Users/sequoia/.local/bin/竞彩模拟结算" "$HERE/simbank.py" daily "$@"
rc=$?
if ! "/Users/sequoia/竞彩比分助手/手机版/同步到站点.sh" >> "$HERE/同步日志.log" 2>&1; then
  rc=1
  print -r -- "[$(date '+%F %T')] 站点同步失败，手机版发布目录未更新" >> "$HERE/同步日志.log"
fi
exit $rc
