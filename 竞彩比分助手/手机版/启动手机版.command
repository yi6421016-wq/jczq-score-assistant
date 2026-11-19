#!/bin/bash
cd "$(dirname "$0")" || exit 1

ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
[ -z "$ip" ] && ip="127.0.0.1"
host=$(scutil --get LocalHostName 2>/dev/null)

if /usr/bin/curl -s -m 1 http://127.0.0.1:8787/api/ping >/dev/null 2>&1; then
  echo "竞彩比分助手手机版已在运行"
  echo ""
  echo "手机访问   http://$ip:8787"
  [ -n "$host" ] && echo "稳定地址   http://$host.local:8787"
  echo ""
  open "http://127.0.0.1:8787"
  echo "已打开电脑预览；手机上用 Safari 打开地址后，分享 → 添加到主屏幕。"
  echo "按回车关闭本窗口（服务继续运行）。"
  read -r _
  exit 0
fi

exec /usr/bin/env python3 serve_mobile.py --open
