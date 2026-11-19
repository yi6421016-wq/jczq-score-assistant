#!/bin/zsh
# 重建手机版站点并同步到 Qoder Sites 发布目录
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
python3 build_mobile.py
DEST="/Users/sequoia/Documents/Qoder/2026-11-18/32ab82b6/竞彩比分助手-手机版/dist"
mkdir -p "$DEST"
rsync -a --delete "$HERE/site/" "$DEST/"
# 静态站点没有 api/data 接口，改为直接读取同目录的 data.json
perl -pi -e 's{api/data}{data.json}g' "$DEST/index.html"
# 同步安卓安装包，方便手机浏览器直接下载安装（保留下载地址固定）
APK="/Users/sequoia/Documents/Qoder/2026-11-18/32ab82b6/竞彩比分助手-手机版/android/dist/竞彩比分助手.apk"
[ -f "$APK" ] && cp -f "$APK" "$DEST/jczq.apk"
echo "站点已同步：$DEST"
