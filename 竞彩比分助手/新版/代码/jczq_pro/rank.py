"""比分胜率排行：按首选/次选/Top3/Top5 概率给当天比分排序。"""
import html
import json

from .config import MARKET_SCORE_SHARE, PRO_DATA, PRO_REPORT
from .report import CSS


def _e(x):
    return html.escape(str(x))


def _pct(x):
    return f"{x:.1%}"


def _direction(score):
    i, j = (int(v) for v in score.split("-"))
    return "主胜" if i > j else ("平局" if i == j else "客胜")


def _load(day):
    p = PRO_DATA / f"预测_{day}.json"
    if not p.exists():
        raise RuntimeError(f"{day} 还没有四模型快照，先点“刷新四模型”")
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("result") or {}


def build_rows(result):
    rows = []
    for m in result.get("matches", []):
        ens = (m.get("models") or {}).get("ens") or {}
        tops = (m.get("score_top") or {}).get("ens") or []
        if not ens.get("available") or not tops:
            continue
        top1, p1 = tops[0]
        top2, p2 = tops[1] if len(tops) > 1 else ("—", 0.0)
        t3 = sum(p for _, p in tops[:3])
        t5 = sum(p for _, p in tops[:5])
        mkt = ((m.get("score_top") or {}).get("mkt_score") or [])
        rows.append({
            "no": m.get("no", ""), "league": m.get("league", ""),
            "home": m.get("home", ""), "away": m.get("away", ""),
            "kickoff": m.get("kickoff", ""), "pick": ens.get("pick", "—"),
            "top1": top1, "p1": float(p1), "top2": top2, "p2": float(p2),
            "t3": t3, "t5": t5,
            "mkt": mkt[0][0] if mkt else "",
            "same": _direction(top1) == ens.get("pick"),
        })
    rows.sort(key=lambda r: -r["p1"])
    return rows


def _row_html(r, i):
    bar = min(100.0, r["p1"] / 0.15 * 100)
    same = ('<span class="tag ok">同向</span>' if r["same"]
            else '<span class="tag off">异向</span>')
    mkt = (f'<span class="mini">{_e(r["mkt"])}</span>' if r["mkt"] else "—")
    return (
        f'<tr data-p1="{r["p1"]:.6f}" data-p2="{r["p2"]:.6f}"'
        f' data-t3="{r["t3"]:.6f}" data-t5="{r["t5"]:.6f}">'
        f'<td class="rk">{i}</td>'
        f'<td><span class="no">{_e(r["no"])}</span><br><span class="mini">{_e(r["league"])}</span></td>'
        f'<td class="teams">{_e(r["home"])} <span>vs</span> {_e(r["away"])}'
        f'<br><span class="mini">{_e(r["kickoff"])}</span></td>'
        f'<td>{_e(r["pick"])}</td>'
        f'<td class="score">{_e(r["top1"])}</td>'
        f'<td class="pcell"><b>{_pct(r["p1"])}</b>'
        f'<span class="pbar"><i style="width:{bar:.0f}%"></i></span></td>'
        f'<td class="score">{_e(r["top2"])}</td>'
        f'<td class="n">{_pct(r["p2"])}</td>'
        f'<td class="n">{_pct(r["t3"])}</td>'
        f'<td class="n">{_pct(r["t5"])}</td>'
        f'<td class="n">{mkt}</td>'
        f'<td>{same}</td></tr>')


def render(day, result):
    rows = build_rows(result)
    n = len(rows)
    maxp = max((r["p1"] for r in rows), default=0.0)
    strong = sum(1 for r in rows if r["p1"] >= 0.10)
    body = "".join(_row_html(r, i + 1) for i, r in enumerate(rows))
    share = result.get("market_score_share", MARKET_SCORE_SHARE)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>比分胜率排序 · {_e(day)}</title><style>{CSS}
.sortbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 12px;font-size:13px;color:#64748b}}
.sortbar button{{font:inherit;font-size:13px;padding:6px 12px;border:1px solid #d8e0ea;
  background:#fff;border-radius:8px;color:#334155;cursor:pointer}}
.sortbar button.on{{background:#1f3b63;border-color:#1f3b63;color:#fff}}
.sortbar button:hover{{border-color:#1f3b63}}
td.rk{{font-weight:700;color:#1f3b63;font-variant-numeric:tabular-nums;text-align:center}}
td.teams{{font-weight:600}}
td.teams span{{color:#cbd5e1;font-weight:400;margin:0 4px}}
.mini{{font-size:11.5px;color:#94a3b8;font-weight:400}}
.pcell{{min-width:120px}}
.pcell b{{display:block;font-variant-numeric:tabular-nums;font-size:13px}}
.pbar{{display:block;height:5px;border-radius:3px;background:#eef2f7;margin-top:4px;overflow:hidden}}
.pbar i{{display:block;height:100%;background:linear-gradient(90deg,#3f74c9,#1f3b63);border-radius:3px}}
.tag{{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid}}
.tag.ok{{color:#15803d;border-color:#bbf7d0;background:#f0fdf4}}
.tag.off{{color:#b45309;border-color:#fde68a;background:#fffbeb}}
</style></head>
<body><div class="wrap">
<header><div><h1>预测比分胜率排序</h1>
<div class="sub">按首选比分概率从高到低 · 点上方按钮切换排序口径</div></div>
<div class="stamp">日期 <b>{_e(day)}</b><br>生成 {_e(result.get('generated_at', '—'))}</div></header>
<div class="note"><b>口径：</b>比分 Top5 = 集成矩阵 {1-share:.0%} + 市场比分盘 {share:.0%}；
“首选概率”是单个比分的概率，通常只有 7%–12%，排序只反映相对高低，不代表命中把握。所有数字为模型计算，不构成投注建议。</div>
<div class="cards">
<div class="card"><span>入榜场次</span><b>{n}</b><em>场</em></div>
<div class="card"><span>首选最高</span><b>{_pct(maxp)}</b><em>单场</em></div>
<div class="card"><span>首选 ≥10%</span><b>{strong}</b><em>场</em></div>
<div class="card"><span>比分盘覆盖</span><b>{result.get('market_score_cover', 0)}/{n}</b><em>场</em></div>
</div>
<div class="sortbar"><span>排序：</span>
<button data-k="p1" class="on">首选概率</button>
<button data-k="p2">次选概率</button>
<button data-k="t3">Top3 累计</button>
<button data-k="t5">Top5 累计</button>
</div>
<table id="rk"><colgroup>
<col style="width:5%"><col style="width:9%"><col style="width:22%"><col style="width:7%">
<col style="width:7%"><col style="width:12%"><col style="width:7%"><col style="width:8%">
<col style="width:8%"><col style="width:8%"><col style="width:7%"><col style="width:7%">
</colgroup><thead><tr><th class="n">排名</th><th>场次</th><th>对阵</th><th>倾向</th>
<th>首选比分</th><th>首选概率</th><th>次选</th><th class="n">次选概率</th>
<th class="n">Top3 累计</th><th class="n">Top5 累计</th><th class="n">市场首选</th><th>与倾向</th>
</tr></thead><tbody id="rkb">{body}</tbody></table>
<footer>由 jczq_pro 生成 · 数据源 500.com 竞彩 · 概率为模型计算，不构成投注建议</footer>
</div>
<script>
(function(){{
  var tb=document.getElementById('rkb');
  var btns=document.querySelectorAll('.sortbar button');
  btns.forEach(function(b){{
    b.addEventListener('click',function(){{
      btns.forEach(function(x){{x.classList.remove('on');}});
      b.classList.add('on');
      var k=b.getAttribute('data-k');
      var rows=Array.prototype.slice.call(tb.querySelectorAll('tr'));
      rows.sort(function(a,b){{return parseFloat(b.dataset[k])-parseFloat(a.dataset[k]);}});
      rows.forEach(function(tr,i){{tr.cells[0].textContent=i+1; tb.appendChild(tr);}});
    }});
  }});
}})();
</script>
</body></html>"""


def write_score_rank(day):
    result = _load(day)
    PRO_REPORT.mkdir(parents=True, exist_ok=True)
    p = PRO_REPORT / f"比分胜率排行_{day}.html"
    p.write_text(render(day, result), encoding="utf-8")
    return p