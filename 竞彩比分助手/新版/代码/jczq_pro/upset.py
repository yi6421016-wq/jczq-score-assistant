"""爆冷比分：从高赔比分里挑模型认为被市场低估的几注。

口径：方向必须与市场热门相反（平局也算冷），赔率 ≥ 12，模型概率 ≥ 3.5%，
EV = 模型概率 × 赔率 ≥ 0.60；按 EV 排序，同一比分与同一场各只取一注。
只做概率记录，不构成投注建议。
"""
import html
import itertools
import json
import math
from datetime import datetime
from string import Template

from .config import DATA_DIR, PRO_DATA, PRO_REPORT

MIN_ODDS = 12.0
MIN_P = 0.035
MIN_EV = 0.60
TOP_N = 3
MAX_ALT = 6
NAMES = ["主胜", "平局", "客胜"]


def _e(x):
    return html.escape(str(x))


def _pct(x):
    return f"{x:.1%}"


def _load(day):
    p = PRO_DATA / f"预测_{day}.json"
    if not p.exists():
        raise RuntimeError(f"{day} 还没有四模型快照，先生成四模型日报")
    pro = (json.loads(p.read_text(encoding="utf-8")).get("result") or {})
    lp = DATA_DIR / f"{day}.json"
    legacy = {}
    if lp.exists():
        try:
            legacy = {m.get("no"): m for m in
                      (json.loads(lp.read_text(encoding="utf-8")).get("matches") or [])}
        except Exception:
            legacy = {}
    return pro, legacy


def build(day, top_n=TOP_N):
    pro, legacy = _load(day)
    cands = []
    universe = 0
    for m in pro.get("matches", []):
        no = m.get("no", "")
        lg = legacy.get(no) or {}
        odds = lg.get("odds") or []
        matrix = m.get("ens_matrix") or {}
        bf = lg.get("bf_odds") or {}
        if len(odds) != 3 or not matrix:
            continue
        universe += 1
        inv = [1.0 / float(x) for x in odds]
        total = sum(inv)
        q = [x / total for x in inv]
        fav = max(range(3), key=lambda i: q[i])
        for key, p in matrix.items():
            try:
                h, a = (int(x) for x in key.split("-"))
            except ValueError:
                continue
            if h > 10 or a > 10:
                continue
            o = bf.get(f"{h}:{a}")
            if not o:
                continue
            kind = 0 if h > a else (1 if h == a else 2)
            if kind == fav:
                continue
            o = float(o)
            p = float(p)
            if o < MIN_ODDS or p < MIN_P:
                continue
            ev = p * o
            if ev < MIN_EV:
                continue
            cands.append({
                "no": no, "league": m.get("league", ""),
                "home": m.get("home", ""), "away": m.get("away", ""),
                "kickoff": m.get("kickoff", ""),
                "score": f"{h}-{a}", "score_p": p, "odds": o, "ev": ev,
                "direction": NAMES[kind], "fav": NAMES[fav], "fav_p": q[fav],
                "edge": p - 1.0 / o,
            })
    cands.sort(key=lambda c: (-c["ev"], -c["score_p"]))
    picks, used_scores, used_no = [], set(), set()
    for c in cands:
        if c["score"] in used_scores or c["no"] in used_no:
            continue
        picks.append(c)
        used_scores.add(c["score"])
        used_no.add(c["no"])
        if len(picks) >= top_n:
            break
    rest = [c for c in cands if c not in picks][:MAX_ALT]
    return {
        "date": day,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rule": {
            "min_odds": MIN_ODDS, "min_p": MIN_P, "min_ev": MIN_EV,
            "text": f"方向非市场热门 · 赔率≥{MIN_ODDS:g} · 概率≥{MIN_P:.1%} · "
                    f"EV≥{MIN_EV:.2f} · 同一比分/同一场各取 1 注 · 按 EV 排序",
        },
        "picks": picks,
        "alternates": rest,
        "candidates": len(cands),
        "universe": universe,
    }


PARLAY_STAKE = 2.0


def _short_no(no):
    return no[-3:] if len(no) >= 3 else no


def _money(v):
    return f"{v:,.0f}"


def _tiny(x):
    v = x * 100
    return f"{v:.5f}%" if v < 0.01 else f"{v:.4f}%"


def build_parlay45(day):
    base = build(day)
    legs = list((base.get("picks") or [])[:3])
    used_no = {c["no"] for c in legs}
    for c in base.get("alternates") or []:
        if len(legs) >= 4:
            break
        if c["no"] in used_no:
            continue
        legs.append(c)
        used_no.add(c["no"])

    data = {
        "date": day,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stake_per_bet": PARLAY_STAKE,
        "legs": [],
        "bets": [],
        "summary": {
            "total_stake": 0.0, "p_any": 0.0, "p_all": 0.0,
            "max_payout": 0.0, "expected_return": 0.0, "roi": 0.0,
        },
        "rule": {
            "text": "每日爆冷三注 + EV 最高的另一场备选，凑成四场；同一场只取一个比分；"
                    "四串五 = 4 注三串一 + 1 注四串一",
        },
        "dup_score": False,
        "note": "",
    }
    if len(legs) < 4:
        data["note"] = "今日满足条件的爆冷场次不足 4 场，凑不出四串五。"
        return data

    legs = legs[:4]
    legs.sort(key=lambda c: (-c["ev"], -c["score_p"]))
    bets = []
    for idx in itertools.combinations(range(4), 3):
        p = math.prod(legs[i]["score_p"] for i in idx)
        o = math.prod(legs[i]["odds"] for i in idx)
        bets.append({
            "kind": "三串一", "legs": [legs[i]["no"] for i in idx],
            "p": p, "odds": o, "payout": o * PARLAY_STAKE, "ev": p * o,
        })
    p4 = math.prod(l["score_p"] for l in legs)
    o4 = math.prod(l["odds"] for l in legs)
    bets.append({
        "kind": "四串一", "legs": [l["no"] for l in legs],
        "p": p4, "odds": o4, "payout": o4 * PARLAY_STAKE, "ev": p4 * o4,
    })

    p_any = 0.0
    for mask in range(16):
        if bin(mask).count("1") < 3:
            continue
        p_any += math.prod(
            (legs[i]["score_p"] if mask >> i & 1 else 1 - legs[i]["score_p"])
            for i in range(4))

    total_stake = PARLAY_STAKE * len(bets)
    expected = sum(b["ev"] * PARLAY_STAKE for b in bets)
    scores = [l["score"] for l in legs]
    data.update({
        "legs": legs,
        "bets": bets,
        "dup_score": len(set(scores)) < len(scores),
        "summary": {
            "total_stake": total_stake,
            "p_any": p_any,
            "p_all": p4,
            "max_payout": sum(b["payout"] for b in bets),
            "expected_return": expected,
            "roi": expected / total_stake if total_stake else 0.0,
        },
    })
    return data


PARLAY_TMPL = Template("""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>爆冷四串五 · $day</title><style>
*{box-sizing:border-box}
body{margin:0;background:#f5f7fa;color:#0f172a;
  font-family:"PingFang SC","Hiragino Sans GB","Helvetica Neue",Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:30px 26px 56px}
.eyebrow{font-size:12px;letter-spacing:.14em;color:#c2410c;font-weight:600}
h1{font-size:34px;line-height:1.2;margin:8px 0 6px;letter-spacing:-.02em}
.sub{color:#64748b;font-size:14px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 22px}
.chip{background:#ffedd5;color:#9a3412;border:1px solid #fed7aa;border-radius:999px;
  padding:5px 12px;font-size:12px;font-weight:600}
.chip.gray{background:#f1f5f9;color:#475569;border-color:#e2e8f0}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:26px}
.kpi{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:16px 16px 14px}
.kpi .k{font-size:12px;color:#64748b;margin-bottom:7px}
.kpi .v{font-size:24px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi .n{font-size:11px;color:#94a3b8;margin-top:5px}
.kpi.hot .v{color:#c2410c}.kpi.good .v{color:#15803d}.kpi.warn .v{color:#b45309}
section{margin-top:30px}
h2{font-size:19px;margin:0 0 4px;letter-spacing:-.01em}
.hint{font-size:12px;color:#64748b;margin:0 0 12px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f8fafc;color:#64748b;font-weight:600;text-align:left;
  padding:10px 14px;border-bottom:1px solid #e2e8f0;white-space:nowrap;font-size:12px}
td{padding:12px 14px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.match{font-weight:600}
.meta{display:block;font-size:11px;color:#94a3b8;margin-top:2px;font-weight:400}
.score{display:inline-block;background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;
  border-radius:8px;padding:3px 9px;font-size:15px;font-weight:700;font-variant-numeric:tabular-nums}
.odds{color:#c2410c;font-weight:700;font-variant-numeric:tabular-nums}
.badge{display:inline-block;background:#ffedd5;color:#9a3412;border-radius:999px;
  padding:2px 8px;font-size:11px;font-weight:600}
tr.four{background:#fff7ed}
tr.four td{border-top:1px solid #fed7aa}
.note{background:#fff;border:1px solid #e2e8f0;border-left:4px solid #c2410c;
  border-radius:10px;padding:15px 18px;font-size:13px;line-height:1.85;color:#334155}
.note b{color:#0f172a}
.note .small{font-size:12px;color:#64748b}
.risk{border-left-color:#b45309}
.empty{background:#fff;border:1px dashed #cfd8e6;border-radius:14px;
  padding:30px;color:#64748b;text-align:center;font-size:13px}
.footer{margin-top:34px;font-size:11px;color:#94a3b8;line-height:1.7}
@media (max-width:960px){.kpis{grid-template-columns:repeat(2,1fr)}.wrap{padding:22px 16px 44px}
  h1{font-size:27px}table{font-size:12px}th,td{padding:9px 10px}.hide-s{display:none}}
</style></head><body><div class="wrap">
<div class="eyebrow">竞彩比分助手 · 爆冷组合</div>
<h1>爆冷四串五</h1>
<div class="sub">$day · 4 场 × 5 注（4 注三串一 + 1 注四串一） · 每注 $stake 元，共 ¥$total_stake</div>
<div class="chips"><span class="chip">比分玩法</span>
<span class="chip">赔率 $odds_range 倍</span>
<span class="chip gray">按模型概率 × 抓取赔率计算</span>
<span class="chip gray">不保证命中</span></div>
$kpi
$body
<section><h2>口径与风险</h2><div class="note">
<b>怎么选的：</b>$rule<br>
$dup_note
<b>期望与波动：</b>按模型概率和未去水赔率，期望返奖 ¥$expected（回报率 $roi%）；赔率含市场抽水，真实期望更低。模型误差在两场 0–0 上会同向放大。
<br><span class="small">赔率为 $stamp 抓取，出票时可能变动；返奖上限与单票限额以实体店为准。只做概率记录，不代下单。</span>
</div></section>
<div class="footer">生成于 $stamp · 概率来自四模型（Dixon-Coles / Elo / xG / 机器学习）与市场比分盘融合，
比分赔率取自 500.com 未去水盘口。<br>爆冷比分是低概率事件，四串五把低概率再相乘；请按可承受损失下注。</div>
</div></body></html>""")


def render_parlay45(data):
    legs = data.get("legs") or []
    bets = data.get("bets") or []
    if not legs:
        body = f'<div class="empty">{_e(data.get("note") or "今天凑不出四串五。")}</div>'
        return PARLAY_TMPL.substitute(
            day=_e(data["date"]), stake=f"{PARLAY_STAKE:g}", total_stake="0",
            odds_range="—", kpi="", body=body, rule=_e(data["rule"]["text"]),
            dup_note="", expected="0.00", roi="0.0",
            stamp=_e(data["generated_at"]))

    sm = data["summary"]
    tri = [b["payout"] for b in bets if b["kind"] == "三串一"]
    kpi = (f'<div class="kpis">'
           f'<div class="kpi"><div class="k">总投入</div><div class="v">¥{_money(sm["total_stake"])}</div>'
           f'<div class="n">{len(bets)} 注 × {PARLAY_STAKE:g} 元</div></div>'
           f'<div class="kpi warn"><div class="k">任一中奖</div><div class="v">{_tiny(sm["p_any"])}</div>'
           f'<div class="n">约 1 / {1 / sm["p_any"]:,.0f}</div></div>'
           f'<div class="kpi good"><div class="k">期望返奖</div><div class="v">¥{sm["expected_return"]:.2f}</div>'
           f'<div class="n">按模型概率，赔率未去水</div></div>'
           f'<div class="kpi hot"><div class="k">全中最高</div><div class="v">¥{_money(sm["max_payout"])}</div>'
           f'<div class="n">4 场全中，5 注同时中</div></div>'
           f'<div class="kpi"><div class="k">命中 3 场</div>'
           f'<div class="v">¥{_money(min(tri))}–{_money(max(tri))}</div>'
           f'<div class="n">只中对应的 1 注三串一</div></div></div>')

    leg_rows = "".join(
        f'<tr><td>{_e(c["no"])}<span class="meta">{_e((c["kickoff"] or "")[5:16])}</span></td>'
        f'<td class="match">{_e(c["home"])} vs {_e(c["away"])}'
        f'<span class="meta">{_e(c["league"])}</span></td>'
        f'<td><span class="score">{_e(c["score"])}</span></td>'
        f'<td class="num odds">{c["odds"]:.2f}</td>'
        f'<td class="num">{c["score_p"] * 100:.2f}%</td>'
        f'<td><span class="badge">{"冷平" if c["direction"] == "平局" else "冷" + c["direction"]}</span></td>'
        f'<td class="hide-s">热{_e(c["fav"])} {c["fav_p"] * 100:.1f}%</td></tr>'
        for c in legs)

    def _bet_row(i, b):
        cls = ' class="four"' if b["kind"] == "四串一" else ""
        combo = " + ".join(_short_no(x) for x in b["legs"])
        return (f"<tr{cls}><td>{i}</td><td class=\"match\">{_e(b['kind'])}</td>"
                f"<td class=\"hide-s\">{combo}</td>"
                f"<td class=\"num\">{_tiny(b['p'])}</td>"
                f"<td class=\"num\">{b['odds']:,.0f}</td>"
                f"<td class=\"num odds\">¥{_money(b['payout'])}</td>"
                f"<td class=\"num\">{b['ev']:.2f}</td></tr>")

    bet_rows = "".join(_bet_row(i, b) for i, b in enumerate(bets, 1))

    body = f"""<section><h2>四场冷门</h2>
<p class="hint">从今日爆冷候选中取三注正选，再补 EV 最高的另一场；同一场只取一个比分。</p>
<div class="card"><table><thead><tr><th>场次</th><th>比赛</th><th>比分</th>
<th class="num">赔率</th><th class="num">模型概率</th><th>冷门方向</th><th class="hide-s">市场热门</th>
</tr></thead><tbody>{leg_rows}</tbody></table></div></section>
<section><h2>五注明细</h2>
<p class="hint">四串五 = 4 注三串一 + 1 注四串一。中 3 场只兑对应那注三串一；中 4 场则 5 注全中。</p>
<div class="card"><table><thead><tr><th>#</th><th>注项</th><th class="hide-s">组合</th>
<th class="num">命中概率</th><th class="num">赔率</th><th class="num">2 元返奖</th><th class="num">EV</th>
</tr></thead><tbody>{bet_rows}</tbody></table></div></section>"""

    scores = [c["score"] for c in legs]
    dups = sorted({s for s in scores if scores.count(s) > 1})
    dup_note = ""
    if dups:
        dup_note = (f'<b>同比分：</b>{"、".join(dups)} 出现在两场以上，等于把该比分集中下注；'
                    f'模型若系统性高估/低估它，几腿会同向错。<br>')

    odds = [c["odds"] for c in legs]
    return PARLAY_TMPL.substitute(
        day=_e(data["date"]), stake=f"{PARLAY_STAKE:g}",
        total_stake=_money(sm["total_stake"]),
        odds_range=f"{min(odds):g}–{max(odds):g}",
        kpi=kpi, body=body, rule=_e(data["rule"]["text"]),
        dup_note=dup_note,
        expected=f'{sm["expected_return"]:.2f}', roi=f'{sm["roi"] * 100:.1f}',
        stamp=_e(data["generated_at"]))


def write_parlay45(day):
    data = build_parlay45(day)
    PRO_REPORT.mkdir(parents=True, exist_ok=True)
    PRO_DATA.mkdir(parents=True, exist_ok=True)
    hp = PRO_REPORT / f"爆冷四串五_{day}.html"
    jp = PRO_DATA / f"爆冷四串五_{day}.json"
    hp.write_text(render_parlay45(data), encoding="utf-8")
    jp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return hp, jp


def _card(c, i):
    edge = c["edge"] * 100
    return f"""<article class="ucard">
  <div class="uhead"><span class="uno">{i}</span>
    <span class="umt">{_e(c['home'])} <em>vs</em> {_e(c['away'])}</span>
    <span class="utag">{_e(c['direction'])}冷 · 热门{_e(c['fav'])}</span></div>
  <div class="ubody">
    <div class="usc">{_e(c['score'])}</div>
    <div class="uinfo">
      <div class="uodds">赔率 <b>{c['odds']:.2f}</b></div>
      <div class="up2">模型 {_pct(c['score_p'])} · EV <b>{c['ev']:.2f}</b></div>
      <div class="up3">{_e(c['no'])} · {_e(c['league'])} · {_e((c['kickoff'] or '')[5:16])}
        · 模型{'高于' if edge >= 0 else '低于'}市场 {abs(edge):.1f} 个点</div>
    </div>
  </div>
</article>"""


def render(day, data):
    picks = data["picks"]
    alts = data["alternates"]
    if picks:
        top = picks[0]
        kpi = (f'<div class="kpis">'
               f'<div class="kpi"><div class="l">今日爆冷注</div><div class="v">{len(picks)}</div>'
               f'<div class="s">全部为高赔比分</div></div>'
               f'<div class="kpi"><div class="l">最高赔率</div><div class="v">{top["odds"]:.0f}</div>'
               f'<div class="s">{_e(top["score"])} · {_pct(top["score_p"])}</div></div>'
               f'<div class="kpi"><div class="l">最高 EV</div><div class="v">{top["ev"]:.2f}</div>'
               f'<div class="s">模型概率 × 赔率</div></div>'
               f'<div class="kpi"><div class="l">符合条件</div><div class="v">{data["candidates"]}</div>'
               f'<div class="s">{data["universe"]} 场里的高赔候选</div></div></div>')
        cards = "".join(_card(c, i + 1) for i, c in enumerate(picks))
    else:
        kpi = ""
        cards = '<div class="empty">今天没有同时满足“高赔 + 冷门方向 + 模型概率”的比分；宁缺毋滥。</div>'
    alt_rows = "".join(
        f"<tr><td>{_e(c['no'])}</td><td>{_e(c['home'])} vs {_e(c['away'])}</td>"
        f"<td class='sc'>{_e(c['score'])}</td><td>{c['odds']:.2f}</td>"
        f"<td>{_pct(c['score_p'])}</td><td>{c['ev']:.2f}</td>"
        f"<td>{_e(c['direction'])}（热门{_e(c['fav'])}）</td></tr>"
        for c in alts)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>爆冷比分 · {_e(day)}</title><style>
*{{box-sizing:border-box}}
body{{margin:0;background:#f4f6f9;color:#1e293b;
  font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}}
.wrap{{max-width:980px;margin:0 auto;padding:26px 20px 44px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px}}
h1{{margin:0;font-size:26px;color:#16283f}}
.sub{{margin-top:6px;font-size:13px;color:#64748b}}
.stamp{{text-align:right;font-size:12px;color:#94a3b8;line-height:1.7}}
.stamp b{{color:#334155;font-size:14px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}}
.kpi{{background:#fff;border:1px solid #e5eaf1;border-radius:12px;padding:12px 14px}}
.kpi .l{{font-size:11px;color:#94a3b8}}
.kpi .v{{font-size:24px;font-weight:700;color:#b91c1c;margin-top:2px}}
.kpi .s{{font-size:11px;color:#94a3b8;margin-top:1px}}
.ucard{{background:#fff;border:1px solid #f0d4d4;border-left:4px solid #b91c1c;
  border-radius:14px;padding:14px 16px;margin-bottom:12px;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
.uhead{{display:flex;align-items:center;gap:8px;font-size:13px;color:#475569}}
.uno{{width:22px;height:22px;border-radius:50%;background:#fef2f2;color:#b91c1c;
  font-size:12px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}}
.umt{{font-weight:700;color:#16283f;font-size:15px}}
.umt em{{font-style:normal;color:#cbd5e1;font-size:12px;padding:0 3px}}
.utag{{margin-left:auto;font-size:11px;color:#b91c1c;background:#fef2f2;
  border:1px solid #fecaca;border-radius:999px;padding:2px 9px}}
.ubody{{display:flex;align-items:center;gap:18px;margin-top:8px}}
.usc{{font-size:40px;font-weight:800;letter-spacing:1px;color:#b91c1c;
  font-variant-numeric:tabular-nums;min-width:110px}}
.uinfo{{flex:1}}
.uodds{{font-size:14px;color:#334155}}
.uodds b{{font-size:20px;color:#0f766e}}
.up2{{font-size:13px;color:#475569;margin-top:2px}}
.up2 b{{color:#b45309}}
.up3{{font-size:11.5px;color:#94a3b8;margin-top:3px}}
.empty{{background:#fff;border:1px dashed #cfd8e6;border-radius:14px;
  padding:26px;color:#64748b;text-align:center;font-size:13px}}
h2{{font-size:17px;margin:26px 0 10px;color:#16283f}}
.table{{background:#fff;border:1px solid #e5eaf1;border-radius:12px;overflow:auto}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th{{text-align:left;font-size:11px;color:#8a94a6;padding:9px 12px;
  border-bottom:1px solid #eef2f7;white-space:nowrap}}
td{{padding:8px 12px;border-bottom:1px solid #f3f6fa;white-space:nowrap;color:#334155}}
tr:last-child td{{border-bottom:none}}
td.sc{{font-weight:700;color:#b91c1c;font-variant-numeric:tabular-nums}}
.note{{margin-top:16px;font-size:12.5px;color:#64748b;line-height:1.8;
  background:#fff;border:1px dashed #dbe3ee;border-radius:10px;padding:12px 14px}}
.note b{{color:#334155}}
footer{{margin-top:18px;text-align:center;font-size:11.5px;color:#a8b3c2}}
@media (max-width:760px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<header><div><h1>爆冷比分</h1>
<div class="sub">高赔冷门比分 · 每天最多 {TOP_N} 注 · 只做概率记录</div></div>
<div class="stamp">日期 <b>{_e(day)}</b><br>生成 {_e(data['generated_at'])}</div></header>
{kpi}
{cards}
<h2>备选</h2>
<div class="table"><table>
<thead><tr><th>场次</th><th>对阵</th><th>比分</th><th>赔率</th><th>模型概率</th><th>EV</th><th>冷门方向</th></tr></thead>
<tbody>{alt_rows or '<tr><td colspan="7" style="padding:18px;color:#94a3b8">无</td></tr>'}</tbody>
</table></div>
<div class="note">
<b>怎么筛的：</b>{_e(data['rule']['text'])}。EV 是模型概率 × 竞彩赔率，大于 1 表示模型认为市场低估了这个比分。<br>
<b>必须知道的：</b>这些比分单格命中率只有 3%–9%，EV 大于 1 也只是“长期统计上不亏”的估算，不代表这一注会中；三注单买或串起来都是娱乐性质。<br>
<b>赔率：</b>封存时的 500.com 竞彩比分赔率（未去水）。所有数字为模型计算与历史校准记录，不构成投注建议。
</div>
<footer>由 jczq_pro 生成 · {_e(day)}</footer>
</div></body></html>"""


def write_upset(day, top_n=TOP_N):
    data = build(day, top_n)
    PRO_REPORT.mkdir(parents=True, exist_ok=True)
    PRO_DATA.mkdir(parents=True, exist_ok=True)
    hp = PRO_REPORT / f"爆冷比分_{day}.html"
    jp = PRO_DATA / f"爆冷比分_{day}.json"
    hp.write_text(render(day, data), encoding="utf-8")
    jp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return hp, jp
