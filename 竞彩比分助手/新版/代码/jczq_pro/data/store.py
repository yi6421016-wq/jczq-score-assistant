"""统一比赛仓库：竞彩封存/回溯 + 外部联赛，按队名与日期去重合并。"""
import datetime as dt
import json
import re
from pathlib import Path

from ..config import BACKFILL_DIR, DATA_DIR, PRO_DATA
from .names import canon, display_cn

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_score(s):
    if not isinstance(s, str) or ":" not in s:
        return None
    try:
        a, b = s.split(":")
        return int(a), int(b)
    except ValueError:
        return None


def _kickoff(day, raw):
    s = (raw.get("kickoff") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", s):
        try:
            return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(20, 0))


def _from_cn(day, raw, source):
    odds = raw.get("odds")
    if not (isinstance(odds, list) and len(odds) == 3
            and all(isinstance(x, (int, float)) and x > 1.0 for x in odds)):
        odds = None
    score = raw.get("score") if _parse_score(raw.get("score")) else ""
    home, away = raw.get("home", "").strip(), raw.get("away", "").strip()
    if not home or not away:
        return None
    ts = _kickoff(day, raw)
    bf = raw.get("bf_odds")
    if not (isinstance(bf, dict) and len(bf) >= 20):
        bf = None
    return {
        "date": ts.date().isoformat(),
        "ts": ts,
        "no": raw.get("no", ""),
        "league": raw.get("league", ""),
        "home": home, "away": away,
        "home_key": canon(home), "away_key": canon(away),
        "odds": odds,
        "bf_odds": bf,
        "score": score,
        "shots": None,
        "xg": None,
        "source": source,
        "fid": raw.get("fid", ""),
    }


def load_cn_matches(log=print):
    out = []
    files = []
    if PRO_DATA.exists():
        files += [p for p in sorted(PRO_DATA.glob("*.json")) if DATE_RE.match(p.stem)]
    if BACKFILL_DIR.exists():
        files += sorted(BACKFILL_DIR.glob("*.json"))
    if DATA_DIR.exists():
        files += [p for p in sorted(DATA_DIR.glob("*.json")) if DATE_RE.match(p.stem)]
    for p in files:
        day = p.stem
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            ms = d.get("matches", [])
        except Exception as e:
            log(f"跳过损坏文件 {p.name}: {e}")
            continue
        src = "backfill" if p.parent == BACKFILL_DIR else "sealed"
        for raw in ms:
            m = _from_cn(day, raw, src)
            if m:
                out.append(m)
    return out


def load_day(day):
    for base in (PRO_DATA, DATA_DIR):
        p = base / f"{day}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return [m for m in (_from_cn(day, r, "sealed") for r in d.get("matches", [])) if m]
    return []


def _merge(a, b):
    """a 为主（竞彩），b 补射门/xG/缺失赛果。"""
    if a.get("score") or not b.get("score"):
        pass
    else:
        a["score"] = b["score"]
    for k in ("shots", "xg"):
        if a.get(k) is None and b.get(k) is not None:
            a[k] = b[k]
    if a.get("odds") is None and b.get("odds") is not None:
        a["odds"] = b["odds"]
    if a.get("bf_odds") is None and b.get("bf_odds") is not None:
        a["bf_odds"] = b["bf_odds"]
    return a


def load_unified(log=print):
    """合并外部与竞彩数据，返回按开球时间排序的列表。"""
    from .external import load_external

    ext = load_external(log=log)
    for m in ext:
        m["home_key"], m["away_key"] = canon(m["home"]), canon(m["away"])
        m["home"], m["away"] = display_cn(m["home"]), display_cn(m["away"])
    cn = load_cn_matches(log=log)
    merged, index = [], {}

    def add(m, primary=False):
        slots = index.setdefault((m["home_key"], m["away_key"]), [])
        for i in slots:
            if abs((merged[i]["ts"] - m["ts"]).total_seconds()) <= 86400 * 1.5:
                if primary:
                    _merge(m, merged[i])
                    merged[i] = m
                else:
                    _merge(merged[i], m)
                return
        slots.append(len(merged))
        merged.append(m)

    # 竞彩优先：先入库，外部数据只补射门/xG
    for m in cn:
        add(m, primary=True)
    for m in ext:
        add(m)
    merged.sort(key=lambda x: x["ts"])
    return merged


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")