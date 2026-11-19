"""Elo：按赛果动态更新评分，再映射成 1X2 与 λ。

评分只依赖历史赛果，不使用当前赔率，因此与 DC 的信息源相互独立。
"""
from ..config import DC_RHO, ELO_HOME
from ..mathx import fit_lambdas, score_matrix
from .base import ModelOutput


class EloModel:
    name = "elo"

    def predict(self, m, state):
        ph, pd, pa, diff = state.elo_probs(m)
        lh, la, err = fit_lambdas(ph, pd, pa, rho=DC_RHO)
        nh, na = state.played(m["home_key"]), state.played(m["away_key"])
        note = f"评分差 {diff:+.0f}（主 {state.rating(m['home_key']):.0f} / 客 {state.rating(m['away_key']):.0f}）"
        if nh < 3 or na < 3:
            note += " · 样本少"
        return ModelOutput(
            name=self.name, available=True,
            probs=(ph, pd, pa), matrix=score_matrix(lh, la, DC_RHO),
            note=note,
            meta={"elo_diff": round(diff, 1), "elo_home": round(state.rating(m["home_key"]), 1),
                  "elo_away": round(state.rating(m["away_key"]), 1),
                  "played_home": nh, "played_away": na,
                  "lambda": [round(lh, 3), round(la, 3)], "fit_err": round(err, 5)},
        )