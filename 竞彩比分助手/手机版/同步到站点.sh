#!/bin/zsh
# 重建手机版站点并同步到发布目录
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
python3 build_mobile.py
# 发布目录刻意放在 ~/Documents 之外：launchd 没有“文稿文件夹”授权，写进去会 rsync 报
# Operation not permitted，站点就停在旧版本。可用 JCZQ_SITE_DEST 指到别处。
DEST="${JCZQ_SITE_DEST:-$HERE/发布/dist}"
mkdir -p "$DEST"
rsync -a --delete "$HERE/site/" "$DEST/"
# 静态站点没有 api/data 接口，改为直接读取同目录的 data.json（只改发布副本，本机 8787 仍走接口）
perl -pi -e 's{api/data}{data.json}g' "$DEST/index.html"
# 同步安卓安装包，方便手机浏览器直接下载安装
APK="${JCZQ_SITE_APK:-$HERE/下载/竞彩比分助手.apk}"
if [ -f "$APK" ]; then
  cp -f "$APK" "$DEST/jczq.apk"
else
  echo "警告：找不到安装包 $APK，本次未更新 jczq.apk"
fi
echo "站点已同步：$DEST"
