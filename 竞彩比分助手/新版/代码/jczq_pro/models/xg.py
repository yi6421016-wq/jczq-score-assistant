"""xG 模型：用射门代理 xG 估计攻防强度。

数据来自 football-data.co.uk 的射正数（xG≈a×射正+b），
没有射门数据的比赛（亚冠等）直接弃权，不拿赔率冒充 xG。
"""
from ..config import DC_RHO, XG_MIN_MATCHES
from ..mathx import fit_lambdas, score_matrix
from .base import ModelOutput, unavailable

SHRINK = 2.0            # 向联赛均值收缩的等效场次
FALLBACK_HOME = 1.50
FALLBACK_AWAY = 1.20


class XGModel:
    name = "xg"

    def predict(self, m, state):
        rh = state.rolling(m["home_key"], n=10)
        ra = state.rolling(m["away_key"], n=10)
        if rh["xg_n"] < XG_MIN_MATCHES or ra["xg_n"] < XG_MIN_MATCHES:
            return unavailable(
                self.name,
                f"射门样本不足（主 {rh['xg_n']} / 客 {ra['xg_n']} 场，需 {XG_MIN_MATCHES} 场）")

        g = state.global_xg() or (FALLBACK_HOME, FALLBACK_AWAY)
        gx = (g[0] + g[1]) / 2 or 1.3

        def shrink(x, n):
            return (x * n + gx * SHRINK) / (n + SHRINK)

        att_h = shrink(rh["xg_for"], rh["xg_n"]) / gx
        def_h = shrink(rh["xg_against"], rh["xg_n"]) / gx
        att_a = shrink(ra["xg_for"], ra["xg_n"]) / gx
        def_a = shrink(ra["xg_against"], ra["xg_n"]) / gx

        lh = max(0.2, min(4.5, g[0] * att_h * def_a))
        la = max(0.2, min(4.5, g[1] * att_a * def_h))
        # 用 xG 直接给 λ；1X2 再反解一次只为报告一致性
        matrix = score_matrix(lh, la, DC_RHO)
        from ..mathx import matrix_1x2
        ph, pd, pa = matrix_1x2(matrix)
        leagues = f"{m.get('league_home', '')}/{m.get('league_away', '')}".strip("/")
        return ModelOutput(
            name=self.name, available=True,
            probs=(ph, pd, pa), matrix=matrix,
            note=f"xG {rh['xg_for']:.2f}-{ra['xg_for']:.2f} · 样本 {rh['xg_n']}/{ra['xg_n']} 场 · 跨联赛已收缩",
            meta={"xg_home": round(rh["xg_for"], 3), "xg_away": round(ra["xg_for"], 3),
                  "xg_against_home": round(rh["xg_against"], 3),
                  "xg_against_away": round(ra["xg_against"], 3),
                  "sample_home": rh["xg_n"], "sample_away": ra["xg_n"],
                  "lambda": [round(lh, 3), round(la, 3)], "leagues": leagues},
        )