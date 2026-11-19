"""今日推荐：挑最稳的几场，每个玩法只给一个答案。

胜平负/比分/总进球 取四模型集成（含市场比分盘融合），
半全场沿用旧版 Dixon-Coles 的九档分布（新版未建半场模型）。
"""
import html
import json
from datetime import datetime

from .config import DATA_DIR, HF_TXT, HF_VALUES, PRO_DATA, PRO_REPORT

TOP_N = 5
X2_STRONG, X2_MID = 0.60, 0.45
GOALS_STRONG, GOALS_MID = 0.55, 0.45
HF_STRONG, HF_MID = 0.55, 0.35


def _e(x):
    return html.escape(str(x))


def _pct(x):
    return f"{x:.1%}"


def _load_pro(day):
    p = PRO_DATA / f"预测_{day}.json"
    if not p.exists():
        raise RuntimeError(f"{day} 还没有四模型快照，先生成四模型日报")
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("result") or {}


def _load_legacy(day):
    p = DATA_DIR / f"{day}.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {m.get("no"): m for m in (d.get("matches") or []) if m.get("no")}


def _goals(matrix):
    dist = {}
    for k, v in (matrix or {}).items():
        try:
            h, a = (int(x) for x in k.split("-"))
        except ValueError:
            continue
        dist[h + a] = dist.get(h + a, 0.0) + v
    if not dist:
        return None
    best = max(dist, key=lambda g: dist[g])
    lo, hi = max(0, best - 1), best + 1
    cov = sum(v for g, v in dist.items() if lo <= g <= hi)
    return {"best": best, "lo": lo, "hi": hi, "cov": cov, "p": dist[best]}


def _hf(legacy_match):
    hf = legacy_match.get("hf") or []
    if len(hf) != len(HF_VALUES):
        return None
    i = max(range(len(hf)), key=lambda k: hf[k])
    half, full = HF_VALUES[i].split("-")
    return {"pick": HF_TXT[half] + HF_TXT[full], "p": float(hf[i]), "raw": HF_VALUES[i]}


def _odd(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _odds(lg, probs_market):
    """封存时的市场赔率：胜平负 / 比分 / 总进球 / 半全场。"""
    x2 = lg.get("odds") or []
    bf = lg.get("bf_odds") or {}
    jq = lg.get("jq_odds") or {}
    bq = lg.get("bq_odds") or []
    return {"x2": [_odd(v) for v in x2] if len(x2) == 3 else [None] * 3,
            "bf": bf, "jq": jq, "bq": bq}


def _score_odds(bf, score):
    if not score:
        return None
    return _odd(bf.get(score.replace("-", ":")))


def _goals_odds(jq, lo, hi):
    out = []
    for g in range(lo, hi + 1):
        out.append((g, _odd(jq.get(str(g)))))
    return out


def build(day, top_n=TOP_N):
    result = _load_pro(day)
    legacy = _load_legacy(day)
    rows = []
    for m in result.get("matches", []):
        ens = (m.get("models") or {}).get("ens") or {}
        probs = ens.get("probs")
        tops = (m.get("score_top") or {}).get("ens") or []
        if not ens.get("available") or not probs or not tops:
            continue
        i = max(range(3), key=lambda k: probs[k])
        lg = legacy.get(m.get("no"), {})
        od = _odds(lg, m.get("market"))
        goals = _goals(m.get("ens_matrix"))
        hf = _hf(lg)
        hf_odds = None
        if hf and len(od["bq"]) == len(HF_VALUES):
            hf_odds = _odd(od["bq"][HF_VALUES.index(hf["raw"])])
        rows.append({
            "no": m.get("no", ""), "league": m.get("league", ""),
            "home": m.get("home", ""), "away": m.get("away", ""),
            "kickoff": m.get("kickoff", ""),
            "x2": ["主胜", "平局", "客胜"][i], "x2_p": float(probs[i]),
            "x2_odds": od["x2"][i],
            "score": tops[0][0], "score_p": float(tops[0][1]),
            "score_odds": _score_odds(od["bf"], tops[0][0]),
            "score2": tops[1][0] if len(tops) > 1 else "",
            "score2_p": float(tops[1][1]) if len(tops) > 1 else 0.0,
            "score2_odds": _score_odds(od["bf"], tops[1][0] if len(tops) > 1 else ""),
            "goals": goals,
            "goals_odds": (_goals_odds(od["jq"], goals["lo"], goals["hi"])
                           if goals else []),
            "hf": hf, "hf_odds": hf_odds,
            "fid": lg.get("fid", ""),
        })
    rows.sort(key=lambda r: -r["x2_p"])
    picked = rows[:top_n]
    stake = picked[0] if picked else None
    return {
        "date": day,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rule": {
            "top_n": top_n,
            "rank": "按胜平负首选概率从高到低取前 %d 场" % top_n,
            "x2": "四模型集成概率最高项",
            "score": "集成矩阵（含市场比分盘 15%）最高格",
            "goals": "比分矩阵换算的总进球，取最可能档 ±1 的区间",
            "hf": "旧版九档半全场概率最高项（新版未建半场模型）",
        },
        "top": stake,
        "rows": picked,
        "universe": len(rows),
    }


def _tag(p, strong, mid):
    p = round(p, 3)
    if p >= strong:
        return ("稳", "s-good")
    if p >= mid:
        return ("中", "s-mid")
    return ("低", "s-low")


def _odd_txt(v):
    return f'<i class="od">@ {v:.2f}</i>' if v else ""


def _card(r, i):
    g = r.get("goals") or {}
    hf = r.get("hf") or {}
    x2_lv, x2_cls = _tag(r["x2_p"], X2_STRONG, X2_MID)
    gl_lv, gl_cls = _tag(g.get("cov", 0), GOALS_STRONG, GOALS_MID) if g else ("—", "s-low")
    hf_lv, hf_cls = _tag(hf.get("p", 0), HF_STRONG, HF_MID) if hf else ("—", "s-low")
    goals_txt = f"{g['lo']}–{g['hi']} 球" if g else "—"
    goals_p = f"区间 {_pct(g['cov'])}" if g else "无数据"
    g_odds = [v for _, v in (r.get("goals_odds") or []) if v]
    goals_od = (f'<span class="od small">赔率 {" / ".join(f"{v:.2f}" for v in g_odds)}</span>'
                if g_odds else "")
    hf_txt = hf.get("pick", "—") if hf else "—"
    hf_p = _pct(hf["p"]) if hf else "无数据"
    score2_txt = (f'{_e(r["score2"])} {_pct(r["score2_p"])} {_odd_txt(r.get("score2_odds"))}'
                  if r["score2"] else "")
    score2 = f'<span class="alt">备选 {score2_txt}</span>' if r["score2"] else ""
    return f"""<section class="m">
  <div class="mh"><span class="rk">{i}</span>
    <div class="mt"><b>{_e(r['home'])} <span>vs</span> {_e(r['away'])}</b>
      <em>{_e(r['no'])} · {_e(r['league'])} · {_e(r['kickoff'])}</em></div>
  </div>
  <div class="plays">
    <div class="play {x2_cls}"><span class="lb">胜平负</span>
      <b class="ans">{_e(r['x2'])}</b>
      <span class="p">{_pct(r['x2_p'])} {_odd_txt(r.get('x2_odds'))}</span>
      <span class="lv">{x2_lv}</span></div>
    <div class="play s-low"><span class="lb">比分</span>
      <b class="ans">{_e(r['score'])}</b>
      <span class="p">{_pct(r['score_p'])} {_odd_txt(r.get('score_odds'))}</span>
      {score2}<span class="lv">参考</span></div>
    <div class="play {gl_cls}"><span class="lb">总进球</span>
      <b class="ans">{goals_txt}</b><span class="p">{goals_p}</span>
      {goals_od}<span class="lv">{gl_lv}</span></div>
    <div class="play {hf_cls}"><span class="lb">半全场</span>
      <b class="ans">{_e(hf_txt)}</b>
      <span class="p">{hf_p} {_odd_txt(r.get('hf_odds'))}</span>
      <span class="lv">{hf_lv}</span></div>
  </div>
</section>"""


def _upset_section(upsets):
    picks = (upsets or {}).get("picks") or []
    if not picks:
        return ""
    cards = ""
    for i, c in enumerate(picks):
        edge = c["edge"] * 100
        cards += f"""<div class="upc">
  <div class="uph"><b>{_e(c['home'])} vs {_e(c['away'])}</b><span>{_e(c['no'])}</span></div>
  <div class="upb"><span class="upsc">{_e(c['score'])}</span>
    <span class="upod">@ {c['odds']:.2f}</span>
    <span class="upp">{_pct(c['score_p'])}</span>
    <span class="upev">EV {c['ev']:.2f}</span></div>
  <div class="upm">{_e(c['direction'])}冷（热门{_e(c['fav'])}）· 模型{'高于' if edge >= 0 else '低于'}市场 {abs(edge):.1f} 个点</div>
</div>"""
    return f"""<h2 class="sec">今日爆冷比分</h2>
<p class="sechint">高赔冷门 · 最多 3 注 · 单格命中率 3%–9%，只做记录</p>
<div class="ups">{cards}</div>"""


def render(day, data, upsets=None):
    rows = data["rows"]
    top = data["top"]
    hero = ""
    if top:
        hero = (f'<div class="hero"><div class="hl">今日最稳一注</div>'
                f'<div class="hv">{_e(top["home"])} vs {_e(top["away"])}</div>'
                f'<div class="hp"><b>{_e(top["x2"])}</b>'
                f'<span>{_pct(top["x2_p"])}</span>'
                f'{_odd_txt(top.get("x2_odds"))}</div>'
                f'<div class="hm">{_e(top["no"])} · {_e(top["kickoff"])}</div></div>')
    cards = "".join(_card(r, i + 1) for i, r in enumerate(rows))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>今日推荐 · {_e(day)}</title><style>
*{{box-sizing:border-box}}
body{{margin:0;background:#f4f6f9;color:#1e293b;
  font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:26px 20px 40px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px}}
h1{{margin:0;font-size:26px;letter-spacing:1px;color:#16283f}}
.sub{{margin-top:6px;font-size:13px;color:#64748b}}
.stamp{{text-align:right;font-size:12px;color:#94a3b8;line-height:1.7}}
.stamp b{{color:#334155;font-size:14px}}
.hero{{background:linear-gradient(120deg,#16283f,#2f5d9a);color:#fff;border-radius:14px;
  padding:20px 24px;margin-bottom:18px;box-shadow:0 8px 20px rgba(22,40,63,.18)}}
.hero .hl{{font-size:13px;letter-spacing:2px;opacity:.75}}
.hero .hv{{font-size:24px;font-weight:700;margin:6px 0 2px}}
.hero .hp{{display:flex;align-items:baseline;gap:12px}}
.hero .hp b{{font-size:34px}}
.hero .hp span{{font-size:20px;opacity:.9}}
.hero .hp .od{{color:#9be7b6;font-size:18px;margin-left:10px}}
.hero .hm{{font-size:12px;opacity:.7;margin-top:4px}}
.m{{background:#fff;border:1px solid #e5eaf1;border-radius:14px;padding:14px 16px;
  margin-bottom:12px;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
.mh{{display:flex;align-items:center;gap:10px;margin-bottom:10px}}
.rk{{width:24px;height:24px;border-radius:50%;background:#eef3fa;color:#2f5d9a;
  font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center}}
.mt b{{font-size:17px}}
.mt b span{{color:#cbd5e1;font-weight:400;margin:0 4px}}
.mt em{{display:block;font-style:normal;font-size:12px;color:#94a3b8;margin-top:2px}}
.plays{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
.play{{border-radius:10px;padding:10px 12px;background:#f8fafc;border:1px solid #eef2f7;
  position:relative;min-height:86px}}
.play .lb{{display:block;font-size:12px;color:#64748b;letter-spacing:1px}}
.play .ans{{display:block;font-size:22px;margin:4px 0 2px;line-height:1.2}}
.play .p{{font-size:13px;color:#475569;font-variant-numeric:tabular-nums}}
.play .od{{font-style:normal;color:#0f766e;font-weight:700;margin-left:4px}}
.play .od.small{{display:block;font-size:11.5px;font-weight:600;margin:2px 0 0}}
.play .alt{{display:block;font-size:11.5px;color:#94a3b8;margin-top:2px}}
.play .lv{{position:absolute;top:8px;right:10px;font-size:11px;padding:1px 7px;
  border-radius:999px;border:1px solid}}
.s-good{{background:#f0fdf4;border-color:#bbf7d0}}
.s-good .ans{{color:#15803d}}
.s-good .lv{{color:#15803d;border-color:#bbf7d0}}
.s-mid{{background:#fffbeb;border-color:#fde68a}}
.s-mid .ans{{color:#b45309}}
.s-mid .lv{{color:#b45309;border-color:#fde68a}}
.s-low .ans{{color:#334155}}
.s-low .lv{{color:#94a3b8;border-color:#e2e8f0}}
.note{{margin-top:16px;font-size:12.5px;color:#64748b;line-height:1.8;
  background:#fff;border:1px dashed #dbe3ee;border-radius:10px;padding:12px 14px}}
.note b{{color:#334155}}
.sec{{margin:22px 0 4px;font-size:18px;color:#16283f}}
.sechint{{margin:0 0 10px;font-size:12px;color:#94a3b8}}
.ups{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.upc{{background:#fff;border:1px solid #f0d4d4;border-left:4px solid #b91c1c;
  border-radius:12px;padding:12px 14px;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
.uph{{display:flex;justify-content:space-between;font-size:12.5px;color:#475569}}
.uph b{{color:#16283f;font-size:13px}}
.upb{{display:flex;align-items:baseline;gap:8px;margin:6px 0 4px;flex-wrap:wrap}}
.upsc{{font-size:28px;font-weight:800;color:#b91c1c;letter-spacing:1px}}
.upod{{font-size:15px;font-weight:700;color:#0f766e}}
.upp{{font-size:12.5px;color:#475569}}
.upev{{font-size:12px;color:#b45309}}
.upm{{font-size:11px;color:#94a3b8;line-height:1.5}}
footer{{margin-top:18px;text-align:center;font-size:11.5px;color:#a8b3c2}}
@media (max-width:820px){{.plays{{grid-template-columns:repeat(2,1fr)}}.ups{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header><div><h1>今日推荐</h1>
<div class="sub">最稳 {len(rows)} 场 · 每个玩法只给一个答案</div></div>
<div class="stamp">日期 <b>{_e(day)}</b><br>生成 {_e(data['generated_at'])}</div></header>
{hero}
{cards}
{_upset_section(upsets)}
<div class="note">
<b>怎么排的：</b>{_e(data['rule']['rank'])}。
胜平负＝四模型集成最高项；比分＝集成矩阵最高格（含市场比分盘 15%）；总进球＝矩阵换算后最可能档 ±1 的区间；半全场＝旧版九档最高项。<br>
<b>怎么看：</b>绿＝把握较高，黄＝中等，灰＝偏低。<b>比分单格通常只有 9%–13%</b>，只作参考，不要和胜平负的概率直接比。<br>
<b>赔率：</b>绿色 @ 数字为封存时的 500.com 竞彩市场赔率（未去水），总进球因为推的是区间，列出区间内各档的赔率。<br>
所有数字为模型计算与历史校准记录，不构成投注建议。
</div>
<footer>由 jczq_pro 生成 · 数据源 500.com 竞彩 · {_e(day)}</footer>
</div></body></html>"""


def write_recommend(day, top_n=TOP_N):
    from .upset import write_upset, write_parlay45
    data = build(day, top_n)
    up_html, up_json = write_upset(day)
    par_html, par_json = write_parlay45(day)
    upsets = json.loads(up_json.read_text(encoding="utf-8"))
    PRO_REPORT.mkdir(parents=True, exist_ok=True)
    PRO_DATA.mkdir(parents=True, exist_ok=True)
    hp = PRO_REPORT / f"今日推荐_{day}.html"
    jp = PRO_DATA / f"推荐_{day}.json"
    hp.write_text(render(day, data, upsets), encoding="utf-8")
    jp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return hp, jp, up_html, up_json, par_html, par_json
