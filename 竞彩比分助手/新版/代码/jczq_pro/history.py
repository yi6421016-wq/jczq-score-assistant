"""按时间回放的历史状态：Elo 评分、近期攻防、xG 滚动。"""
import math
from collections import defaultdict

from .config import (DRAW_BASE, DRAW_SCALE, ELO_HOME, ELO_INIT, ELO_K,
                     XG_DECAY)


class HistoryState:
    def __init__(self):
        self.elo = defaultdict(lambda: ELO_INIT)
        self.matches = defaultdict(list)   # team -> [{ts,gf,ga,home,xgf,xga}]
        self.xg_league = defaultdict(lambda: [0.0, 0.0, 0])  # league -> [xg_h, xg_a, n]

    # ---------------------------------------------------------- 查询
    def rating(self, team):
        return self.elo[team]

    def played(self, team):
        return len(self.matches[team])

    def rolling(self, team, n=5, decay=XG_DECAY):
        ms = self.matches[team][-n:]
        if not ms:
            return {"gf": None, "ga": None, "n": 0, "rest": None,
                    "xg_for": None, "xg_against": None, "xg_n": 0}
        wsum = gf = ga = xgf = xga = 0.0
        xg_w = 0.0
        for i, m in enumerate(reversed(ms)):
            w = decay ** i
            wsum += w
            gf += w * m["gf"]
            ga += w * m["ga"]
            if m.get("xgf") is not None:
                xgf += w * m["xgf"]
                xga += w * m["xga"]
                xg_w += w
        return {
            "gf": gf / wsum, "ga": ga / wsum, "n": len(ms),
            "rest": ms[-1]["rest_days"],
            "xg_for": (xgf / xg_w) if xg_w else None,
            "xg_against": (xga / xg_w) if xg_w else None,
            "xg_n": sum(1 for m in ms if m.get("xgf") is not None),
        }

    def league_xg(self, league):
        v = self.xg_league.get(league)
        if not v or v[2] == 0:
            return None
        return v[0] / v[2], v[1] / v[2]

    def global_xg(self):
        h = sum(v[0] for v in self.xg_league.values())
        a = sum(v[1] for v in self.xg_league.values())
        n = sum(v[2] for v in self.xg_league.values())
        if n == 0:
            return None
        return h / n, a / n

    # ---------------------------------------------------------- 更新
    def update(self, m):
        """在比赛结束后调用：更新 Elo 与滚动样本。"""
        gh, ga = _score(m.get("score"))
        if gh is None:
            return
        h, a = m["home_key"], m["away_key"]
        rh, ra = self.elo[h], self.elo[a]
        exp_h = 1.0 / (1.0 + 10 ** (-((rh + ELO_HOME) - ra) / 400.0))
        actual = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
        gd = abs(gh - ga)
        mult = 1.0 if gd <= 1 else (1.5 if gd == 2 else 1.75 + (gd - 3) / 8.0)
        delta = ELO_K * mult * (actual - exp_h)
        self.elo[h] = rh + delta
        self.elo[a] = ra - delta

        xg = m.get("xg")
        if xg:
            lg = m.get("league", "")
            self.xg_league[lg][0] += xg[0]
            self.xg_league[lg][1] += xg[1]
            self.xg_league[lg][2] += 2

        for team, gf, ga_, home, xf, xa in (
            (h, gh, ga, True, xg[0] if xg else None, xg[1] if xg else None),
            (a, ga, gh, False, xg[1] if xg else None, xg[0] if xg else None),
        ):
            last = self.matches[team][-1]["ts"] if self.matches[team] else None
            rest = (m["ts"].date() - last.date()).days if last else None
            self.matches[team].append({
                "ts": m["ts"], "gf": gf, "ga": ga_, "home": home,
                "xgf": xf, "xga": xa, "rest_days": rest,
            })

    # ---------------------------------------------------------- Elo 预测
    def elo_probs(self, m):
        h, a = m["home_key"], m["away_key"]
        rh, ra = self.elo[h], self.elo[a]
        diff = rh + ELO_HOME - ra
        we = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        pd = DRAW_BASE * math.exp(-(abs(diff) / DRAW_SCALE) ** 2)
        ph = max(0.001, we - pd / 2)
        pa = max(0.001, 1 - we - pd / 2)
        t = ph + pd + pa
        return ph / t, pd / t, pa / t, diff


def _score(s):
    if not s or ":" not in s:
        return None, None
    try:
        a, b = s.split(":")
        return int(a), int(b)
    except ValueError:
        return None, None