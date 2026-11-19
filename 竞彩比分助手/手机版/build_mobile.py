#!/usr/bin/env python3
"""把竞彩比分助手的数据整理成手机版 PWA 所需的数据与离线快照。

用法：
    python3 build_mobile.py            # 生成 site/ 与离线快照
    python3 build_mobile.py --quiet    # 只输出一行结果
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(os.environ.get("JCZQ_HOME", Path.home() / "竞彩比分助手")).expanduser()
DATA_DIR = BASE / "数据"
REPORT_DIR = BASE / "报告"
LEDGER = BASE / "模拟投注" / "台账.json"
AUTO_STATUS = BASE / "模拟投注" / "自动运行状态.json"
HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
TEMPLATE = HERE / "template.html"
OFFLINE_NAME = "竞彩比分助手_离线版.html"

PLAYS = ["胜平负", "比分", "总进球", "半全场"]
PLACE = {"主胜": 0, "平局": 1, "客胜": 2}
HF_CODES = ["3-3", "3-1", "3-0", "1-3", "1-1", "1-0", "0-3", "0-1", "0-0"]
DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
MAX_DAYS = 14
MAX_RADAR = 10


def read_json(path: Path, default=None):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def pick_prob(m: dict, key: str, default=None):
    v = m.get(key)
    if isinstance(v, (int, float)):
        return float(v)
    return default


def bf_odds(m: dict, score: str):
    if not score:
        return None
    odds = m.get("bf_odds") or {}
    return odds.get(str(score).replace("-", ":"))


def hf_level(p):
    """半全场把握档：单格 9 选 1，≥45% 才算高把握，≥38% 一般，其余观望。"""
    try:
        p = float(p or 0)
    except (TypeError, ValueError):
        return "low"
    return "high" if p >= 0.45 else ("mid" if p >= 0.38 else "low")


def trim_match(m: dict) -> dict:
    pick = m.get("pick")
    probs = m.get("probs") or []
    idx = PLACE.get(pick)
    pick_p = m.get("pick_p")
    if pick_p is None and idx is not None and idx < len(probs):
        pick_p = probs[idx]
    same = m.get("same_score") or m.get("pick_score")
    same_p = m.get("same_score_p")
    if same_p is None:
        same_p = (m.get("bf_probs") or {}).get(str(same).replace("-", ":"))
    top = []
    for item in (m.get("top") or [])[:6]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            top.append([item[0], item[1], bf_odds(m, item[0])])
    jq_probs = m.get("jq_probs") or []
    jq_odds = m.get("jq_odds") or {}
    hf = m.get("hf") or m.get("bq_probs") or []
    return {
        "no": m.get("no") or m.get("num") or "",
        "league": m.get("league") or "",
        "home": m.get("home") or "",
        "away": m.get("away") or "",
        "kickoff": m.get("kickoff") or "",
        "score": m.get("score") or "",
        "wdl_result": m.get("wdl_result") or "",
        "jq_result": m.get("jq_result"),
        "hf_result": m.get("hf_result") or "",
        "odds": m.get("odds") or [],
        "probs": probs,
        "pick": pick or "",
        "pick_p": pick_p,
        "same_score": same or "",
        "same_score_p": same_p,
        "same_score_odds": bf_odds(m, same),
        "top": top,
        "jq_probs": jq_probs,
        "jq_odds": jq_odds,
        "jq_pick": m.get("jq_pick"),
        "jq_pick_p": m.get("jq_pick_p"),
        "hf": hf,
        "hf_pick": m.get("hf_pick") or "",
        "hf_pick_p": m.get("hf_pick_p"),
        "hf_level": hf_level(m.get("hf_pick_p")),
        "bq_odds": m.get("bq_odds") or [],
        "hc": m.get("hc") or {},
        "return_rate": m.get("return_rate"),
        "lambda": m.get("lambda"),
        "model": m.get("model") or "",
    }


def load_days():
    days = []
    files = []
    for p in DATA_DIR.glob("*.json"):
        mt = DAY_RE.match(p.name)
        if mt:
            files.append((mt.group(1), p))
    files.sort(reverse=True)
    for day, p in files[:MAX_DAYS]:
        raw = read_json(p, {})
        if not isinstance(raw, dict):
            continue
        matches = raw.get("matches") or []
        if not matches:
            continue
        days.append({
            "day": day,
            "sealed": raw.get("sealed_at") or "",
            "matches": [trim_match(m) for m in matches],
        })
    return days


def compute_plays(days):
    agg = {p: {"play": p, "hit": 0, "n": 0, "exp_sum": 0.0} for p in PLAYS}
    for d in days:
        for m in d["matches"]:
            if not m.get("score"):
                continue
            wdl_hit = bool(m.get("wdl_result")) and m.get("pick") == m.get("wdl_result")
            bf_hit = bool(m.get("score")) and str(m.get("same_score", "")).replace("-", ":") == m.get("score")
            jq_hit = (m.get("jq_result") is not None and m.get("jq_pick") is not None
                      and str(m.get("jq_pick")) == str(m.get("jq_result")))
            hf_hit = bool(m.get("hf_result")) and m.get("hf_pick") == m.get("hf_result")
            rows = [
                ("胜平负", wdl_hit, m.get("pick_p")),
                ("比分", bf_hit, m.get("same_score_p")),
                ("总进球", jq_hit, m.get("jq_pick_p")),
                ("半全场", hf_hit, m.get("hf_pick_p")),
            ]
            for play, hit, p in rows:
                a = agg[play]
                a["n"] += 1
                a["hit"] += 1 if hit else 0
                if isinstance(p, (int, float)):
                    a["exp_sum"] += float(p)
    out = []
    for play in PLAYS:
        a = agg[play]
        n = a["n"]
        out.append({
            "play": play,
            "hit": a["hit"],
            "n": n,
            "rate": (a["hit"] / n) if n else None,
            "exp": (a["exp_sum"] / n) if n else None,
        })
    settled_days = sum(1 for d in days if any(m.get("score") for m in d["matches"]))
    return out, settled_days


def load_ledger():
    bets = read_json(LEDGER, [])
    if not isinstance(bets, list):
        bets = []
    trimmed = []
    for b in bets:
        legs = []
        for l in (b.get("legs") or []):
            legs.append({
                "no": l.get("no") or "",
                "match": l.get("match") or "",
                "play": l.get("play") or "",
                "pick": l.get("pick") or "",
                "p": l.get("p"),
                "odds": l.get("odds"),
                "score": l.get("score") or "",
                "hit": l.get("hit"),
            })
        trimmed.append({
            "id": b.get("id") or "",
            "day": b.get("day") or "",
            "created": b.get("created") or "",
            "bucket": b.get("bucket") or "",
            "play": b.get("play") or "",
            "type": b.get("type") or "",
            "legs": legs,
            "odds": b.get("odds") or 0,
            "p": b.get("p") or 0,
            "stake": b.get("stake") or 0,
            "ev": b.get("ev") or 0,
            "status": b.get("status") or "待结算",
            "payout": b.get("payout") or 0,
            "backfill": bool(b.get("backfill")),
        })
    order = {"H1": 1, "H2": 2, "N1": 3, "N2": 4}

    def key(b):
        short = str(b["id"]).rsplit("-", 1)[-1]
        return (b["day"], -order.get(short, 9))

    trimmed.sort(key=key, reverse=True)
    settled = [b for b in trimmed if b["status"] in ("中", "未中")]
    pending = [b for b in trimmed if b["status"] == "待结算"]
    stake = sum(float(b["stake"] or 0) for b in settled)
    payout = sum(float(b["payout"] or 0) for b in settled)
    hits = sum(1 for b in settled if b["status"] == "中")
    daily = {}
    for b in trimmed:
        d = daily.setdefault(b["day"], {"day": b["day"], "stake": 0.0, "payout": 0.0,
                                        "profit": 0.0, "n": 0, "pending": 0.0})
        if b["status"] in ("中", "未中"):
            d["stake"] += float(b["stake"] or 0)
            d["payout"] += float(b["payout"] or 0)
            d["profit"] = d["payout"] - d["stake"]
            d["n"] += 1
        elif b["status"] == "待结算":
            d["pending"] += float(b["stake"] or 0)
    daily_list = [d for d in sorted(daily.values(), key=lambda x: x["day"]) if d["n"]]
    stats = {
        "settled_n": len(settled),
        "hits": hits,
        "hit_rate": (hits / len(settled)) if settled else None,
        "stake": round(stake, 2),
        "payout": round(payout, 2),
        "profit": round(payout - stake, 2),
        "pending_n": len(pending),
        "pending_stake": round(sum(float(b["stake"] or 0) for b in pending), 2),
        "daily": daily_list,
    }
    return trimmed, stats


def load_radar():
    out = {}
    files = []
    for p in REPORT_DIR.glob("爆冷雷达_*.csv"):
        mt = re.search(r"爆冷雷达_(\d{4}-\d{2}-\d{2})\.csv$", p.name)
        if mt:
            files.append((mt.group(1), p))
    files.sort(reverse=True)
    for day, p in files[:MAX_RADAR]:
        rows = []
        try:
            with p.open("r", encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    def fnum(key):
                        try:
                            return float(str(r.get(key, "")).strip())
                        except Exception:
                            return None
                    if not (r.get("对阵") or "").strip():
                        continue
                    rows.append({
                        "day": day,
                        "no": (r.get("场次") or "").strip(),
                        "league": (r.get("联赛") or "").strip(),
                        "match": (r.get("对阵") or "").strip(),
                        "kickoff": (r.get("开球") or "").strip(),
                        "fav": (r.get("市场热门") or "").strip(),
                        "fav_p": fnum("热门市场概率"),
                        "fav_odds": fnum("热门赔率"),
                        "cold": (r.get("爆冷方向") or "").strip(),
                        "cold_p": fnum("冷门模型概率"),
                        "cold_odds": fnum("冷门赔率"),
                        "diff": fnum("模型-市场"),
                        "s1": (r.get("比分1") or "").strip(),
                        "s1p": fnum("比分1概率"),
                        "s2": (r.get("比分2") or "").strip(),
                        "s2p": fnum("比分2概率"),
                    })
        except Exception:
            continue
        out[day] = rows
    return out


def load_backtest():
    raw = read_json(DATA_DIR / "比分口径回测.json", {}) or {}
    rho = raw.get("rho")
    if isinstance(rho, dict):
        rho = rho.get("rho")
    dist = raw.get("dist")
    if isinstance(dist, dict):
        dist = dist.get("现行集成")
    return {
        "at": raw.get("at") or "",
        "n": raw.get("n") or 0,
        "top1_rate": raw.get("top1_rate"),
        "top3_rate": raw.get("top3_rate"),
        "top5_rate": raw.get("top5_rate"),
        "top10_rate": raw.get("top10_rate"),
        "rho": rho,
        "dist": dist,
    }


def build_icons():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return
    sizes = {"icon-1024.png": 1024, "icon-512.png": 512, "icon-192.png": 192, "icon-180.png": 180}
    big = Image.new("RGB", (1024, 1024), "#0B6B4F")
    px = big.load()
    for y in range(1024):
        for x in range(0, 1024, 2):
            t = (y / 1023) * 0.62 + (x / 1023) * 0.38
            r = int(9 + t * 8)
            g = int(88 + t * 64)
            b = int(68 + t * 40)
            px[x, y] = (r, g, b)
            if x + 1 < 1024:
                px[x + 1, y] = (r, g, b)
    d = ImageDraw.Draw(big, "RGBA")
    d.ellipse((690, -140, 1180, 350), fill=(255, 255, 255, 14))
    d.ellipse((-180, 700, 320, 1200), fill=(255, 255, 255, 10))
    font = None
    for path, idx in [("/System/Library/Fonts/Hiragino Sans GB.ttc", 1),
                      ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
                      ("/System/Library/Fonts/Supplemental/Songti.ttc", 1)]:
        try:
            font = ImageFont.truetype(path, 460, index=idx)
            break
        except Exception:
            continue
    if font:
        text = "竞"
        box = d.textbbox((0, 0), text, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        d.text(((1024 - w) / 2 - box[0], (1024 - h) / 2 - box[1] - 24), text, font=font, fill=(255, 255, 255, 255))
        try:
            small = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 86, index=0)
            sub = "比分助手"
            sb = d.textbbox((0, 0), sub, font=small)
            d.text(((1024 - (sb[2] - sb[0])) / 2 - sb[0], 742), sub, font=small, fill=(255, 255, 255, 205))
        except Exception:
            pass
    mask = Image.new("L", (1024, 1024), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 1023, 1023), radius=210, fill=255)
    big.putalpha(mask)
    for name, size in sizes.items():
        img = big.resize((size, size), Image.LANCZOS)
        img.save(SITE / name, optimize=True)


def write_atomic(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build(quiet: bool = False) -> dict:
    SITE.mkdir(parents=True, exist_ok=True)
    days = load_days()
    ledger, stats = load_ledger()
    auto = read_json(AUTO_STATUS, {}) or {}
    stats["plays"], window_days = compute_plays(days)
    stats["window_days"] = window_days
    radar = load_radar()
    backtest = load_backtest()
    today = days[0]["day"] if days else datetime.now().strftime("%Y-%m-%d")
    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "days": days,
        "ledger": ledger,
        "auto": auto,
        "stats": stats,
        "radar": radar,
        "backtest": backtest,
        "meta": {
            "matches": backtest.get("n"),
            "rho": backtest.get("rho"),
        },
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    write_atomic(SITE / "data.json", payload)

    tpl = TEMPLATE.read_text(encoding="utf-8")
    write_atomic(SITE / "index.html", tpl)

    manifest = {
        "name": "竞彩比分助手",
        "short_name": "比分助手",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#F4F5F7",
        "theme_color": "#F4F5F7",
        "lang": "zh-CN",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "icon-180.png", "sizes": "180x180", "type": "image/png"},
        ],
    }
    write_atomic(SITE / "manifest.webmanifest", json.dumps(manifest, ensure_ascii=False, indent=2))

    if not all((SITE / n).exists() for n in ("icon-180.png", "icon-192.png", "icon-512.png", "icon-1024.png")):
        build_icons()

    needle = "window.__SNAPSHOT__ = null;"
    if needle not in tpl:
        raise RuntimeError("模板缺少数据占位符")
    offline = tpl.replace(needle, "window.__SNAPSHOT__ = " + payload + ";")
    write_atomic(HERE / OFFLINE_NAME, offline)

    result = {
        "ok": True,
        "today": today,
        "days": len(days),
        "matches": sum(len(d["matches"]) for d in days),
        "bets": len(ledger),
        "pending": stats["pending_n"],
        "profit": stats["profit"],
        "radar_days": len(radar),
        "size_kb": round(len(payload.encode("utf-8")) / 1024, 1),
    }
    if not quiet:
        print(f"手机版数据已更新 {result['today']}：{result['days']} 个比赛日、"
              f"{result['matches']} 场、{result['bets']} 注、数据 {result['size_kb']} KB")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    try:
        build(args.quiet)
    except Exception as e:
        print(f"生成失败：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
