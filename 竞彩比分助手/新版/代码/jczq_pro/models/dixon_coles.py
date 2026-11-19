"""Dixon-Coles：赔率去水后反解 λ，低比分格用 ρ 修正。"""
from ..config import DC_RHO
from ..mathx import fit_lambdas, score_matrix, shin
from .base import ModelOutput, unavailable


class DixonColes:
    name = "dc"

    def __init__(self, rho=DC_RHO):
        self.rho = rho

    def predict(self, m):
        odds = m.get("odds")
        if not odds or len(odds) != 3:
            return unavailable(self.name, "无胜平负赔率")
        p, _ = shin(odds)
        lh, la, err = fit_lambdas(*p, rho=self.rho)
        matrix = score_matrix(lh, la, self.rho)
        return ModelOutput(
            name=self.name, available=True,
            probs=tuple(p), matrix=matrix,
            note=f"λ {lh:.2f}-{la:.2f} · ρ={self.rho:+.2f} · 拟合误差 {err:.4f}",
            meta={"lambda": [round(lh, 3), round(la, 3)], "rho": self.rho,
                  "fit_err": round(err, 5), "return_rate": round(1 / sum(1 / o for o in odds), 4)},
        )