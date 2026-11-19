"""500.com 竞彩赛程抓取（新版自带，产物写新版目录）。"""
import datetime as dt
import json
import re
import subprocess
import time
import urllib.request

from ..config import PRO_DATA, SOURCE, UA

TR_RE = re.compile(r'<tr class="bet-tb-tr[^"]*"(.*?)</tr>', re.S)
BF_ROW_RE = re.compile(r'<tr class="bet-tb-tr[^"]*"')
ATTR_RE = re.compile(r'(data-[a-z]+)="([^"]*)"')
WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
BF_SOURCE = "https://trade.500.com/jczq/?playid=271&g=2&date=%s"


def _attrs(tag):
    return dict(ATTR_RE.findall(tag))


def http_get(url, timeout=15, tries=2):
    last = None
    for i in range(tries):
        try:
            p = subprocess.run(
                ["curl", "-sL", "--compressed", "-m", str(timeout), "-A", UA,
                 "-H", "Referer: https://www.500.com/", url],
                capture_output=True)
            body = p.stdout or b""
            if len(body) > 20000:
                return body.decode("gb18030", "ignore")
            last = RuntimeError(f"curl 返回 {p.returncode}，{len(body)} 字节")
        except Exception as e:
            last = e
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://www.500.com/",
                "Accept-Language": "zh-CN,zh;q=0.9"})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            if len(raw) > 20000:
                return raw.decode("gb18030", "ignore")
        except Exception as e:
            last = e
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    raise last


def handicap_of(title):
    t = (title or "").replace(" ", "")
    if "不让球" in t or "平手" in t:
        return 0
    m = re.match(r"(主队|客队)(受让|让)(\d+)球", t)
    if not m:
        return None
    side, kind, n = m.group(1), m.group(2), int(m.group(3))
    if side == "主队":
        return -n if kind == "让" else n
    return n if kind == "让" else -n


def parse_matches(page):
    out = []
    for tr in TR_RE.findall(page):
        a = _attrs(tr[:tr.find(">") + 1])
        no = a.get("data-matchnum", "").strip()
        if not no:
            continue
        m = re.search(r'class="team-l" title="([^"]+)"', tr)
        n = re.search(r'class="team-r" title="([^"]+)"', tr)
        if not m or not n:
            continue
        lg = re.search(r'zuqiu-\d+/"[^>]*>([^<]+)<', tr)
        lg = lg.group(1).strip() if lg else ""
        rang = re.findall(r'class="[^"]*itm-rangA(\d)"[^>]*title="([^"]*)"', tr)
        rows = re.findall(r'<div class="betbtn-row[^"]*">(.*?)</div>', tr, re.S)
        markets = []
        for idx, title in rang:
            i = int(idx) - 1
            if i >= len(rows):
                continue
            row = [None, None, None]
            for tag in re.findall(r"<p [^>]*>", rows[i]):
                pa = _attrs(tag)
                if pa.get("data-type") not in ("nspf", "spf"):
                    continue
                try:
                    row[{"3": 0, "1": 1, "0": 2}[pa.get("data-value", "")]] = float(pa.get("data-sp", "0"))
                except Exception:
                    continue
            h = handicap_of(title)
            if h is None or not all(x and x > 1.0 for x in row):
                continue
            if any(mk["h"] == h for mk in markets):
                continue
            markets.append({"h": h, "odds": row})
        odds = next((mk["odds"] for mk in markets if mk["h"] == 0), [None, None, None])
        hc = next((mk for mk in markets if mk["h"] != 0), None)
        sc = re.search(r'class="score"[^>]*>([^<]*)<', tr)
        score = sc.group(1).strip() if sc else ""
        if not re.match(r"^\d+:\d+$", score or ""):
            score = ""
        kick = ""
        if a.get("data-matchdate") and a.get("data-matchtime"):
            kick = f"{a['data-matchdate']} {a['data-matchtime']}"
        out.append({
            "no": no, "num": re.sub(r"\D", "", no) or no,
            "league": lg, "home": m.group(1), "away": n.group(1),
            "kickoff": kick, "odds": odds, "score": score,
            "fid": a.get("data-fixtureid", ""), "hc": hc,
        })
    seen, uniq = set(), []
    for x in out:
        if x["no"] in seen:
            continue
        seen.add(x["no"])
        uniq.append(x)
    return uniq


def parse_bf(page):
    """解析比分玩法页：31 档赔率（含胜/平/负其它），返回 {场次: {比分: 赔率}}。"""
    starts = [m.start() for m in BF_ROW_RE.finditer(page)]
    out = {}
    for i, s in enumerate(starts):
        chunk = page[s:starts[i + 1] if i + 1 < len(starts) else len(page)]
        no = _attrs(chunk[:chunk.find(">") + 1]).get("data-matchnum", "").strip()
        if not no:
            continue
        odds = {}
        for tag in re.findall(r'<p [^>]*data-type="bf"[^>]*>', chunk):
            pa = _attrs(tag)
            v = pa.get("data-value", "")
            try:
                sp = float(pa.get("data-sp", "0"))
            except ValueError:
                continue
            if v and sp > 1.0:
                odds[v] = sp
        if len(odds) >= 20:
            out[no] = odds
    return out


def fetch_bf(day, log=print):
    """抓某天比分赔率；失败返回空字典，不影响主流程。"""
    for i in range(2):
        try:
            page = http_get(BF_SOURCE % day, timeout=10, tries=1)
        except Exception as e:
            if i == 1:
                log(f"比分赔率抓取失败：{e}")
            continue
        got = parse_bf(page)
        if got:
            return got
        time.sleep(1)
    return {}


def fetch_day(day, attempts=4, wait=5, log=print):
    urls = [f"{SOURCE}?date={day}&_={int(time.time())}",
            f"https://trade.500.com/jczq/index.php?date={day}",
            f"{SOURCE}?date={day}"]
    last = None
    for i in range(attempts):
        try:
            page = http_get(urls[min(i, len(urls) - 1)])
        except Exception as e:
            last = e
            log(f"抓取失败 {i + 1}/{attempts}: {e}")
        else:
            rows = parse_matches(page)
            want = WD[dt.date.fromisoformat(day).weekday()]
            same = [r for r in rows if r["no"][:2] == want
                    and all(o and o > 1.0 for o in r["odds"])]
            if same:
                bf = fetch_bf(day, log=log)
                if bf:
                    for r in same:
                        if bf.get(r["no"]):
                            r["bf_odds"] = bf[r["no"]]
                    got = sum(1 for r in same if r.get("bf_odds"))
                    log(f"比分赔率：{got}/{len(same)} 场")
                log(f"抓取完成：{len(same)} 场")
                return same
            last = RuntimeError(f"页面 {len(page)} 字节，未解析到 {want} 比赛")
        if i < attempts - 1:
            time.sleep(wait * (i + 1))
    raise last or RuntimeError("抓取失败")


def save_day(day, matches, source=SOURCE):
    PRO_DATA.mkdir(parents=True, exist_ok=True)
    p = PRO_DATA / f"{day}.json"
    p.write_text(json.dumps({
        "date": day, "sealed_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source, "late": False, "matches": matches,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return p