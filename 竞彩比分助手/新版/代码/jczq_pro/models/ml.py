"""梯度提升模型：1X2 分类，再把概率反解成比分矩阵。

特征只用赛前可得信息：赔率、Elo、近期攻防、休息天数、xG 滚动。
训练样本按时间走步，禁止使用未来比赛。
"""
import math

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from ..config import (DC_RHO, ML_L2, ML_LEARNING_RATE, ML_MAX_DEPTH,
                      ML_MAX_ITER, ML_MIN_LEAF)
from ..mathx import fit_lambdas, score_matrix, shin
from .base import ModelOutput, unavailable

FEATURES = [
    "p1", "px", "p2", "log_ratio",
    "elo_diff", "elo_h", "elo_a",
    "gf_h", "ga_h", "gf_a", "ga_a",
    "rest_h", "rest_a", "n_h", "n_a",
    "xgf_h", "xga_h", "xgf_a", "xga_a",
    "league",
]
CAT_IDX = [FEATURES.index("league")]
NAN = float("nan")


class LeagueEncoder:
    def __init__(self, leagues):
        self.map = {lg: i + 1 for i, lg in enumerate(sorted(set(leagues)))}

    def get(self, lg):
        return self.map.get(lg, 0)


def _num(x):
    return NAN if x is None else float(x)


def feature_row(m, state, enc: LeagueEncoder):
    odds = m.get("odds")
    if odds and len(odds) == 3:
        p1, px, p2 = shin(odds)[0]
        logr = math.log(max(p1, 1e-6) / max(p2, 1e-6))
    else:
        p1 = px = p2 = logr = NAN
    rh, ra = state.rolling(m["home_key"]), state.rolling(m["away_key"])
    diff = state.rating(m["home_key"]) + 60 - state.rating(m["away_key"])
    return [
        p1, px, p2, logr,
        diff / 400.0,
        (state.rating(m["home_key"]) - 1500) / 400.0,
        (state.rating(m["away_key"]) - 1500) / 400.0,
        _num(rh["gf"]), _num(rh["ga"]), _num(ra["gf"]), _num(ra["ga"]),
        (min(rh["rest"], 30) / 30.0) if rh["rest"] is not None else NAN,
        (min(ra["rest"], 30) / 30.0) if ra["rest"] is not None else NAN,
        min(rh["n"], 20) / 20.0, min(ra["n"], 20) / 20.0,
        _num(rh["xg_for"]), _num(rh["xg_against"]),
        _num(ra["xg_for"]), _num(ra["xg_against"]),
        float(enc.get(m.get("league", ""))),
    ]


class MLModel:
    name = "ml"

    def __init__(self, encoder: LeagueEncoder):
        self.enc = encoder
        self.clf = None
        self.n_train = 0

    def train(self, rows):
        """rows: [(feature_vector, label)]，只包含当前时点之前的比赛。"""
        if len(rows) < 2:
            return False
        X = np.asarray([r[0] for r in rows], dtype=float)
        y = np.asarray([r[1] for r in rows], dtype=int)
        if len(set(y.tolist())) < 2:
            return False
        self.clf = HistGradientBoostingClassifier(
            max_iter=ML_MAX_ITER, learning_rate=ML_LEARNING_RATE,
            max_depth=ML_MAX_DEPTH, l2_regularization=ML_L2,
            min_samples_leaf=ML_MIN_LEAF,
            categorical_features=CAT_IDX,
            random_state=7,
        )
        self.clf.fit(X, y)
        self.n_train = len(rows)
        return True

    def predict(self, m, state):
        if self.clf is None:
            return unavailable(self.name, "训练样本不足")
        x = np.asarray([feature_row(m, state, self.enc)], dtype=float)
        pr = self.clf.predict_proba(x)[0]
        probs = [0.0, 0.0, 0.0]
        for cls, p in zip(self.clf.classes_, pr):
            probs[int(cls)] = float(p)
        s = sum(probs)
        if s <= 0:
            return unavailable(self.name, "输出异常")
        probs = [p / s for p in probs]
        lh, la, err = fit_lambdas(*probs, rho=DC_RHO)
        return ModelOutput(
            name=self.name, available=True,
            probs=tuple(probs), matrix=score_matrix(lh, la, DC_RHO),
            note=f"样本 {self.n_train} 场 · 深度 {ML_MAX_DEPTH}",
            meta={"n_train": self.n_train, "lambda": [round(lh, 3), round(la, 3)],
                  "fit_err": round(err, 5)},
        )