#!/bin/zsh
# 模拟投注跑完后重建手机版站点，供每日发布使用
set -u
HERE="/Users/sequoia/竞彩比分助手/模拟投注"
"/Users/sequoia/.local/bin/竞彩模拟结算" "$HERE/simbank.py" "$@"
rc=$?
"/Users/sequoia/竞彩比分助手/手机版/同步到站点.sh" >> "$HERE/同步日志.log" 2>&1 || true
exit $rc
