"""HTML 报告：四模型日报、走步回测页。产物写新版目录。"""
import datetime as dt
import html

from .config import DC_RHO, MARKET_SCORE_SHARE, PRO_REPORT

NAME_CN = {"dc": "Dixon-Coles", "elo": "Elo 评分", "xg": "xG 射门",
           "ml": "机器学习", "ens": "四模型集成", "mkt_score": "市场比分盘"}
PICK_COLOR = {"主胜": "#d94b4b", "平局": "#8a94a6", "客胜": "#3f74c9"}

CSS = """
:root{--ink:#1e293b;--mute:#64748b;--line:#e2e8f0;--accent:#1f3b63}
*{box-sizing:border-box}
body{margin:0;background:#f5f7fa;color:var(--ink);
  font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1280px;margin:30px auto 60px;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:18px;gap:16px}
h1{font-size:23px;margin:0;letter-spacing:.5px}
.sub{color:var(--mute);font-size:13px;margin-top:6px}
.stamp{text-align:right;color:var(--mute);font-size:13px;line-height:1.55;white-space:nowrap}
.stamp b{color:var(--accent);font-size:15px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:18px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.card span{display:block;font-size:12px;color:var(--mute)}
.card b{font-size:20px;color:var(--accent);font-variant-numeric:tabular-nums}
.card em{font-style:normal;font-size:12px;color:#94a3b8;margin-left:4px}
.note{background:#fff7e6;border:1px solid #f5d9a8;border-radius:10px;padding:10px 14px;
  margin:0 0 18px;font-size:13px;color:#7c4a03}
.note b{color:#b45309}
h2{font-size:17px;margin:28px 0 12px;color:var(--accent)}
h2 small{font-weight:400;font-size:12.5px;color:var(--mute);margin-left:8px}
.mcard{background:#fff;border:1px solid var(--line);border-radius:12px;
  padding:14px 16px 12px;margin-bottom:12px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.mhead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.no{font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums}
.lg{font-size:12px;padding:2px 8px;border-radius:6px;background:#eef2f7;color:#475569;white-space:nowrap}
.teams{font-weight:600;font-size:15.5px}
.teams span{color:#cbd5e1;margin:0 6px;font-size:12px;font-weight:400}
.kick{margin-left:auto;color:var(--mute);font-size:12.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
.probbar{display:flex;height:9px;border-radius:5px;overflow:hidden;background:#f1f5f9;margin:6px 0 3px}
.seg{display:block;height:100%}
.nums{display:flex;justify-content:space-between;font-size:12px;color:var(--mute);
  font-variant-numeric:tabular-nums;margin-bottom:8px}
.nums i{font-style:normal}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;
  border:1px solid var(--line);border-radius:10px;overflow:hidden;table-layout:fixed}
th{background:#eef2f7;color:#334155;font-size:12px;font-weight:600;text-align:left;
  padding:8px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 9px;border-bottom:1px solid #f1f5f9;vertical-align:middle;font-size:13px}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fbfdff}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.mname{font-weight:600;white-space:nowrap}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid;
  font-size:12.5px;white-space:nowrap}
.score{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.scores{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;font-size:12px;color:var(--mute);align-items:center}
.scores b{color:var(--ink);font-variant-numeric:tabular-nums}
.chip{background:#f8fafc;border:1px solid #e8eef5;border-radius:7px;padding:2px 8px;white-space:nowrap}
.muted{color:#94a3b8;font-size:12px}
.modelnote{margin-top:6px;font-size:12px;color:#94a3b8}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}.kick{margin-left:0}}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);
  color:#94a3b8;font-size:12px;line-height:1.8}
.best{background:#ecfdf3;font-weight:600}
details{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:10px}
summary{cursor:pointer;font-weight:600;color:var(--accent)}
"""


def _e(x):
    return html.escape(str(x))


def _pct(x, digits=1):
    return f"{x * 100:.{digits}f}%"


def _bar(probs):
    segs = "".join(
        f'<span class="seg" style="width:{max(0.0, p) * 100:.2f}%;background:{c}"></span>'
        for p, c in zip(probs, (PICK_COLOR["主胜"], PICK_COLOR["平局"], PICK_COLOR["客胜"])))
    nums = (
        f'<i style="color:{PICK_COLOR["主胜"]}">主胜 {_pct(probs[0])}</i>'
        f'<i style="color:{PICK_COLOR["平局"]}">平 {_pct(probs[1])}</i>'
        f'<i style="color:{PICK_COLOR["客胜"]}">客胜 {_pct(probs[2])}</i>')
    return f'<div class="probbar">{segs}</div><div class="nums">{nums}</div>'


def _pill(pick):
    c = PICK_COLOR.get(pick, "#8a94a6")
    return f'<span class="pill" style="color:{c};border-color:{c}">{_e(pick)}</span>'


def _weight_text(prior):
    parts = [f'{NAME_CN.get(k, k)} {v:.0%}' for k, v in prior.items() if v >= 0.001]
    return " / ".join(parts) if parts else "—"


def _match_block(m):
    models = m["models"]
    ens = models["ens"]
    home, away = _e(m["home"]), _e(m["away"])
    head = (f'<div class="mhead"><span class="no">{_e(m["no"])}</span>'
            f'<span class="lg">{_e(m["league"])}</span>'
            f'<span class="teams">{home}<span>vs</span>{away}</span>'
            f'<span class="kick">{_e(m["kickoff"])}</span></div>')
    if not ens.get("available"):
        return f'<div class="mcard">{head}<div class="muted">集成不可用：{_e(ens.get("note", ""))}</div></div>'

    rows = []
    if m.get("market"):
        mk = m["market"]
        rows.append(
            f'<tr><td class="mname">市场去水</td><td class="n">{_pct(mk[0])}</td>'
            f'<td class="n">{_pct(mk[1])}</td><td class="n">{_pct(mk[2])}</td>'
            f'<td>{_pill(("主胜", "平局", "客胜")[mk.index(max(mk))])}</td></tr>')
    for key in ("dc", "elo", "xg", "ml"):
        o = models.get(key, {})
        if not o.get("available"):
            reason = _e(o.get("note", "不可用"))
            rows.append(
                f'<tr><td class="mname">{NAME_CN[key]}</td>'
                f'<td class="muted" colspan="3">弃权：{reason}</td><td class="muted">—</td></tr>')
            continue
        p = o["probs"]
        rows.append(
            f'<tr><td class="mname">{NAME_CN[key]}</td><td class="n">{_pct(p[0])}</td>'
            f'<td class="n">{_pct(p[1])}</td><td class="n">{_pct(p[2])}</td>'
            f'<td>{_pill(o["pick"])}</td></tr>')
    ep = ens["probs"]
    rows.append(
        f'<tr><td class="mname">四模型集成</td><td class="n"><b>{_pct(ep[0])}</b></td>'
        f'<td class="n"><b>{_pct(ep[1])}</b></td><td class="n"><b>{_pct(ep[2])}</b></td>'
        f'<td>{_pill(ens["pick"])}</td></tr>')

    scores = m.get("score_top", {}).get("ens", [])[:5]
    chips = "".join(f'<span class="chip"><b>{_e(s)}</b> {_pct(p)}</span>' for s, p in scores)
    share = m.get("score_share")
    score_label = ("比分 Top5" if not share else
                   f"比分 Top5 <small>集成 {1-share:.0%} + 市场比分盘 {share:.0%}</small>")
    notes = "；".join(f'{NAME_CN[k]}：{_e(n)}' for k, n in m.get("notes", {}).items()
                     if n and k in NAME_CN)
    return (
        f'<div class="mcard">{head}{_bar(ep)}'
        f'<table><colgroup><col style="width:20%"><col style="width:17%"><col style="width:17%">'
        f'<col style="width:17%"><col style="width:20%"></colgroup>'
        f'<thead><tr><th>模型</th><th class="n">主胜</th><th class="n">平</th>'
        f'<th class="n">客胜</th><th>倾向</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
        f'<div class="scores"><span>{score_label}</span>{chips}</div>'
        f'<div class="modelnote">{notes}</div></div>')


def render_daily(result, summary=None):
    n = len(result["matches"])
    cover = sum(1 for m in result["matches"] if m["models"]["xg"].get("available"))
    spread = sum(1 for m in result["matches"]
                 if len({o.get("pick") for k, o in m["models"].items()
                         if k != "ens" and o.get("available")}) > 1)
    cards = (
        f'<div class="card"><span>比赛场次</span><b>{n}</b><em>场</em></div>'
        f'<div class="card"><span>ML 训练样本</span><b>{result["train_rows"]}</b><em>场</em></div>'
        f'<div class="card"><span>xG 可用</span><b>{cover}/{n}</b><em>场</em></div>'
        f'<div class="card"><span>模型分歧</span><b>{spread}/{n}</b><em>场</em></div>')
    blocks = "".join(_match_block(m) for m in result["matches"])
    bt = ""
    if summary:
        bt = ("<h2>样本外回测 <small>走步预测，仅使用开球前信息</small></h2>"
              + _metrics_table(summary, compact=True))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>竞彩比分日报 · 四模型版 · {_e(result['date'])}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><div><h1>竞彩比分日报 · 四模型版</h1>
<div class="sub">Dixon-Coles + Elo + xG + 机器学习 · 走步回测集成</div></div>
<div class="stamp">生成 <b>{_e(result['generated_at'])}</b><br>截断时点 {_e(result['cutoff'])}</div></header>
<div class="note"><b>口径：</b>所有概率为模型估计，不含投注建议。集成权重 = {_e(_weight_text(result['prior']))}；
权重由最近 220 场样本外 LogLoss 优化后向先验收缩，缺覆盖时自动归一化。</div>
{cards}{blocks}
{bt}
<h2>模型说明</h2>
<details><summary>四个模型各自在算什么</summary>
<p><b>Dixon-Coles：</b>竞彩胜平负赔率 Shin 去水 → 反解主客 λ → 10×10 比分矩阵，低比分格用 ρ={DC_RHO:+.2f} 修正。1X2 与市场同源，所以它的 LogLoss 基本等于市场基线，这是设计使然，不是缺陷。</p>
<p><b>Elo：</b>只吃历史赛果，初始 1500、主场 +60、K=20、净胜球乘数；再把评分差映射成 1X2 与 λ。信息源与赔率独立，主要用来发现市场没反映的状态变化。</p>
<p><b>xG：</b>用 football-data.co.uk 的射正数代理 xG（主 0.287×射正+0.155，客 0.277×射正+0.140），向联赛均值收缩 2 场。只有 9 大联赛有射门数据；亚冠、欧冠无数据时直接弃权，不拿赔率冒充。</p>
<p><b>机器学习：</b>sklearn HistGradientBoosting（8GB 机器上替代 XGBoost/LightGBM），20 个赛前特征：赔率、Elo、近期攻防、休息天数、xG 滚动、联赛编码。严格走步训练，每 200 场重训，不做未来数据泄漏。</p>
<p><b>市场比分盘：</b>500.com 比分玩法 31 档赔率（含“胜/平/负其它”）Shin 去水成 10×10 矩阵，“其它”按集成矩阵同方向未单列格摊开。最终比分 Top5 = 集成矩阵 {1-MARKET_SCORE_SHARE:.0%} + 市场比分盘 {MARKET_SCORE_SHARE:.0%}，只影响比分分布，不改 1X2 倾向。</p>
</details>
<footer>由 jczq_pro 生成 · 数据源 500.com 竞彩 + football-data.co.uk · 旧版系统与定时任务未改动<br>
概率来自模型计算，不构成投注建议，也不承诺命中。</footer>
</div></body></html>"""


def _metrics_table(summary, compact=False):
    models = summary.get("models", {})
    order = [k for k in ("dc", "elo", "xg", "ml", "ens", "mkt_score", "market")
             if k in models]
    if not order:
        return ""
    best_ll = min((models[k]["logloss"] for k in order if "logloss" in models[k]), default=None)
    best_acc = max((models[k]["acc"] for k in order if "acc" in models[k]), default=None)
    best_t1 = max((models[k].get("score_top1", -1) for k in order), default=None)
    rows = []
    for k in order:
        v = models[k]
        if not v.get("n"):
            continue
        cells = [
            f'<td class="mname">{NAME_CN.get(k, "市场去水")}</td>',
            f'<td class="n">{v["coverage"]:.0%}</td>',
            f'<td class="n">{v["n"]}</td>',
            f'<td class="n{" best" if v["logloss"] == best_ll else ""}">{v["logloss"]:.4f}</td>',
            f'<td class="n">{v["brier"]:.4f}</td>',
            f'<td class="n{" best" if v["acc"] == best_acc else ""}">{v["acc"]:.2%}</td>',
        ]
        for top in (1, 3, 5):
            key = f"score_top{top}"
            if key in v:
                cls = " best" if top == 1 and v[key] == best_t1 else ""
                cells.append(f'<td class="n{cls}">{v[key]:.2%}</td>')
            else:
                cells.append('<td class="n muted">—</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head = ("<tr><th>模型</th><th class='n'>覆盖</th><th class='n'>样本</th>"
            "<th class='n'>LogLoss</th><th class='n'>Brier</th><th class='n'>1X2 命中</th>"
            "<th class='n'>比分 Top1</th><th class='n'>Top3</th><th class='n'>Top5</th></tr>")
    table = (f'<table><colgroup><col style="width:19%"><col style="width:9%"><col style="width:9%">'
             f'<col style="width:11%"><col style="width:10%"><col style="width:11%">'
             f'<col style="width:10%"><col style="width:10%"><col style="width:11%"></colgroup>'
             f'<thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>')
    if compact:
        return table
    cal = summary.get("calibration") or []
    cal_rows = "".join(
        f'<tr><td>{_e(c["bin"])}</td><td class="n">{c["n"]}</td>'
        f'<td class="n">{c["pred"]:.1%}</td><td class="n">{c["hit"]:.1%}</td>'
        f'<td class="n">{c["hit"] - c["pred"]:+.1%}</td></tr>' for c in cal)
    cal_html = ""
    if cal_rows:
        cal_html = (
            '<h2>集成校准 <small>按预测类概率分箱</small></h2>'
            '<table><colgroup><col style="width:26%"><col style="width:18%">'
            '<col style="width:18%"><col style="width:19%"><col style="width:19%"></colgroup>'
            '<thead><tr><th>预测概率区间</th><th class="n">场次</th><th class="n">预测均值</th>'
            '<th class="n">实际命中</th><th class="n">偏差</th></tr></thead>'
            f'<tbody>{cal_rows}</tbody></table>')
    return table + cal_html


def render_backtest(summary):
    cards = (
        f'<div class="card"><span>评估场次</span><b>{summary["n_matches"]}</b><em>场</em></div>'
        f'<div class="card"><span>评估区间</span><b style="font-size:14px">{_e(summary["from"])}</b>'
        f'<em>至 {_e(summary["to"])}</em></div>'
        f'<div class="card"><span>ML 训练行</span><b>{summary.get("train_rows", "—")}</b><em>行</em></div>'
        f'<div class="card"><span>模型分歧率</span><b>{summary.get("disagree", 0):.0%}</b><em>场</em></div>')
    final = summary.get("final_prior", {})
    weight = "".join(f'<span class="chip">{NAME_CN.get(k, k)} <b>{v:.1%}</b></span>'
                     for k, v in final.items())
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>四模型走步回测 · {_e(summary.get('from', ''))} 至 {_e(summary.get('to', ''))}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><div><h1>四模型走步回测</h1>
<div class="sub">每场预测只使用开球前的赛果、赔率与射门数据</div></div>
<div class="stamp">生成 <b>{_e(summary.get("generated_at", ""))}</b><br>评估起点 {_e(summary.get("from", ""))}</div></header>
{cards}
<div class="note"><b>解读：</b>DC 的 1X2 由竞彩赔率反解，和“市场去水”同源，LogLoss 几乎相同是预期结果；
Elo 与赔率独立，LogLoss 略差但能提供不同信号；xG 只在有射门数据的联赛启用，覆盖率明显偏低；
ML 受样本与特征质量限制，实际权重不高。集成用滚动 LogLoss 优化权重，追求的是概率质量与比分分布的平衡。</div>
<h2>指标对比 <small>绿色为该列最优</small></h2>
{_metrics_table(summary)}
<h2>最终集成权重 <small>最近 220 场样本外优化，向先验收缩 0.6</small></h2>
<div class="scores">{weight}</div>
<h2>口径与限制</h2>
<details open><summary>哪些结论能信、哪些不能</summary>
<p><b>1X2：</b>集成命中率与市场基线接近，提升主要来自 Elo/ML 在部分场次纠正市场偏差，不是系统性优势。</p>
<p><b>比分：</b>Top1 约 10%–11%、Top3 约 33%、Top5 约 45%，这是比分玩法的结构上限：31 个选项中单格概率很难超过 15%。</p>
<p><b>xG：</b>射正代理 xG 的 R² 只有约 0.32，定位是“比单看进球稳一点的强度指标”，不是精确 xG。</p>
<p><b>ML：</b>HistGradientBoosting 在 3000 多场上训练，特征以赔率为主；如果去掉赔率特征，表现会明显下降。它的价值在于非线性组合，不在替代市场。</p>
<p><b>未覆盖：</b>亚冠、欧冠正赛没有外部射门数据；伤停、轮换、天气均未纳入。任何概率都不应视为确定结果。</p>
</details>
<footer>由 jczq_pro 生成 · 旧版系统与定时任务未改动 · 概率为模型计算，不构成投注建议</footer>
</div></body></html>"""


def write_report(day, result, summary=None):
    PRO_REPORT.mkdir(parents=True, exist_ok=True)
    path = PRO_REPORT / f"比分日报_四模型_{day}.html"
    path.write_text(render_daily(result, summary), encoding="utf-8")
    _write_index()
    return path


def write_backtest_report(summary):
    PRO_REPORT.mkdir(parents=True, exist_ok=True)
    tag = dt.date.today().isoformat()
    path = PRO_REPORT / f"回测_四模型_{tag}.html"
    path.write_text(render_backtest(summary), encoding="utf-8")
    _write_index()
    return path


def _write_index():
    files = sorted(PRO_REPORT.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    items = "".join(
        f'<li><a href="{_e(p.name)}">{_e(p.stem)}</a>'
        f'<span class="muted"> {dt.datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}</span></li>'
        for p in files if p.name != "index.html")
    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>新版报告索引</title><style>{CSS}</style></head><body><div class="wrap">
<header><div><h1>竞彩比分助手 · 新版报告</h1>
<div class="sub">四模型：Dixon-Coles / Elo / xG / 机器学习</div></div></header>
<ul>{items}</ul>
<footer>旧版报告仍在 ~/竞彩比分助手/报告/，未被覆盖。</footer>
</div></body></html>"""
    (PRO_REPORT / "index.html").write_text(page, encoding="utf-8")