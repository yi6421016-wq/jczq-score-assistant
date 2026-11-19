"""覆盖率自适应集成：只对可用模型归一化先验权重。"""
from .config import ENSEMBLE_PRIOR
from .models.base import ModelOutput


def combine(outputs, prior=None):
    prior = prior or ENSEMBLE_PRIOR
    avail = [o for o in outputs if o.available and o.probs and o.matrix]
    if not avail:
        return ModelOutput(name="ens", available=False, note="没有可用模型")
    w = {o.name: prior.get(o.name, 0.0) for o in avail}
    tw = sum(w.values())
    if tw <= 0:
        w = {o.name: 1.0 for o in avail}
        tw = float(len(avail))
    probs = [0.0, 0.0, 0.0]
    matrix = {}
    for o in avail:
        wt = w[o.name] / tw
        for i in range(3):
            probs[i] += wt * o.probs[i]
        for cell, p in o.matrix.items():
            matrix[cell] = matrix.get(cell, 0.0) + wt * p
    s = sum(matrix.values())
    matrix = {k: v / s for k, v in matrix.items()}
    picks = {o.name: o.pick for o in avail}
    spread = max(o.probs[0] for o in avail) - min(o.probs[0] for o in avail)
    return ModelOutput(
        name="ens", available=True, probs=tuple(probs), matrix=matrix,
        note=" / ".join(f"{k} {w[o.name] / tw:.0%}" for o in avail for k in [o.name]),
        meta={"weights": {o.name: round(w[o.name] / tw, 3) for o in avail},
              "picks": picks, "home_spread": round(spread, 4),
              "disagree": len(set(picks.values())) > 1},
    )