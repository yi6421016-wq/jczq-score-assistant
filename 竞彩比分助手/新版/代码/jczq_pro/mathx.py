"""概率与比分矩阵的基础数学。所有模型统一输出 10×10 比分矩阵。"""
import math
from functools import lru_cache

import numpy as np
from scipy.optimize import minimize

from .config import MAX_GOALS


def pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def shin(odds):
    """Shin 去水，返回 (概率, 倒数和)。"""
    q = [1.0 / float(o) for o in odds]
    s = sum(q)
    if s <= 1.0000001:
        return [x / s for x in q], s

    def probs(z):
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / s) - z)
                / (2 * (1 - z)) for x in q]

    lo, hi = 0.0, 0.45
    for _ in range(50):
        mid = (lo + hi) / 2
        if sum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2)
    t = sum(p)
    return [x / t for x in p], s


def score_matrix(lh: float, la: float, rho: float = 0.0, n: int = MAX_GOALS):
    """Dixon-Coles 低比分修正后的比分矩阵 {(i,j): p}。"""
    vh = np.array([pois(i, lh) for i in range(n)])
    va = np.array([pois(j, la) for j in range(n)])
    m = np.outer(vh, va)
    if rho:
        m[0, 0] *= max(1e-6, 1 - lh * la * rho)
        m[0, 1] *= max(1e-6, 1 + lh * rho)
        m[1, 0] *= max(1e-6, 1 + la * rho)
        m[1, 1] *= max(1e-6, 1 - rho)
    m /= m.sum()
    return {(i, j): float(m[i, j]) for i in range(n) for j in range(n)}


def matrix_1x2(m):
    h = d = a = 0.0
    for (i, j), p in m.items():
        if i > j:
            h += p
        elif i == j:
            d += p
        else:
            a += p
    return h, d, a


def fit_lambdas(p1: float, px: float, p2: float, rho: float = 0.0):
    """把 1X2 概率反解成 (λ主, λ客)。多起点 L-BFGS-B，返回 (err, lh, la)。"""
    target = np.array([p1, px, p2])

    def obj(x):
        lh, la = x
        m = score_matrix(lh, la, rho, n=8)
        p = np.array(matrix_1x2(m))
        return float(np.sum((p - target) ** 2))

    best = (1e9, 1.3, 1.1)
    for lh0, la0 in ((1.3, 1.1), (0.8, 1.6), (1.8, 0.8), (1.0, 1.0)):
        r = minimize(obj, [lh0, la0], method="L-BFGS-B",
                     bounds=[(0.05, 5.0), (0.05, 5.0)])
        if r.fun < best[0]:
            best = (float(r.fun), float(r.x[0]), float(r.x[1]))
    return best[1], best[2], best[0]


def total_goals_dist(m):
    """0..7 球，7 表示 7+。"""
    out = [0.0] * 8
    for (i, j), p in m.items():
        out[min(i + j, 7)] += p
    return out


def top_scores(m, k=5):
    return sorted(((f"{i}-{j}", p) for (i, j), p in m.items()),
                  key=lambda x: -x[1])[:k]


def score_label(m):
    i, j = max(m, key=m.get)
    return f"{i}-{j}"


def parse_score(s):
    if not s or ":" not in s:
        return None
    try:
        a, b = s.split(":")
        return int(a), int(b)
    except ValueError:
        return None


def result_index(score):
    """0 主胜 / 1 平 / 2 客胜。"""
    g = parse_score(score)
    if g is None:
        return None
    return 0 if g[0] > g[1] else (1 if g[0] == g[1] else 2)


def market_probs(odds):
    p, _ = shin(odds)
    return tuple(p)


TAIL_KIND = {"胜其它": 0, "平其它": 1, "负其它": 2}


def bf_matrix(odds, base, n: int = MAX_GOALS):
    """竞彩比分 31 档赔率 → 比分矩阵（Shin 去水）。

    “胜/平/负其它”按 base 矩阵里同方向、未单列各格的形状摊开。
    赔率不足 20 档返回 None。
    """
    if not isinstance(odds, dict):
        return None
    labels = [k for k, v in odds.items()
              if isinstance(v, (int, float)) and v > 1.0]
    if len(labels) < 20:
        return None
    probs, _ = shin([float(odds[k]) for k in labels])
    named, tails = {}, {}
    for k, pr in zip(labels, probs):
        if ":" in k:
            try:
                i, j = (int(x) for x in k.split(":"))
            except ValueError:
                continue
            if i < n and j < n:
                named[(i, j)] = named.get((i, j), 0.0) + pr
        elif k in TAIL_KIND:
            tails[k] = pr
    mx = {c: named.get(c, 0.0) for c in base}
    for k, pr in tails.items():
        kind = TAIL_KIND[k]
        if kind == 0:
            cond = lambda i, j: i > j
        elif kind == 1:
            cond = lambda i, j: i == j
        else:
            cond = lambda i, j: i < j
        cells = [c for c in mx if c not in named and cond(c[0], c[1])]
        w = sum(base.get(c, 0.0) for c in cells)
        if w <= 0:
            continue
        for c in cells:
            mx[c] += pr * base.get(c, 0.0) / w
    s = sum(mx.values())
    if s <= 0:
        return None
    return {c: v / s for c, v in mx.items()}


def blend_matrix(a, b, w: float):
    """混合两张同格比分矩阵，w 为 b 的权重。"""
    w = min(max(w, 0.0), 1.0)
    out = {c: (1 - w) * a.get(c, 0.0) + w * b.get(c, 0.0) for c in a}
    s = sum(out.values())
    if s <= 0:
        return dict(a)
    return {c: v / s for c, v in out.items()}


@lru_cache(maxsize=4096)
def _pois_cache(lam: float, k: int) -> float:
    return pois(k, lam)