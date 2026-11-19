"""走步回测与当日预测。核心约束：预测时只能用开球前的信息。"""
import datetime as dt
import json
import math

import numpy as np
from scipy.optimize import minimize

from .config import (ENSEMBLE_PRIOR, MARKET_SCORE_SHARE, ML_MIN_TRAIN,
                     ML_RETRAIN_EVERY, PRO_DATA)
from .data.store import load_day, load_unified
from .ensemble import combine
from .history import HistoryState
from .mathx import (bf_matrix, blend_matrix, matrix_1x2, result_index, shin,
                    top_scores)
from .models.dixon_coles import DixonColes
from .models.elo import EloModel
from .models.ml import LeagueEncoder, MLModel, feature_row
from .models.xg import XGModel

MODELS = ("dc", "elo", "xg", "ml", "ens", "mkt_score")


def predict_all(m, state, ml, prior=None):
    outs = {
        "dc": DixonColes().predict(m),
        "elo": EloModel().predict(m, state),
        "xg": XGModel().predict(m, state),
        "ml": ml.predict(m, state),
    }
    outs["ens"] = combine(list(outs.values()), prior=prior)
    return outs


def _rolling_prior(records, base, window=220, shrink=0.6):
    """在最近窗口上直接最小化 LogLoss 求线性池权重，再向先验收缩。因果。"""
    names = ("dc", "elo", "xg", "ml")
    sub = records[-window:]
    if len(sub) < 80:
        s = sum(base.values())
        return {k: v / s for k, v in base.items()}
    x0 = np.array([base.get(n, 0.01) for n in names], dtype=float)
    x0 /= x0.sum()

    def obj(w):
        total, n = 0.0, 0
        for r in sub:
            xs = [(w[i], r["probs"][names[i]]) for i in range(4)
                  if names[i] in r["probs"]]
            tw = sum(a for a, _ in xs)
            if tw <= 1e-9:
                continue
            p = [sum(a * pr[c] for a, pr in xs) / tw for c in range(3)]
            total -= math.log(max(p[r["result"]], 1e-9))
            n += 1
        return total / max(n, 1)

    res = minimize(obj, x0, method="SLSQP", bounds=[(0, 1)] * 4,
                   constraints={"type": "eq", "fun": lambda w: w.sum() - 1},
                   options={"maxiter": 80, "ftol": 1e-6})
    w = res.x if res.success else x0
    out = {n: shrink * float(w[i]) + (1 - shrink) * base.get(n, 0.0)
           for i, n in enumerate(names)}
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def _fuse_scores(m, ens_matrix):
    """集成比分矩阵与市场比分盘混合；没有比分盘时原样返回。"""
    if not m.get("bf_odds"):
        return ens_matrix, None
    bf = bf_matrix(m["bf_odds"], ens_matrix)
    if not bf:
        return ens_matrix, None
    return blend_matrix(ens_matrix, bf, MARKET_SCORE_SHARE), bf


def _record(m, outs):
    idx = result_index(m["score"])
    probs = {k: list(o.probs) for k, o in outs.items() if o.available}
    if m.get("odds"):
        probs["market"] = list(shin(m["odds"])[0])
    score_top = {k: [s for s, _ in top_scores(o.matrix, 5)]
                 for k, o in outs.items() if o.available and o.matrix}
    score_share = None
    if outs["ens"].available and outs["ens"].matrix:
        fused, bf = _fuse_scores(m, outs["ens"].matrix)
        score_share = MARKET_SCORE_SHARE if bf else None
        score_top["ens"] = [s for s, _ in top_scores(fused, 5)]
        if bf:
            probs["mkt_score"] = list(matrix_1x2(bf))
            score_top["mkt_score"] = [s for s, _ in top_scores(bf, 5)]
    return {
        "date": m["date"], "league": m.get("league", ""),
        "home": m["home"], "away": m["away"], "score": m["score"],
        "score_norm": m["score"].replace(":", "-"),
        "result": idx, "source": m.get("source", ""),
        "probs": probs,
        "picks": {k: o.pick for k, o in outs.items() if o.available},
        "score_top": score_top,
        "score_share": score_share,
        "notes": {k: o.note for k, o in outs.items()},
    }


def replay(unified, cutoff=None, evaluate_from=None, log=print):
    enc = LeagueEncoder([m.get("league", "") for m in unified])
    state = HistoryState()
    ml = MLModel(enc)
    rows, records, recent = [], [], []
    prior = dict(ENSEMBLE_PRIOR)
    for m in unified:
        if cutoff and m["ts"] >= cutoff:
            break
        labelled = bool(m.get("score"))
        if labelled and m.get("odds"):
            if m.get("source") in ("sealed", "backfill"):
                if len(recent) % 20 == 0 and len(recent) >= 80:
                    prior = _rolling_prior(recent, ENSEMBLE_PRIOR)
                outs = predict_all(m, state, ml, prior=prior)
                rec = _record(m, outs)
                recent.append(rec)
                recent = recent[-220:]
                if (not evaluate_from or m["date"] >= evaluate_from) and outs["ens"].available:
                    records.append(rec)
            label = result_index(m["score"])
            if label is not None:
                rows.append((feature_row(m, state, enc), label))
        state.update(m)
        if len(rows) >= ML_MIN_TRAIN and (
                ml.clf is None or len(rows) % ML_RETRAIN_EVERY == 0):
            ml.train(rows)
    return state, ml, rows, records, recent


def _logloss(probs, idx):
    p = max(probs[idx], 1e-9)
    return -math.log(p)


def _brier(probs, idx):
    return sum((p - (1.0 if i == idx else 0.0)) ** 2 for i, p in enumerate(probs))


def summarize(records):
    n = len(records)
    out = {"n_matches": n,
           "from": min((r["date"] for r in records), default=None),
           "to": max((r["date"] for r in records), default=None),
           "models": {}}
    for name in MODELS:
        sub = [r for r in records if name in r["probs"]]
        if not sub:
            out["models"][name] = {"coverage": 0}
            continue
        ll = np.mean([_logloss(r["probs"][name], r["result"]) for r in sub])
        br = np.mean([_brier(r["probs"][name], r["result"]) for r in sub])
        acc = np.mean([int(np.argmax(r["probs"][name]) == r["result"]) for r in sub])
        entry = {"coverage": round(len(sub) / n, 3), "n": len(sub),
                 "logloss": round(float(ll), 4), "brier": round(float(br), 4),
                 "acc": round(float(acc), 4)}
        if name in ("dc", "elo", "xg", "ml", "ens", "mkt_score"):
            for k in (1, 3, 5):
                entry[f"score_top{k}"] = round(float(np.mean(
                    [int(r["score_norm"] == r["score_top"][name][0]) if k == 1
                     else int(r["score_norm"] in r["score_top"][name][:k]) for r in sub])), 4)
        out["models"][name] = entry
    # 市场基线
    msub = [r for r in records if "market" in r["probs"]]
    if msub:
        out["models"]["market"] = {
            "coverage": round(len(msub) / n, 3), "n": len(msub),
            "logloss": round(float(np.mean([_logloss(r["probs"]["market"], r["result"]) for r in msub])), 4),
            "brier": round(float(np.mean([_brier(r["probs"]["market"], r["result"]) for r in msub])), 4),
            "acc": round(float(np.mean([int(np.argmax(r["probs"]["market"]) == r["result"]) for r in msub])), 4),
        }
    # 集成校准（按预测类概率分箱）
    bins = [[] for _ in range(6)]
    for r in records:
        if "ens" not in r["probs"]:
            continue
        p = max(r["probs"]["ens"])
        i = min(5, int(p * 6))
        bins[i].append((p, int(np.argmax(r["probs"]["ens"]) == r["result"])))
    cal = []
    for i, b in enumerate(bins):
        if b:
            cal.append({"bin": f"{i/6:.0%}-{(i+1)/6:.0%}", "n": len(b),
                        "pred": round(float(np.mean([x[0] for x in b])), 3),
                        "hit": round(float(np.mean([x[1] for x in b])), 3)})
    out["calibration"] = cal
    # 各模型分歧
    out["disagree"] = round(float(np.mean([
        int(len(set(r["picks"].values())) > 1) for r in records if "ens" in r["probs"]])), 3)
    return out


def run_backtest(evaluate_from="2026-10-01", log=print):
    unified = load_unified(log=log)
    log(f"统一样本 {len(unified)} 场（含外部联赛）")
    state, ml, rows, records, recent = replay(unified, evaluate_from=evaluate_from, log=log)
    summary = summarize(records)
    summary["generated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary["train_rows"] = len(rows)
    summary["ml_trained"] = ml.clf is not None
    summary["elo_teams"] = len(state.elo)
    summary["final_prior"] = {k: round(v, 3) for k, v in
                              _rolling_prior(recent, ENSEMBLE_PRIOR).items()}
    try:
        PRO_DATA.mkdir(parents=True, exist_ok=True)
        (PRO_DATA / "回测摘要.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"回测摘要缓存失败：{e}")
    return summary, records


def load_cached_summary():
    p = PRO_DATA / "回测摘要.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def predict_day(day, log=print):
    unified = load_unified(log=log)
    pending = load_day(day)
    if not pending:
        raise RuntimeError(f"{day} 没有封存赛程")
    cutoff = min(m["ts"] for m in pending)
    state, ml, rows, _, recent = replay(unified, cutoff=cutoff, log=log)
    prior = _rolling_prior(recent, ENSEMBLE_PRIOR)
    out = []
    for m in sorted(pending, key=lambda x: x["ts"]):
        outs = predict_all(m, state, ml, prior=prior)
        market = shin(m["odds"])[0] if m.get("odds") else None
        score_top = {k: [[s, round(p, 4)] for s, p in top_scores(o.matrix, 5)]
                     for k, o in outs.items() if o.matrix}
        score_share = None
        if outs["ens"].available and outs["ens"].matrix:
            fused, bf = _fuse_scores(m, outs["ens"].matrix)
            if bf:
                score_share = MARKET_SCORE_SHARE
                score_top["ens"] = [[s, round(p, 4)] for s, p in top_scores(fused, 5)]
                score_top["mkt_score"] = [[s, round(p, 4)] for s, p in top_scores(bf, 5)]
        out.append({
            "no": m.get("no") or m.get("fid") or "",
            "kickoff": m["ts"].strftime("%Y-%m-%d %H:%M"),
            "league": m.get("league", ""),
            "home": m["home"], "away": m["away"],
            "odds": m.get("odds"),
            "market": [round(x, 4) for x in market] if market else None,
            "models": {k: o.to_dict() for k, o in outs.items()},
            "score_top": score_top,
            "score_share": score_share,
            "ens_pick": outs["ens"].pick,
            "ens_matrix": {f"{i}-{j}": round(p, 5)
                           for (i, j), p in outs["ens"].matrix.items()},
        })
    return {
        "date": day, "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cutoff": cutoff.strftime("%Y-%m-%d %H:%M"),
        "train_rows": len(rows), "ml_trained": ml.clf is not None,
        "prior": {k: round(v, 3) for k, v in prior.items()},
        "market_score_share": MARKET_SCORE_SHARE,
        "market_score_cover": sum(1 for x in out if x["score_share"]),
        "matches": out,
    }