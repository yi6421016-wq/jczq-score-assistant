"""命令行入口：fetch / daily / backtest。"""
import argparse
import datetime as dt
import webbrowser

from .backtest import load_cached_summary, predict_day, run_backtest
from .config import PRO_DATA, PRO_REPORT, ensure_dirs
from .data.source_500 import fetch_day, save_day
from .data.store import save_json


def _today():
    return dt.date.today().isoformat()


def cmd_fetch(args):
    ensure_dirs()
    matches = fetch_day(args.date)
    path = save_day(args.date, matches)
    print(f"已保存 {len(matches)} 场 → {path}")
    if args.predict:
        return cmd_daily(args)


def _fmt_probs(p):
    return f"主 {p[0]:.1%} / 平 {p[1]:.1%} / 客 {p[2]:.1%}"


def cmd_daily(args):
    summary = None
    if not args.no_backtest:
        print("先跑走步回测，取样本外指标…")
        summary, _ = run_backtest(evaluate_from=args.bt_from)
    else:
        summary = load_cached_summary()
        if summary:
            print(f"使用缓存的回测摘要（{summary.get('generated_at', '—')}）")
    result = predict_day(args.date)
    path = None
    if not args.no_report:
        path = write_and_report(result, summary)
    save_json(PRO_DATA / f"预测_{args.date}.json",
              {"result": result, "backtest": summary})
    ana = None
    if not args.no_report:
        try:
            from .analysis import write_analysis
            ana = write_analysis(args.date)
        except Exception as e:
            print(f"当日全解析生成失败：{e}")

    print(f"\n{args.date} 共 {len(result['matches'])} 场 · 截断 {result['cutoff']}")
    print(f"集成权重：{' / '.join(f'{k} {v:.0%}' for k, v in result['prior'].items() if v >= 0.001)}")
    print(f"比分分布：集成 {1-result['market_score_share']:.0%} + 市场比分盘 "
          f"{result['market_score_share']:.0%}（覆盖 {result['market_score_cover']}/{len(result['matches'])} 场）")
    for m in result["matches"]:
        ens = m["models"]["ens"]
        if not ens.get("available"):
            print(f"{m['no']} {m['home']} vs {m['away']}  集成不可用")
            continue
        scores = " ".join(f"{s}({p:.1%})" for s, p in m["score_top"]["ens"][:3])
        print(f"{m['no']} {m['league']} {m['home']} vs {m['away']}")
        print(f"   集成 {_fmt_probs(ens['probs'])} → {ens['pick']}   比分 {scores}")
        for key in ("dc", "elo", "xg", "ml"):
            o = m["models"][key]
            if o.get("available"):
                print(f"   {key:3s} {_fmt_probs(o['probs'])} → {o['pick']}")
            else:
                print(f"   {key:3s} 弃权：{o.get('note', '')}")
    if path:
        print(f"\n日报：{path}")
    else:
        print(f"\n快照：{PRO_DATA / f'预测_{args.date}.json'}")
    if ana:
        print(f"当日全解析：{ana}")
    return result


def write_and_report(result, summary=None):
    from .report import write_report
    path = write_report(result["date"], result, summary)
    try:
        webbrowser.open(path.as_uri())
    except Exception:
        pass
    return path


def _top_cell(v, k):
    key = f"score_top{k}"
    return f"{v[key]:.2%}" if key in v else "—"


def cmd_backtest(args):
    summary, _ = run_backtest(evaluate_from=args.evaluate_from)
    from .report import write_backtest_report
    path = write_backtest_report(summary)
    models = summary["models"]
    print(f"\n评估 {summary['n_matches']} 场 · {summary['from']} 至 {summary['to']}")
    print(f"{'模型':<14}{'覆盖':>6}{'LogLoss':>10}{'Brier':>8}{'1X2命中':>9}"
          f"{'Top1':>8}{'Top3':>8}{'Top5':>8}")
    for key in ("dc", "elo", "xg", "ml", "ens", "mkt_score", "market"):
        v = models.get(key)
        if not v or not v.get("n"):
            continue
        name = {"dc": "Dixon-Coles", "elo": "Elo", "xg": "xG", "ml": "机器学习",
                "ens": "集成", "mkt_score": "市场比分盘", "market": "市场去水"}[key]
        print(f"{name:<14}{v['coverage']:>6.0%}{v['logloss']:>10.4f}{v['brier']:>8.4f}"
              f"{v['acc']:>9.2%}"
              f"{_top_cell(v, 1):>8}{_top_cell(v, 3):>8}{_top_cell(v, 5):>8}")
    print(f"\n最终权重：{summary['final_prior']}")
    print(f"回测页：{path}")
    try:
        webbrowser.open(path.as_uri())
    except Exception:
        pass


def cmd_rank(args):
    from .rank import write_score_rank
    path = write_score_rank(args.date)
    print(f"比分胜率排行：{path}")
    return path


def cmd_recommend(args):
    from .recommend import write_recommend
    hp, jp, uh, uj, ph, pj = write_recommend(args.date, top_n=args.top)
    print(f"今日推荐：{hp}")
    print(f"爆冷比分：{uh}")
    print(f"爆冷四串五：{ph}")
    print(f"推荐数据：{jp}")
    print(f"爆冷数据：{uj}")
    print(f"四串五数据：{pj}")
    try:
        from .analysis import write_analysis
        print(f"当日全解析：{write_analysis(args.date)}")
    except Exception as e:
        print(f"当日全解析生成失败：{e}")
    return hp


def cmd_analysis(args):
    from .analysis import write_analysis
    path = write_analysis(args.date)
    print(f"当日全解析：{path}")
    return path


def cmd_upset(args):
    from .upset import write_upset
    hp, jp = write_upset(args.date, top_n=args.top)
    print(f"爆冷比分：{hp}")
    print(f"爆冷数据：{jp}")
    return hp


def cmd_parlay45(args):
    from .upset import write_parlay45
    hp, jp = write_parlay45(args.date)
    print(f"爆冷四串五：{hp}")
    print(f"四串五数据：{jp}")
    return hp


def build_parser():
    p = argparse.ArgumentParser(prog="jczq_pro", description="竞彩比分助手 · 四模型版")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="抓取并封存指定日期赛程")
    f.add_argument("--date", default=_today())
    f.add_argument("--predict", action="store_true", help="抓取后立即出日报")
    f.add_argument("--no-backtest", action="store_true")
    f.add_argument("--bt-from", default="2026-10-01")
    f.set_defaults(func=cmd_fetch)

    d = sub.add_parser("daily", help="生成指定日期的四模型日报")
    d.add_argument("--date", default=_today())
    d.add_argument("--no-backtest", action="store_true", help="跳过回测摘要，速度更快")
    d.add_argument("--no-report", action="store_true", help="只更新预测快照，不重写 HTML")
    d.add_argument("--bt-from", default="2026-10-01")
    d.set_defaults(func=cmd_daily)

    b = sub.add_parser("backtest", help="走步回测并生成指标页")
    b.add_argument("--evaluate-from", default="2026-10-01")
    b.set_defaults(func=cmd_backtest)

    r = sub.add_parser("rank", help="生成比分胜率排序页")
    r.add_argument("--date", default=_today())
    r.set_defaults(func=cmd_rank)

    c = sub.add_parser("recommend", help="生成今日推荐页（四玩法最稳几场）")
    c.add_argument("--date", default=_today())
    c.add_argument("--top", type=int, default=5, help="推荐场次数，默认 5")
    c.set_defaults(func=cmd_recommend)

    u = sub.add_parser("upset", help="生成爆冷比分页（高赔冷门比分）")
    u.add_argument("--date", default=_today())
    u.add_argument("--top", type=int, default=3, help="爆冷注数，默认 3")
    u.set_defaults(func=cmd_upset)

    p45 = sub.add_parser("parlay45", help="生成爆冷四串五组合页")
    p45.add_argument("--date", default=_today())
    p45.set_defaults(func=cmd_parlay45)

    an = sub.add_parser("analysis", help="生成当日逐场全解析文档")
    an.add_argument("--date", default=_today())
    an.set_defaults(func=cmd_analysis)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    ensure_dirs()
    print(f"新版数据：{PRO_DATA}\n新版报告：{PRO_REPORT}")
    args.func(args)


if __name__ == "__main__":
    main()