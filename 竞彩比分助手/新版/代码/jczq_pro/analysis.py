"""当日全解析：把当天每一场的四模型、市场、比分、总进球、半全场与冷门
汇总成一份可打印的 HTML。只做概率记录，不构成投注建议。
"""
import html
import json
from datetime import datetime
from string import Template

from .config import DATA_DIR, HF_TXT, HF_VALUES, PRO_DATA, PRO_REPORT

NAMES = ["主胜", "平局", "客胜"]
MODEL_NAMES = {"dc": "Dixon-Coles", "elo": "Elo", "xg": "xG", "ml": "机器学习",
               "ens": "集成"}
MODEL_ORDER = ["dc", "elo", "xg", "ml", "ens"]


def _e(x):
    return html.escape(str(x))


def _load(path):
    p = path
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pct(x, d=1):
    return f"{x * 100:.{d}f}%"


def _pp(x):
    return f"{x * 100:+.1f}pp"


def _money(x):
    return f"{x:,.0f}"


def _hf_name(raw):
    try:
        a, b = raw.split("-")
        return HF_TXT.get(a, "?") + HF_TXT.get(b, "?")
    except ValueError:
        return raw


def _hf_rows(legacy):
    hf = (legacy or {}).get("hf") or []
    odds = (legacy or {}).get("bq_odds") or []
    rows = []
    for i, (raw, p) in enumerate(zip(HF_VALUES, hf)):
        o = odds[i] if i < len(odds) else None
        rows.append({"raw": raw, "name": _hf_name(raw), "p": float(p), "odds": o})
    rows.sort(key=lambda r: -r["p"])
    return rows


def _goals_dist(matrix):
    dist = [0.0] * 8
    for key, p in (matrix or {}).items():
        try:
            h, a = (int(x) for x in key.split("-"))
        except ValueError:
            continue
        dist[min(h + a, 7)] += float(p)
    total = sum(dist) or 1.0
    return [x / total for x in dist]


def collect(day):
    snap = _load(PRO_DATA / f"预测_{day}.json")
    if not snap or not snap.get("result"):
        raise RuntimeError(f"{day} 还没有四模型快照，先生成四模型日报")
    result = snap["result"]
    legacy_all = _load(DATA_DIR / f"{day}.json") or {}
    legacy = {m.get("no"): m for m in (legacy_all.get("matches") or [])}
    upset = _load(PRO_DATA / f"爆冷比分_{day}.json") or {}
    parlay = _load(PRO_DATA / f"爆冷四串五_{day}.json") or {}
    up_picks = {c["no"]: c for c in (upset.get("picks") or [])}
    up_alts = {c["no"]: c for c in (upset.get("alternates") or [])}
    parlay_nos = {l["no"] for l in (parlay.get("legs") or [])}

    matches = []
    for m in result.get("matches") or []:
        no = m.get("no", "")
        lg = legacy.get(no) or {}
        odds = m.get("odds") or lg.get("odds") or []
        market = m.get("market") or []
        if len(odds) != 3:
            continue
        if len(market) != 3:
            inv = [1.0 / float(x) for x in odds]
            s = sum(inv) or 1.0
            market = [x / s for x in inv]
        market = [float(x) for x in market]
        fav = max(range(3), key=lambda i: market[i])
        models = m.get("models") or {}
        ens = models.get("ens") or {}
        ens_probs = ens.get("probs") or []
        if len(ens_probs) != 3:
            continue
        pick_idx = max(range(3), key=lambda i: ens_probs[i])
        ens_pick = NAMES[pick_idx]
        ens_p = float(ens_probs[pick_idx])
        edge = ens_p - market[pick_idx]
        reverse = pick_idx != fav

        avail = [k for k in ("dc", "elo", "xg", "ml")
                 if (models.get(k) or {}).get("available")]
        agree = sum(1 for k in avail
                    if (models.get(k) or {}).get("pick") == ens_pick)

        bf = lg.get("bf_odds") or {}
        scores = []
        for s, p in (m.get("score_top") or {}).get("ens") or []:
            o = bf.get(s.replace("-", ":"))
            o = float(o) if o else None
            scores.append({"score": s, "p": float(p), "odds": o,
                           "ev": float(p) * o if o else None})
        mkt_scores = []
        for s, p in ((m.get("score_top") or {}).get("mkt_score") or [])[:3]:
            mkt_scores.append({"score": s, "p": float(p)})

        dist = _goals_dist(m.get("ens_matrix") or {})
        best = max(range(8), key=lambda i: dist[i])
        lo = max(0, best - 1)
        hi = min(7, best + 1)
        cov = sum(dist[lo:hi + 1])
        jq = lg.get("jq_odds") or {}

        hf_rows = _hf_rows(lg)
        hf = hf_rows[0] if hf_rows else None

        tags = []
        if reverse:
            tags.append("反向")
        if abs(edge) >= 0.05:
            tags.append("分歧≥5pp")
        elif abs(edge) >= 0.02:
            tags.append("小分歧")
        if no in up_picks:
            tags.append("冷门")
        if no in parlay_nos:
            tags.append("四串五")
        if avail and agree == len(avail) and len(avail) >= 3:
            tags.append("模型一致")

        matches.append({
            "no": no, "num": (no[-3:] if len(no) >= 3 else no),
            "league": m.get("league", ""), "home": m.get("home", ""),
            "away": m.get("away", ""), "kickoff": m.get("kickoff", ""),
            "odds": [float(x) for x in odds], "market": market,
            "market_fav": NAMES[fav], "models": models,
            "ens_pick": ens_pick, "ens_p": ens_p, "edge": edge,
            "reverse": reverse, "avail": avail, "agree": agree,
            "scores": scores, "mkt_scores": mkt_scores,
            "goals": {"dist": dist, "best": best, "lo": lo, "hi": hi,
                      "cov": cov, "jq": jq},
            "hf": hf, "hf_rows": hf_rows,
            "upset": up_picks.get(no), "upset_alt": up_alts.get(no),
            "parlay": no in parlay_nos,
            "tags": tags,
        })

    matches.sort(key=lambda x: (x["kickoff"], x["no"]))
    biggest = max(matches, key=lambda x: abs(x["edge"])) if matches else None
    summary = {
        "n": len(matches),
        "reverse": sum(1 for x in matches if x["reverse"]),
        "diverge": sum(1 for x in matches if abs(x["edge"]) >= 0.05),
        "upset": sum(1 for x in matches if x["upset"]),
        "parlay": sum(1 for x in matches if x["parlay"]),
        "biggest": biggest,
    }
    return {
        "date": day,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cutoff": result.get("cutoff", ""),
        "summary": summary,
        "matches": matches,
    }


CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f5f7fa;color:#0f172a;
  font-family:"PingFang SC","Hiragino Sans GB","Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:30px 26px 56px}
.eyebrow{font-size:12px;letter-spacing:.14em;color:#1d4ed8;font-weight:600}
h1{font-size:36px;line-height:1.2;margin:8px 0 6px;letter-spacing:-.02em}
.sub{color:#64748b;font-size:14px;line-height:1.7}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:22px 0 26px}
.kpi{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:15px 16px}
.kpi .k{font-size:12px;color:#64748b;margin-bottom:6px}
.kpi .v{font-size:23px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi .n{font-size:11px;color:#94a3b8;margin-top:4px}
.kpi.blue .v{color:#1d4ed8}.kpi.amber .v{color:#b45309}
.kpi.green .v{color:#15803d}.kpi.orange .v{color:#c2410c}
h2{font-size:20px;margin:0 0 4px;letter-spacing:-.01em}
.hint{font-size:12px;color:#64748b;margin:0 0 12px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f8fafc;color:#64748b;font-weight:600;text-align:left;
  padding:9px 12px;border-bottom:1px solid #e2e8f0;white-space:nowrap;font-size:11.5px}
td{padding:10px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.match-name{font-weight:600}
.meta{display:block;font-size:11px;color:#94a3b8;margin-top:2px;font-weight:400}
.pos{color:#15803d}.neg{color:#b45309}.flat{color:#64748b}
.tagrow{display:flex;gap:6px;flex-wrap:wrap}
.tag{display:inline-block;border-radius:999px;padding:2px 8px;font-size:10.5px;
  font-weight:600;background:#f1f5f9;color:#475569;white-space:nowrap}
.tag.rev{background:#fef2f2;color:#b91c1c}
.tag.div{background:#fffbeb;color:#b45309}
.tag.cold{background:#fff7ed;color:#c2410c}
.tag.p45{background:#eff6ff;color:#1d4ed8}
.tag.same{background:#f0fdf4;color:#15803d}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 4px}
.nav a{font-size:12px;color:#1d4ed8;text-decoration:none;background:#fff;
  border:1px solid #dbe3ee;border-radius:999px;padding:4px 10px}
.match{background:#fff;border:1px solid #e2e8f0;border-radius:16px;
  margin-top:20px;overflow:hidden;break-inside:avoid}
.mhead{padding:16px 18px 13px;border-bottom:1px solid #eef2f7;background:#fbfcfe}
.mno{font-size:11.5px;color:#64748b;margin-bottom:3px}
.mhead h3{font-size:20px;margin:0 0 4px;letter-spacing:-.01em}
.mhead .oddsline{font-size:12px;color:#475569;margin-top:2px}
.mhead .tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:16px 18px 6px}
.panel{border:1px solid #eef2f7;border-radius:12px;padding:12px 13px;background:#fff}
.panel h4{font-size:13px;margin:0 0 8px;color:#334155}
.panel table{font-size:12px}
.panel th{font-size:10.5px;padding:6px 7px}
.panel td{padding:6px 7px}
.pbar{height:5px;background:#eef2f7;border-radius:3px;overflow:hidden;margin-top:4px}
.pbar i{display:block;height:100%;border-radius:3px;background:#1d4ed8}
.pbar.warm i{background:#c2410c}
.pbar.green i{background:#15803d}
.model-note{font-size:10.5px;color:#94a3b8;line-height:1.6;margin-top:7px}
.score{font-weight:700;color:#c2410c;font-variant-numeric:tabular-nums}
.goalrow{display:grid;grid-template-columns:34px 1fr 46px 44px;gap:8px;
  align-items:center;font-size:11.5px;margin-bottom:5px}
.goalrow .lab{color:#64748b;font-variant-numeric:tabular-nums}
.goalrow .val{text-align:right;color:#334155;font-variant-numeric:tabular-nums}
.goalrow .od{text-align:right;color:#94a3b8;font-variant-numeric:tabular-nums}
.goalrow.best .lab{color:#c2410c;font-weight:700}
.hfbox{display:flex;align-items:baseline;gap:10px;margin:2px 0 8px}
.hfbox .big{font-size:25px;font-weight:800;color:#0f766e}
.hfbox .p{font-size:13px;color:#475569}
.hfbox .o{font-size:12px;color:#94a3b8}
.coldbox{background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;
  padding:9px 11px;font-size:12px;line-height:1.7;color:#7c2d12;margin-top:8px}
.coldbox b{color:#c2410c}
.cold-none{font-size:11.5px;color:#94a3b8;margin-top:8px}
.read{margin:10px 18px 18px;background:#f8fafc;border-left:4px solid #1d4ed8;
  border-radius:8px;padding:11px 14px;font-size:12.5px;line-height:1.85;color:#334155}
.read b{color:#0f172a}
.footer{margin-top:30px;font-size:11px;color:#94a3b8;line-height:1.8;
  border-top:1px solid #e2e8f0;padding-top:14px}
.footer b{color:#64748b}
@media (max-width:900px){
  .kpis{grid-template-columns:repeat(2,1fr)}
  .panels{grid-template-columns:1fr}
  .wrap{padding:22px 14px 44px}
  h1{font-size:27px}
  .mhead h3{font-size:17px}
  .hide-s{display:none}
}
@media print{
  body{background:#fff}
  .wrap{max-width:none;padding:0}
  .match{break-inside:avoid;box-shadow:none}
  .nav{display:none}
}
"""


def _tag_class(t):
    return {"反向": "rev", "分歧≥5pp": "div", "小分歧": "div",
            "冷门": "cold", "四串五": "p45", "模型一致": "same"}.get(t, "")


def _tag_html(tags):
    return "".join(f'<span class="tag {_tag_class(t)}">{_e(t)}</span>' for t in tags)


def _verdict_edge(edge):
    if abs(edge) < 0.02:
        return "与市场基本一致"
    return "高于市场" if edge > 0 else "低于市场"


def _read(m):
    parts = []
    if m["reverse"]:
        parts.append(f"模型首选<b>{m['ens_pick']}</b>，市场热门<b>{m['market_fav']}</b>，方向相反")
    else:
        parts.append(f"模型与市场同选<b>{m['ens_pick']}</b>")
    parts.append(f"模型给 {_pct(m['ens_p'])}，市场 {_pct(m['market'][NAMES.index(m['ens_pick'])])}，"
                 f"{'高' if m['edge'] >= 0 else '低'} {abs(m['edge']) * 100:.1f} 个点")
    if m["avail"]:
        parts.append(f"四模型 {m['agree']}/{len(m['avail'])} 同向")
    if m["scores"]:
        s1 = m["scores"][0]
        seg = f"比分首选 <b>{s1['score']}</b> {_pct(s1['p'])}"
        if len(m["scores"]) > 1:
            s2 = m["scores"][1]
            seg += f"，次选 {s2['score']} {_pct(s2['p'])}（差 {abs(s1['p'] - s2['p']) * 100:.1f} 个点）"
        parts.append(seg)
    g = m["goals"]
    parts.append(f"总进球最可能 {g['best']} 球，{g['lo']}–{g['hi']} 球覆盖 {_pct(g['cov'])}")
    if m["hf"]:
        hf = m["hf"]
        o = f" @{hf['odds']:.2f}" if hf.get("odds") else ""
        parts.append(f"半全场偏 {hf['name']} {_pct(hf['p'])}{o}")
    if m["upset"]:
        c = m["upset"]
        parts.append(f"冷门候选 {c['score']} @{c['odds']:.2f}（模型 {_pct(c['score_p'])}，"
                     f"EV {c['ev']:.2f}）")
    elif m["upset_alt"]:
        c = m["upset_alt"]
        parts.append(f"备选冷门 {c['score']} @{c['odds']:.2f}（模型 {_pct(c['score_p'])}）")
    else:
        parts.append("无符合条件的高赔冷门")
    return "；".join(parts) + "。"


def _panel_wdl(m):
    rows = ""
    for i, name in enumerate(NAMES):
        q = m["market"][i]
        e = (m["models"].get("ens") or {}).get("probs") or [0, 0, 0]
        e = float(e[i])
        diff = e - q
        cls = "pos" if diff >= 0.005 else ("neg" if diff <= -0.005 else "flat")
        rows += (f'<tr><td>{name}</td><td class="num">{_pct(q)}</td>'
                 f'<td class="num">{_pct(e)}</td>'
                 f'<td class="num {cls}">{diff * 100:+.1f}</td>'
                 f'<td><div class="pbar"><i style="width:{min(100, e * 250):.0f}%"></i></div></td></tr>')
    return (f'<div class="panel"><h4>胜平负：市场 vs 集成</h4><table>'
            f'<thead><tr><th>结果</th><th class="num">市场</th><th class="num">集成</th>'
            f'<th class="num">差</th><th>集成概率</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<div class="model-note">市场＝1/赔率归一化去水；差＝集成−市场，单位百分点。</div></div>')


def _panel_models(m):
    rows = ""
    for key in MODEL_ORDER:
        o = (m["models"].get(key) or {})
        name = MODEL_NAMES[key]
        if key == "ens":
            probs = o.get("probs") or []
            pick = o.get("pick", "—")
            note = " · ".join(f"{k} {v:.0%}" for k, v in
                              (o.get("meta") or {}).get("weights", {}).items())
        elif o.get("available") and o.get("probs"):
            probs = o["probs"]
            pick = o.get("pick", "—")
            note = o.get("note", "")
        else:
            probs = []
            pick = "—"
            note = o.get("note", "弃权")
        if len(probs) == 3:
            cells = "".join(f'<td class="num">{_pct(float(p))}</td>' for p in probs)
        else:
            cells = '<td class="num" colspan="3" style="color:#94a3b8">—</td>'
        bold = ' style="font-weight:700;color:#1d4ed8"' if key == "ens" else ""
        rows += (f'<tr{bold}><td>{_e(name)}</td>{cells}'
                 f'<td>{_e(pick)}</td></tr>')
        if key != "ens" and not (o.get("available") and o.get("probs")):
            rows += (f'<tr><td colspan="5" style="padding:2px 7px 7px;font-size:10px;'
                     f'color:#94a3b8">{_e(note)}</td></tr>')
    ens_note = " · ".join(f"{k} {v:.0%}" for k, v in
                          (((m["models"].get("ens") or {}).get("meta") or {})
                           .get("weights") or {}).items())
    return (f'<div class="panel"><h4>四模型与集成</h4><table>'
            f'<thead><tr><th>模型</th><th class="num">主胜</th><th class="num">平局</th>'
            f'<th class="num">客胜</th><th>选择</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<div class="model-note">集成权重：{_e(ens_note) or "—"}；xG 样本不足时弃权并按可用模型归一。</div></div>')


def _panel_scores(m):
    rows = ""
    for s in m["scores"][:5]:
        o = f"{s['odds']:.2f}" if s.get("odds") else "—"
        ev = f"{s['ev']:.2f}" if s.get("ev") else "—"
        cls = "pos" if (s.get("ev") or 0) > 1 else "flat"
        rows += (f'<tr><td class="score">{s["score"]}</td>'
                 f'<td class="num">{_pct(s["p"])}</td>'
                 f'<td class="num">{o}</td>'
                 f'<td class="num {cls}">{ev}</td>'
                 f'<td><div class="pbar warm"><i style="width:{min(100, s["p"] * 400):.0f}%"></i></div></td></tr>')
    mkt = "、".join(f"{s['score']} {_pct(s['p'])}" for s in m["mkt_scores"]) or "—"
    return (f'<div class="panel"><h4>比分 Top 5（集成分布）</h4><table>'
            f'<thead><tr><th>比分</th><th class="num">概率</th><th class="num">赔率</th>'
            f'<th class="num">EV</th><th>概率条</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<div class="model-note">市场比分盘前三：{_e(mkt)}。EV＝模型概率×比分赔率。</div></div>')


def _panel_goals(m):
    g = m["goals"]
    top = max(g["dist"]) or 1.0
    rows = ""
    for k, p in enumerate(g["dist"]):
        lab = "7+" if k == 7 else str(k)
        o = g["jq"].get(str(k))
        o = f"{float(o):.2f}" if o else "—"
        cls = " best" if k == g["best"] else ""
        rows += (f'<div class="goalrow{cls}"><span class="lab">{lab} 球</span>'
                 f'<span><div class="pbar {"warm" if k == g["best"] else ""}">'
                 f'<i style="width:{p / top * 100:.0f}%"></i></div></span>'
                 f'<span class="val">{_pct(p)}</span>'
                 f'<span class="od">{o}</span></div>')
    return (f'<div class="panel"><h4>总进球分布</h4>{rows}'
            f'<div class="model-note">最优 {g["best"]} 球；推荐区间 {g["lo"]}–{g["hi"]} 球，'
            f'覆盖 {_pct(g["cov"])}；右列为该档总进球赔率。</div></div>')


def _panel_hf_cold(m):
    hf = m["hf"]
    if hf:
        o = f'<span class="o">@{hf["odds"]:.2f}</span>' if hf.get("odds") else ""
        box = (f'<div class="hfbox"><span class="big">{_e(hf["name"])}</span>'
               f'<span class="p">{_pct(hf["p"])}</span>{o}</div>')
        alt = "、".join(f'{r["name"]} {_pct(r["p"])}' for r in m["hf_rows"][1:3])
        box += f'<div class="model-note">二三位：{_e(alt) or "—"}</div>'
    else:
        box = '<div class="model-note">无半全场数据</div>'
    if m["upset"]:
        c = m["upset"]
        cold = (f'<div class="coldbox"><b>冷门候选 {c["score"]} @{c["odds"]:.2f}</b><br>'
                f'模型 {_pct(c["score_p"])} · EV {c["ev"]:.2f} · '
                f'{_e(c["direction"])}（市场热 {_e(c["fav"])} {_pct(c["fav_p"])}）</div>')
    elif m["upset_alt"]:
        c = m["upset_alt"]
        cold = (f'<div class="coldbox"><b>备选冷门 {c["score"]} @{c["odds"]:.2f}</b><br>'
                f'模型 {_pct(c["score_p"])} · EV {c["ev"]:.2f} · '
                f'{_e(c["direction"])}（市场热 {_e(c["fav"])}）</div>')
    else:
        cold = '<div class="cold-none">本场没有符合“高赔 + 冷门方向 + EV”的比分候选。</div>'
    if m["parlay"]:
        cold += '<div class="cold-none" style="color:#1d4ed8">本场是今日爆冷四串五的腿之一。</div>'
    return (f'<div class="panel"><h4>半全场与冷门</h4>{box}{cold}</div>')


def _match_html(m, i):
    odd = " / ".join(f"{x:.2f}" for x in m["odds"])
    mkt = " / ".join(_pct(x) for x in m["market"])
    tags = _tag_html(m["tags"])
    return f"""<article class="match" id="m{m['num']}">
  <div class="mhead">
    <div class="mno">{_e(m['no'])} · {_e(m['league'])} · {_e(m['kickoff'][5:16])}</div>
    <h3>{_e(m['home'])} <span style="color:#cbd5e1;font-weight:400">vs</span> {_e(m['away'])}</h3>
    <div class="oddsline">市场赔率 {_e(odd)} · 去水概率 {_e(mkt)}</div>
    <div class="tags">{tags}</div>
  </div>
  <div class="panels">
    {_panel_wdl(m)}
    {_panel_models(m)}
    {_panel_scores(m)}
    {_panel_goals(m)}
    {_panel_hf_cold(m)}
  </div>
  <div class="read"><b>解读：</b>{_read(m)}</div>
</article>"""


def _overview(data):
    rows = ""
    for m in data["matches"]:
        s1 = m["scores"][0] if m["scores"] else None
        hf = m["hf"]["name"] if m["hf"] else "—"
        cls = "pos" if m["edge"] >= 0 else "neg"
        rows += (f'<tr><td><a href="#m{m["num"]}" style="color:#1d4ed8;text-decoration:none">'
                 f'{_e(m["no"])}</a><span class="meta">{_e(m["kickoff"][5:16])}</span></td>'
                 f'<td class="match-name">{_e(m["home"])} vs {_e(m["away"])}'
                 f'<span class="meta">{_e(m["league"])}</span></td>'
                 f'<td>{_e(m["market_fav"])}<span class="meta">{_pct(max(m["market"]))}</span></td>'
                 f'<td><b>{_e(m["ens_pick"])}</b><span class="meta">{_pct(m["ens_p"])}</span></td>'
                 f'<td class="num {cls}">{m["edge"] * 100:+.1f}</td>'
                 f'<td>{s1["score"] if s1 else "—"}<span class="meta">'
                 f'{_pct(s1["p"]) if s1 else ""}</span></td>'
                 f'<td>{m["goals"]["best"]} 球<span class="meta">'
                 f'{m["goals"]["lo"]}–{m["goals"]["hi"]} 球</span></td>'
                 f'<td>{_e(hf)}</td>'
                 f'<td class="hide-s">{_tag_html(m["tags"]) or "—"}</td></tr>')
    return (f'<div class="card"><table><thead><tr>'
            f'<th>场次</th><th>比赛</th><th>市场热门</th><th>模型首选</th>'
            f'<th class="num">差</th><th>比分首选</th><th>总进球</th><th>半全场</th>'
            f'<th class="hide-s">标签</th></tr></thead><tbody>{rows}</tbody></table></div>')


def render(data):
    s = data["summary"]
    big = s["biggest"]
    kpis = (f'<div class="kpis">'
            f'<div class="kpi blue"><div class="k">当日场次</div><div class="v">{s["n"]}</div>'
            f'<div class="n">全部逐场分析</div></div>'
            f'<div class="kpi amber"><div class="k">反向场次</div><div class="v">{s["reverse"]}</div>'
            f'<div class="n">模型首选 ≠ 市场热门</div></div>'
            f'<div class="kpi amber"><div class="k">分歧 ≥5pp</div><div class="v">{s["diverge"]}</div>'
            f'<div class="n">模型选项目与市场差</div></div>'
            f'<div class="kpi orange"><div class="k">冷门候选</div><div class="v">{s["upset"]}</div>'
            f'<div class="n">高赔 + 冷门方向</div></div>'
            f'<div class="kpi green"><div class="k">四串五腿</div><div class="v">{s["parlay"]}</div>'
            f'<div class="n">今日爆冷组合占用</div></div></div>')
    if big:
        kpis += (f'<div class="hint">最大分歧：<b>{_e(big["no"])} {_e(big["home"])} vs '
                 f'{_e(big["away"])}</b>，模型{_e(big["ens_pick"])} '
                 f'{_pct(big["ens_p"])} 对市场 '
                 f'{_pct(big["market"][NAMES.index(big["ens_pick"])])}，'
                 f'差 {big["edge"] * 100:+.1f} 个点。</div>')
    nav = "".join(f'<a href="#m{m["num"]}">{_e(m["num"])} {_e(m["home"][:4])}</a>'
                  for m in data["matches"])
    blocks = "".join(_match_html(m, i + 1) for i, m in enumerate(data["matches"]))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>当日全解析 · {_e(data['date'])}</title><style>{CSS}</style></head>
<body><div class="wrap">
<div class="eyebrow">竞彩比分助手 · 逐场分析</div>
<h1>当日全解析</h1>
<div class="sub">{_e(data['date'])} · 共 {s['n']} 场 · 四模型 + 市场比分盘 · 封存截断 {_e(data.get('cutoff') or '—')}<br>
生成 {_e(data['generated_at'])} · 只做概率记录，不构成投注建议</div>
{kpis}
<h2 style="margin-top:26px">全部场次总览</h2>
<p class="hint">“差”＝集成对首选结果的概率 − 市场去水概率；正数表示模型比市场更看好，负数相反。</p>
{_overview(data)}
<div class="nav">{nav}</div>
<h2 style="margin-top:30px">逐场分析</h2>
<p class="hint">每场按开球时间排序；点击上方场次可跳转。</p>
{blocks}
<div class="footer">
<b>口径：</b>胜平负市场概率＝1/赔率归一化去水；集成＝Dixon-Coles / Elo / xG / 机器学习按回测权重加权，xG 样本不足时弃权并归一。
比分分布为 100 格矩阵，融合市场比分盘 {(_load(PRO_DATA / f"预测_{data['date']}.json") or {}).get('result', {}).get('market_score_share', 0.15):.0%}；
总进球＝矩阵按总进球聚合，区间为众数 ±1；半全场为旧版九档引擎；冷门＝方向非市场热门、赔率≥12、模型≥3.5%、EV≥0.6。<br>
<b>风险：</b>比分单格概率通常只有 3%–13%，模型概率不是保证；赔率为封存时点 500.com 未去水盘口，出票时会变。所有数字仅作模型记录与复盘使用。
</div>
</div></body></html>"""


def write_analysis(day):
    data = collect(day)
    PRO_REPORT.mkdir(parents=True, exist_ok=True)
    hp = PRO_REPORT / f"当日全解析_{day}.html"
    hp.write_text(render(data), encoding="utf-8")
    return hp
