#!/usr/bin/env python3
"""竞彩比分助手 · 手机版服务

在电脑上运行后，iPhone 用 Safari 打开同一个 Wi-Fi 下的地址即可使用，
也可以「添加到主屏幕」当成 App。只读本机数据，不代下单。

用法：
    python3 serve_mobile.py              # 默认 0.0.0.0:8787
    python3 serve_mobile.py --open       # 同时在电脑浏览器打开
    python3 serve_mobile.py --port 8800
"""
from __future__ import annotations

import argparse
import functools
import json
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import build_mobile

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
BUILD_LOCK = threading.Lock()


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".webmanifest": "application/manifest+json",
        ".json": "application/json; charset=utf-8",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def end_headers(self):
        path = (self.path or "").split("?")[0]
        if path.endswith((".html", ".json", ".webmanifest")) or path in ("/", ""):
            self.send_header("Cache-Control", "no-store, max-age=0")
        elif path.endswith(".png"):
            self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()

    def do_GET(self):
        path = (self.path or "").split("?")[0]
        if path == "/api/ping":
            return self._json({"ok": True, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        if path == "/api/data":
            try:
                with BUILD_LOCK:
                    build_mobile.build(quiet=True)
                payload = (SITE / "data.json").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)
            return
        if path == "/api/offline":
            try:
                with BUILD_LOCK:
                    build_mobile.build(quiet=True)
                data = (SITE / "data.json").read_text(encoding="utf-8")
                tpl = (HERE / "template.html").read_text(encoding="utf-8")
                html = tpl.replace("window.__SNAPSHOT__ = null;", "window.__SNAPSHOT__ = " + data + ";")
                body = html.encode("utf-8")
                name = f"竞彩比分助手_离线版_{datetime.now().strftime('%Y%m%d')}.html"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("/api/"):
            return
        sys.stderr.write("  %s  %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    print("竞彩比分助手 · 手机版")
    try:
        with BUILD_LOCK:
            r = build_mobile.build(quiet=True)
        print(f"数据：{r['days']} 个比赛日 / {r['matches']} 场 / 台账 {r['bets']} 注")
    except Exception as e:
        print(f"数据生成失败（先继续用上次的）：{e}")

    server = None
    port = args.port
    for _ in range(6):
        try:
            server = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError:
            port += 1
    if server is None:
        print("端口被占用，启动失败。")
        return 1

    ip = lan_ip()
    print("")
    print(f"电脑预览   http://127.0.0.1:{port}")
    print(f"手机访问   http://{ip}:{port}")
    hostname = ""
    try:
        hostname = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        hostname = ""
    if hostname:
        print(f"稳定地址   http://{hostname}.local:{port}")
    print("")
    print("（IP 会随网络变化；长期使用建议用稳定地址添加到主屏幕）")
    print("")
    print("iPhone 与电脑连同一个 Wi-Fi → Safari 打开手机地址")
    print("→ 分享 → 添加到主屏幕，即可像 App 一样全屏使用。")
    print("离线快照   http://%s:%d/api/offline" % (ip, port))
    print("按 Ctrl+C 停止服务。")
    print("")
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
