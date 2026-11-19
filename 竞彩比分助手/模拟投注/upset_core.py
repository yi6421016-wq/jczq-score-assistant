#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爆冷雷达核心：筛出“市场热门可能不赢”的场次，生成 HTML + CSV。

供 jczq_app.py 每日流程调用，也可由 爆冷雷达.py 单独运行。
只做概率记录，不构成投注建议。
"""
import csv
import datetime as dt
import html as H
import json
import os
from pathlib import Path

BASE = Path(os.environ.get("JCZQ_HOME", str(Path.home() / "竞彩比分助手")))
DATA_DIR = BASE / "数据"
REPORT_DIR = BASE / "报告"
NAMES = ["主胜", "平局", "客胜"]
MIN_DOG_P = 0.26
MIN_DELTA = 0.005
MAX_CARDS = 8


def kind(score: str) -> int:
    """0 主胜 / 1 平局 / 2 客胜"""
    h, a = (int(x) for x in score.split("-"))
    return 0 if h > a else (1 if h == a else 2)


def load_day(day: str) -> dict:
    p = DATA_DIR / ("%s.json" % day)
    if not p.exists():
        raise RuntimeError("没有 %s 的数据：%s" % (day, p))
    return json.loads(p.read_text(encoding="utf-8"))


def analyze(day: str) -> tuple[dict, list[dict]]:
    data = load_day(day)
    rows = []
    for m in data.get("matches", []):
        odds = m.get("odds") or []
        probs = m.get("probs") or []
        if len(odds) < 3 or len(probs) < 3:
            continue
        inv = [1.0 / x for x in odds]
        total = sum(inv)
        q = [x / total for x in inv]            # 去水后的市场概率
        fav = max(range(3), key=lambda i: q[i])
        dogs = [i for i in range(3) if i != fav]
        # 冷门方向：非热门里“模型概率 - 市场概率”最大的一项
        dog = max(dogs, key=lambda i: (probs[i] - q[i], probs[i]))
        top = [(s, p) for s, p in (m.get("top") or []) if kind(s) == dog][:2]
        if not top:
            top = [(s, p) for s, p in (m.get("top") or [])][:2]
        rows.append({
            "no": m.get("no", ""),
            "league": m.get("league", ""),
            "home": m.get("home", ""),
            "away": m.get("away", ""),
            "kickoff": m.get("kickoff", ""),
            "fav": NAMES[fav],
            "fav_p": probs[fav],
            "fav_q": q[fav],
            "fav_odds": odds[fav],
            "dog": NAMES[dog],
            "dog_p": probs[dog],
            "dog_q": q[dog],
            "dog_odds": odds[dog],
            "delta": probs[dog] - q[dog],
            "scores": top,
        })
    rows.sort(key=lambda x: (x["delta"], x["dog_p"]), reverse=True)
    picks = [r for r in rows
             if r["delta"] >= MIN_DELTA and r["dog_p"] >= MIN_DOG_P][:MAX_CARDS]
    return data, picks


def level_of(r: dict) -> tuple[str, str]:
    if r["dog_p"] >= 0.33:
        return "high", "高"
    if r["dog_p"] >= 0.30:
        return "mid", "中"
    return "low", "低"


def card_html(r: dict) -> str:
    cls, label = level_of(r)
    scores = "".join(
        '<div class="score"><b>%s</b><span>%.1f%%</span></div>' % (H.escape(s), p * 100)
        for s, p in r["scores"]
    )
    delta = r["delta"] * 100
    delta_txt = ("模型高 %.1f 个点" % delta) if delta >= 0 else ("模型低 %.1f 个点" % abs(delta))
    return """
    <article class="card {cls}">
      <div class="card-top">
        <span class="no">{no}</span><span class="lg">{lg}</span>
        <span class="lv lv-{cls}">{label}</span>
        <span class="kick">{kick}</span>
      </div>
      <h3>{home} <em>vs</em> {away}</h3>
      <div class="pair">
        <div class="cell">
          <div class="lab">市场热门</div>
          <div class="val">{fav} <b>{favp:.0f}%</b></div>
          <div class="sub">赔率 {favo:.2f}</div>
        </div>
        <div class="arrow">→</div>
        <div class="cell dog">
          <div class="lab">爆冷方向</div>
          <div class="val">{dog} <b>{dogp:.0f}%</b></div>
          <div class="sub">赔率 {dogo:.2f} · {delta_txt}</div>
        </div>
      </div>
      <div class="scores">{scores}</div>
      <div class="bar"><i style="width:{w:.0f}%"></i></div>
    </article>""".format(
        cls=cls, label=label, no=H.escape(r["no"]), lg=H.escape(r["league"]),
        kick=H.escape((r["kickoff"] or "")[5:16]),
        home=H.escape(r["home"]), away=H.escape(r["away"]),
        fav=r["fav"], favp=r["fav_q"] * 100, favo=r["fav_odds"],
        dog=r["dog"], dogp=r["dog_p"] * 100, dogo=r["dog_odds"],
        delta_txt=delta_txt, scores=scores,
        w=min(100, r["dog_p"] * 100 / 0.45),
    )


def render(day: str, data: dict, picks: list[dict]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    total = len(data.get("matches", []))
    top_p = picks[0]["dog_p"] if picks else 0
    strong = [p for p in picks if p["fav_q"] >= 0.60 and p["dog_p"] >= 0.30]
    cards = "".join(card_html(r) for r in picks)
    empty = '<div class="empty">今天没有达到阈值的爆冷候选；说明市场定价与模型基本一致。</div>'
    note_strong = ("今天没有“强队大热但模型明显看衰”的场次。" if not strong
                   else "另有 %d 场强队大热但模型给出三成以上冷门概率。" % len(strong))
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s vs %s</td><td>%s %.0f%%</td>"
        "<td class='dog'>%s %.0f%%</td><td>%.2f</td><td>%s</td></tr>" % (
            H.escape(r["no"]), H.escape(r["league"]), H.escape(r["home"]),
            H.escape(r["away"]), r["fav"], r["fav_q"] * 100, r["dog"],
            r["dog_p"] * 100, r["dog_odds"],
            " · ".join("%s %.1f%%" % (s, p * 100) for s, p in r["scores"]))
        for r in picks
    )
    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>爆冷雷达 · %(day)s</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--ink:#16202f;--mute:#6b7890;--line:#e4e9f2;--bg:#f2f5fa;--card:#fff;
--red:#cf2b3f;--red-d:#9d1428;--amber:#d9851c;--navy:#1d3557;--green:#0f7a43}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);line-height:1.55;-webkit-font-smoothing:antialiased}
.hero{background:radial-gradient(720px 320px at 8%% -30%%,rgba(207,43,63,.55),transparent 62%%),
radial-gradient(680px 300px at 96%% 0%%,rgba(217,133,28,.35),transparent 60%%),
linear-gradient(135deg,#111722,#1c2433 55%%,#381018);color:#fff;padding:40px 22px 70px}
.wrap{max-width:1120px;margin:0 auto;padding:0 22px}
.badge{display:inline-block;font-size:11.5px;letter-spacing:.12em;padding:4px 11px;border-radius:999px;
background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.24);color:#ffd9de;margin-bottom:14px}
h1{font-size:clamp(25px,3.4vw,38px);letter-spacing:-.02em;font-weight:800}
h1 span{color:#ff6172}
.sub{color:#aab4c7;font-size:13px;margin-top:8px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;
max-width:1120px;margin:-40px auto 0;padding:0 22px;position:relative;z-index:2}
.kpi{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;
box-shadow:0 10px 26px rgba(16,24,40,.08)}
.kpi .l{font-size:11px;color:var(--mute)}
.kpi .v{font-size:24px;font-weight:800;margin-top:2px;letter-spacing:-.01em}
.kpi .s{font-size:10.5px;color:#98a2b3;margin-top:1px}
main{max-width:1120px;margin:0 auto;padding:26px 22px 50px}
.def{background:#eef4ff;border:1px solid #d7e4ff;color:#2f4a78;border-radius:12px;
padding:12px 15px;font-size:12.5px;margin-bottom:22px}
.def b{color:#1d3557}
h2{font-size:18px;margin:6px 0 3px;letter-spacing:-.01em}
.hint{font-size:12px;color:var(--mute);margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--navy);
border-radius:16px;padding:15px 17px;box-shadow:0 8px 22px rgba(16,24,40,.05)}
.card.high{border-left-color:var(--red)}
.card.mid{border-left-color:var(--amber)}
.card.low{border-left-color:#7c8aa5}
.card-top{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--mute);flex-wrap:wrap}
.no{font-weight:800;color:var(--ink)}
.lg{background:#f1f4f9;border-radius:6px;padding:1px 6px}
.lv{margin-left:auto;font-weight:700;border-radius:999px;padding:2px 8px;font-size:10px}
.lv-high{background:#fdecef;color:var(--red-d)}
.lv-mid{background:#fdf3e3;color:#9a5b0c}
.lv-low{background:#eef1f6;color:#5b6678}
.kick{font-variant-numeric:tabular-nums}
.card h3{font-size:16.5px;margin:7px 0 10px;letter-spacing:-.01em}
.card h3 em{font-style:normal;color:#b7c0d0;font-size:12px;padding:0 3px}
.pair{display:flex;align-items:center;gap:10px;background:#f8fafd;border-radius:11px;padding:10px 12px}
.cell{flex:1}
.lab{font-size:10px;color:var(--mute);letter-spacing:.04em}
.val{font-size:14px;font-weight:700;margin-top:1px}
.val b{font-size:18px;margin-left:2px}
.cell.dog .val{color:var(--red-d)}
.sub2{font-size:10.5px;color:#98a2b3;margin-top:1px}
.pair .sub{font-size:10.5px;color:#98a2b3;margin-top:1px}
.arrow{color:#c3cad8;font-weight:700}
.scores{display:flex;gap:8px;margin-top:10px}
.score{flex:1;display:flex;align-items:baseline;justify-content:space-between;
border:1px dashed #dbe2ee;border-radius:9px;padding:6px 10px;background:#fff}
.score b{font-size:16px;letter-spacing:.02em}
.score span{font-size:11px;color:var(--mute)}
.bar{height:5px;background:#eef2f8;border-radius:99px;overflow:hidden;margin-top:10px}
.bar i{display:block;height:100%%;background:linear-gradient(90deg,#e66272,#b01a2e);border-radius:99px}
.empty{background:#fff;border:1px dashed #cfd8e6;border-radius:14px;padding:28px;color:var(--mute);
font-size:13px;text-align:center}
.table{background:#fff;border:1px solid var(--line);border-radius:16px;overflow:auto;margin-top:8px}
table{width:100%%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-size:10.5px;color:#8a94a6;letter-spacing:.04em;padding:10px 14px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 14px;border-bottom:1px solid #f0f3f8;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.dog{color:var(--red-d);font-weight:700}
footer{margin-top:28px;border-top:1px solid var(--line);padding-top:14px;font-size:11px;color:#8a94a6}
footer p+p{margin-top:6px}
@media (max-width:720px){.grid{grid-template-columns:1fr}.pair{flex-direction:row}.kpis{padding:0 16px}}
</style></head><body>
<header class="hero"><div class="wrap">
  <div class="badge">概率记录 · 不是投注建议</div>
  <h1>爆冷雷达 <span>%(day)s</span></h1>
  <p class="sub">从当日 %(total)d 场里筛出 %(n)d 场“市场热门可能不赢”的比赛 · 按模型给冷门方向的概率排序 · 生成于 %(now)s</p>
</div></header>
<section class="kpis">
  <div class="kpi"><div class="l">当日场次</div><div class="v">%(total)d</div><div class="s">有胜平负赔率的场次</div></div>
  <div class="kpi"><div class="l">爆冷候选</div><div class="v">%(n)d</div><div class="s">模型高于市场 ≥ 0.5 点</div></div>
  <div class="kpi"><div class="l">最高冷门概率</div><div class="v">%(topp).1f%%</div><div class="s">单场单一方向</div></div>
  <div class="kpi"><div class="l">强队翻车信号</div><div class="v">%(ns)d</div><div class="s">热门 ≥ 60%% 且冷门 ≥ 30%%</div></div>
</section>
<main>
  <div class="def"><b>怎么看：</b>“市场热门”是把三家赔率去水后的概率，代表庄家定价；“爆冷方向”是模型认为最可能出现的非热门结果。
  两者差值很小说明市场与模型基本一致，差值为正才是模型相对看好冷门。<b>%(note)s</b></div>
  <h2>今日候选</h2>
  <p class="hint">卡片越红，冷门方向概率越高；比分是模型分布里该方向概率最高的两个。</p>
  <div class="grid">%(cards)s</div>
  <h2 style="margin-top:26px">明细</h2>
  <p class="hint">市场热门、冷门方向、赔率与比分候选一览。</p>
  <div class="table"><table>
    <thead><tr><th>场次</th><th>联赛</th><th>对阵</th><th>市场热门</th><th>爆冷方向</th><th>冷门赔率</th><th>比分候选</th></tr></thead>
    <tbody>%(rows)s</tbody>
  </table>%(empty)s</div>
  <footer>
    <p><b>口径：</b>模型为 Dixon-Coles 比分分布；市场概率由胜平负赔率去水得到；冷门方向取非市场热门中模型相对市场最看好的一项。</p>
    <p><b>提醒：</b>所有方向的模型期望仍为负（庄家抽水），这里只做概率排序与比分参考，不代表“值得投注”。样本只有 100 多场，波动很大。</p>
    <p>仅用于数据统计与程序演示，不构成任何购彩或投注建议。未成年人不得购彩。</p>
  </footer>
</main></body></html>""" % {
        "day": day, "total": total, "n": len(picks), "now": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "minp": MIN_DOG_P * 100, "topp": top_p * 100, "ns": len(strong),
        "note": note_strong, "cards": cards or empty, "rows": rows,
        "empty": "" if picks else empty,
    }
    out = REPORT_DIR / ("爆冷雷达_%s.html" % day)
    out.write_text(doc, encoding="utf-8")

    csv_path = REPORT_DIR / ("爆冷雷达_%s.csv" % day)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["比赛日", "场次", "联赛", "对阵", "开球", "市场热门", "热门市场概率",
                    "热门赔率", "爆冷方向", "冷门模型概率", "冷门赔率", "模型-市场",
                    "比分1", "比分1概率", "比分2", "比分2概率"])
        for r in picks:
            s1 = r["scores"][0] if len(r["scores"]) > 0 else ("", 0.0)
            s2 = r["scores"][1] if len(r["scores"]) > 1 else ("", 0.0)
            w.writerow([day, r["no"], r["league"], "%s vs %s" % (r["home"], r["away"]),
                        r["kickoff"], r["fav"], "%.4f" % r["fav_q"], r["fav_odds"],
                        r["dog"], "%.4f" % r["dog_p"], r["dog_odds"], "%.4f" % r["delta"],
                        s1[0], "%.4f" % s1[1], s2[0], "%.4f" % s2[1]])
    return out


def build(day: str) -> Path:
    data, picks = analyze(day)
    return render(day, data, picks)
