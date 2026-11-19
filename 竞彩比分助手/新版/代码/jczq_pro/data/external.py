"""football-data.co.uk 历史赛果/射门/赔率导入。

只做“外部基准数据”：9 大联赛，两个赛季。用于
  - Elo 长历史
  - 射门代理 xG
  - ML 训练样本
不覆盖亚冠/欧冠正赛。
"""
import csv
import io
import datetime as dt
from pathlib import Path

import requests

from ..config import EXTERNAL_DIR, UA

LEAGUES = {
    "E0": "英超", "D1": "德甲", "SP1": "西甲", "I1": "意甲", "F1": "法甲",
    "N1": "荷甲", "T1": "土超", "B1": "比甲", "P1": "葡超",
}
SEASONS = ("2627", "2526")
BASE = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"

# 2025-26 赛季标定：xG ≈ 0.287×射正 + 0.155（主），0.277×射正 + 0.140（客）
XG_H_A, XG_H_B = 0.287, 0.155
XG_A_A, XG_A_B = 0.277, 0.140


def _path(season, league):
    return EXTERNAL_DIR / f"{season}_{league}.csv"


def ensure_csvs(seasons=SEASONS, force=False, log=print):
    """缺文件才下载；返回成功可用的文件数。"""
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for season in seasons:
        for league in LEAGUES:
            p = _path(season, league)
            if p.exists() and p.stat().st_size > 10000 and not force:
                ok += 1
                continue
            try:
                r = requests.get(BASE.format(season=season, league=league),
                                 headers={"User-Agent": UA}, timeout=25)
                if r.status_code == 200 and len(r.content) > 10000:
                    p.write_bytes(r.content)
                    ok += 1
                else:
                    log(f"外部数据缺失：{season}/{league} HTTP {r.status_code}")
            except Exception as e:
                log(f"外部数据下载失败：{season}/{league} {e}")
    return ok


def _num(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            try:
                x = float(v)
                if x > 0:
                    return x
            except ValueError:
                continue
    return None


def _int(row, key):
    v = row.get(key)
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def load_external(log=print):
    """返回统一结构的比赛列表（英文队名、UTC 无关的当地日期）。"""
    out = []
    for season in SEASONS:
        for league, lg_name in LEAGUES.items():
            p = _path(season, league)
            if not p.exists():
                continue
            raw = p.read_bytes().decode("utf-8-sig", "ignore")
            for row in csv.DictReader(io.StringIO(raw)):
                d = _date(row.get("Date"))
                home, away = row.get("HomeTeam"), row.get("AwayTeam")
                fh, fa = _int(row, "FTHG"), _int(row, "FTAG")
                if not d or not home or not away or fh is None or fa is None:
                    continue
                hst, ast = _int(row, "HST"), _int(row, "AST")
                hs, as_ = _int(row, "HS"), _int(row, "AS")
                xg = None
                if hst is not None and ast is not None:
                    xg = (XG_H_A * hst + XG_H_B, XG_A_A * ast + XG_A_B)
                odds = [_num(row, "AvgH", "B365H", "PSH"),
                        _num(row, "AvgD", "B365D", "PSD"),
                        _num(row, "AvgA", "B365A", "PSA")]
                out.append({
                    "date": d.isoformat(),
                    "ts": dt.datetime.combine(d, dt.time(20, 0)),
                    "league": lg_name,
                    "home": home, "away": away, "score": f"{fh}:{fa}",
                    "odds": odds if all(odds) else None,
                    "shots": (hs, hst, as_, ast) if None not in (hs, hst, as_, ast) else None,
                    "xg": xg, "source": "ext",
                })
    return out