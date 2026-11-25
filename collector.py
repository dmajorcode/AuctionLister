#!/usr/bin/env python3
"""경매마당(madangs.com) 신규 물건 수집기.

조건: 서울 / 주거용건물 > 아파트 / 지분경매 제외 / 감정가 10억 이상
매일 실행하면
  1) 처음 보는 물건(m_code 기준)을 '그날 추가된 물건'으로 기록하고
  2) 매각기일이 지난 물건은 경매마당 상세 페이지에서 매각 결과(매각액)를 확인하고
  3) MOLIT_API_KEY 가 있으면 국토부 실거래가로 같은 평형 시세를 계산한 뒤
report.html 을 다시 만듭니다. 표준 라이브러리만 사용합니다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "auction.db"          # 로컬 실행용 SQLite
STORE_PATH = ROOT / "data" / "store.json"       # GitHub 실행용 (git 으로 관리되는 수집 데이터)
STATE_PATH = ROOT / "data" / "state.json"       # GitHub 실행용 (화면에서 저장하는 확인함/★ 표시)
KST = dt.timezone(dt.timedelta(hours=9))
STATS_PATH = ROOT / "data" / "stats.json"       # 개요 페이지용 서울 아파트 낙찰 기록 (로컬·GitHub 공용)
FIREBASE_PATH = ROOT / "data" / "firebase.json"  # {"databaseURL": "https://....firebasedatabase.app"} (비밀 아님)
STATS_SINCE = "2026-01-01"
STATS_REFRESH_DAYS = 45                          # 첫 수집 뒤에는 최근 며칠만 다시 읽음

SITE = "https://madangs.com"
FILTERS = {
    "use_type": "2000",          # 주거용건물 > 아파트
    "addr": "11",                # 서울 전체
    "share": "1",                # 지분필터: 지분 제외
    "eval_p_min": "1000000000",  # 감정가 10억 이상
}
MIN_EVAL_PRICE = 1_000_000_000
MAX_LOW_PRICE = 2_000_000_000   # 최저가 20억 이하 (넘는 물건은 '가격 대기'로 추적, 유찰로 내려오면 신규로 진입)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
DELAY = 1.5                 # 경매마당 요청 사이 간격(초)
RESULT_TRACK_DAYS = 45      # 목록에서 빠진 뒤 매각 결과를 확인하는 기간
RESULT_SCAN_DAYS = 90       # 매각결과 페이지에서 매번 다시 훑는 기간 (60건당 요청 1회, 보통 5회)
MARKET_MONTHS = 12          # 실거래 조회 기간(개월)
SAME_AREA_TOL = 2.0
TENANT_TAGS = ("선순위임차인", "임차권등기")  # 리포트·알림에서 기본으로 숨기는 태그         # 같은 평형으로 보는 전용면적 차이(㎡)

LOCAL_URL = "http://localhost:8765"
PUBLIC_URL = "https://dmajorcode.github.io/AuctionLister/"  # 알림에 넣는 공개 페이지

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', re.S)


# ---------------------------------------------------------------- 경매마당 ---
def http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def rsc_payload(html: str) -> str:
    return "".join(json.loads(m.group(1)) for m in PUSH_RE.finditer(html))


def extract_obj(payload: str, key: str):
    i = payload.find(f'"{key}":')
    if i < 0:
        return None
    obj, _ = json.JSONDecoder().raw_decode(payload, i + len(key) + 3)
    return obj


def fetch_all() -> list[dict]:
    items: list[dict] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        params = dict(FILTERS, **({"page": str(page)} if page > 1 else {}))
        res = extract_obj(rsc_payload(http_get(f"{SITE}/search?{urllib.parse.urlencode(params)}")), "initialResult")
        if res is None:
            raise RuntimeError("initialResult 를 찾지 못했습니다 (사이트 구조 변경 가능성)")
        items.extend(res.get("items") or [])
        total_pages = int((res.get("pagination") or {}).get("total_pages") or 1)
        page += 1
        if page <= total_pages:
            time.sleep(DELAY)
    return items


def fetch_results(today: str) -> list[dict]:
    """매각결과 페이지(/search/result)에서 최근 매각기일의 물건. 주소 필터가 안 먹어서 서울은 is_wanted 로 거른다."""
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=RESULT_SCAN_DAYS)).isoformat()
    params = {"use_type": FILTERS["use_type"], "eval_p_min": FILTERS["eval_p_min"], "date": start, "date_end": today}
    items: list[dict] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        q = urllib.parse.urlencode(dict(params, page=page))
        res = extract_obj(rsc_payload(http_get(f"{SITE}/search/result?{q}")), "initialResult")
        if res is None:
            raise RuntimeError("매각결과 페이지에서 initialResult 를 찾지 못했습니다")
        items.extend(res.get("items") or [])
        total_pages = int((res.get("pagination") or {}).get("total_pages") or 1)
        page += 1
        if page <= total_pages:
            time.sleep(DELAY)
    return items


def update_stats(today: str) -> None:
    """개요(낙찰가율)용: 서울 아파트 전체(가격 제한 없음, 지분 제외)의 낙찰 기록 {m_code: [매각일, 감정가, 낙찰가]}.
    매각결과 목록을 '낙찰(status=40)'만 읽는다. 주소 필터가 없어 전국을 읽고 서울만 남긴다."""
    old = json.loads(STATS_PATH.read_text(encoding="utf-8")) if STATS_PATH.exists() else {}
    sales = old.get("sales", {})
    start = STATS_SINCE
    if old.get("updated"):  # 마지막 갱신일에서 STATS_REFRESH_DAYS 앞부터
        back = dt.date.fromisoformat(old["updated"]) - dt.timedelta(days=STATS_REFRESH_DAYS)
        start = max(STATS_SINCE, back.isoformat())
    page, total_pages = 1, 1
    while page <= total_pages:
        q = urllib.parse.urlencode({"use_type": FILTERS["use_type"], "status": "40", "date": start,
                                    "date_end": today, "page": page})
        for attempt in range(3):
            try:
                res = extract_obj(rsc_payload(http_get(f"{SITE}/search/result?{q}")), "initialResult")
                if res is None:
                    raise RuntimeError("initialResult 없음")
                break
            except Exception:
                if attempt == 2:
                    raise  # 일부만 읽은 상태는 저장하지 않음
                time.sleep(10)
        for it in res.get("items") or []:
            day, ev, price = it.get("m_bid_date") or "", it.get("m_evaluate_price"), it.get("sold_price")
            if (it.get("jibun_region_1depth_name") == "서울" and str(it.get("use_type_code")) == "2000"
                    and it.get("sold_status") and ev and price and STATS_SINCE <= day <= today
                    and not any("지분" in r for r in it.get("special_right") or [])):
                sales[it["m_code"]] = [day, ev, price]
        total_pages = int((res.get("pagination") or {}).get("total_pages") or 1)
        if page % 50 == 0:
            print(f"  통계 {page}/{total_pages}쪽, 서울 낙찰 {len(sales)}건", flush=True)
        page += 1
        time.sleep(DELAY)
    rows = ",\n".join(f'{json.dumps(k)}: {json.dumps(v)}' for k, v in sorted(sales.items()))
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(f'{{"since": "{STATS_SINCE}", "updated": "{today}", "sales": {{\n{rows}\n}}}}\n', encoding="utf-8")
    print(f"  통계: {start}~{today} 확인, 서울 아파트 낙찰 누적 {len(sales)}건")


def save_results(con: sqlite3.Connection, items: list[dict], today: str) -> int:
    """낙찰가가 있는 물건을 저장. 추적하던 물건이거나 최저가 20억↓ 였던 물건만. 새로 기록한 건수를 돌려준다."""
    added = 0
    for it in items:
        # 매각 / 허가결정 / 납부 / 배당종결 등 낙찰된 상태는 모두 sold_status=True
        if not (it.get("sold_status") and it.get("sold_price") and it.get("m_bid_date") and is_wanted(it)):
            continue
        s = slim(it)
        res = {"date": s["m_bid_date"], "state": "매각", "sold_price": it["sold_price"],
               "status": (it.get("m_state") or {}).get("state")}
        row = con.execute("SELECT qualified_on, starred, result FROM items WHERE m_code=?", (s["m_code"],)).fetchone()
        if row is None:
            if not qualifies(s):
                continue
            con.execute(
                "INSERT INTO items(m_code, first_seen, last_seen, baseline, data, qualified_on, entry_reason, result) "
                "VALUES (?,?,?,0,?,?,'result',?)",
                (s["m_code"], today, s["m_bid_date"], json.dumps(s, ensure_ascii=False), s["m_bid_date"],
                 json.dumps(res, ensure_ascii=False)))
            added += 1
            continue
        qualified_on, starred, old = row
        if not (qualified_on or starred or qualifies(s)):
            continue
        if not old or json.loads(old).get("state") != "매각":
            added += 1
        con.execute(
            "UPDATE items SET data=?, result=?, qualified_on=COALESCE(qualified_on, ?), "
            "entry_reason=COALESCE(entry_reason, 'result') WHERE m_code=?",
            (json.dumps(s, ensure_ascii=False), json.dumps(res, ensure_ascii=False), s["m_bid_date"], s["m_code"]))
    con.commit()
    return added


def is_wanted(it: dict) -> bool:
    """서버 필터에 더해 한 번 더 확인 (지분/감정가/지역/용도)."""
    rights = it.get("special_right") or []
    if any("지분" in r for r in rights):
        return False
    if (it.get("m_evaluate_price") or 0) < MIN_EVAL_PRICE:
        return False
    if it.get("jibun_region_1depth_name") not in (None, "서울") and not str(it.get("addr", "")).startswith("서울"):
        return False
    return str(it.get("use_type_code")) == "2000"


def slim(it: dict) -> dict:
    keys = [
        "m_code", "case_num", "bubwon", "addr", "use_type", "2depth_addr",
        "m_evaluate_price", "eval_price", "low_price", "low_price_kor", "eval_per",
        "state", "m_bid_date", "m_bid_uchal", "special_right", "areas",
        "m_build_area", "jibun_addr_code", "jibun_region_2depth_name", "jibun_region_3depth_name", "img_url", "case_url",
    ]
    return {k: it.get(k) for k in keys}


def parse_won(s: str | None) -> int | None:
    """'59억5900만원' -> 5959000000"""
    if not s:
        return None
    m = re.fullmatch(r"\s*(?:(\d+)억)?\s*(?:([\d,]+)만)?\s*(?:([\d,]+))?\s*원?\s*", s)
    if not m or not any(m.groups()):
        return None
    eok, man, won = (int((g or "0").replace(",", "")) for g in m.groups())
    return eok * 100_000_000 + man * 10_000 + won


def fetch_detail(case_url: str) -> dict:
    """상세 페이지: 기일 내역(매각액), 단지명, 단지 평균 실거래(평형 무관)."""
    p = rsc_payload(http_get(SITE + case_url))
    bid_items = (extract_obj(p, "bidDate") or {}).get("items") or []
    molit = extract_obj(p, "molitAverage") or {}
    danji = re.search(r'"danji_name":"([^"]*)"', p)
    naver = re.search(r'"naver_apt_url":"([^"]*)"', p)
    history = []
    for b in bid_items:
        d = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", b.get("date") or "")
        history.append({
            "date": "-".join(d.groups()) if d else b.get("date"),
            "state": b.get("state"),
            "min_price": parse_won(b.get("price")),
            "sold_price": parse_won(b.get("sold_price")),
        })
    return {
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "danji_name": danji.group(1) if danji else None,
        "naver_apt_url": naver.group(1) if naver else None,
        "danji_trade_avg": molit.get("sell_price_avg"),
        "history": history,
    }


def sale_result(item: dict, detail: dict) -> dict | None:
    """이번 매각기일(또는 그 이후)의 결과. 매각이면 매각액 포함."""
    bid_date = item.get("m_bid_date") or ""
    past = [h for h in detail.get("history", []) if (h.get("date") or "") >= bid_date and h.get("state") not in (None, "", "진행", "매각기일")]
    if not past:
        return None
    sold = [h for h in past if h.get("state") == "매각" and h.get("sold_price")]
    h = sold[0] if sold else sorted(past, key=lambda x: x["date"])[0]
    return {"date": h["date"], "state": h["state"], "sold_price": h.get("sold_price")}


# ------------------------------------------------------ 국토부 실거래가 API ---
MOLIT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"


class Molit:
    """시군구×월 단위로 실거래를 받아 캐시 (한 번 실행 중에만)."""

    def __init__(self, key: str):
        self.key = key
        self.cache: dict[tuple[str, str], list[dict]] = {}

    def month(self, lawd: str, ym: str) -> list[dict]:
        if (lawd, ym) in self.cache:
            return self.cache[(lawd, ym)]
        rows, page = [], 1
        while True:
            q = urllib.parse.urlencode({"LAWD_CD": lawd, "DEAL_YMD": ym, "pageNo": page, "numOfRows": 1000})
            key = self.key if "%" in self.key else urllib.parse.quote(self.key, safe="")
            root = ET.fromstring(http_get(f"{MOLIT_URL}?serviceKey={key}&{q}"))
            code = root.findtext(".//resultCode")
            if code not in ("00", "000"):
                raise RuntimeError(f"국토부 API 오류 {code}: {root.findtext('.//resultMsg')}")
            for el in root.iter("item"):
                rows.append({c.tag: (c.text or "").strip() for c in el})
            total = int(root.findtext(".//totalCount") or 0)
            if page * 1000 >= total:
                break
            page += 1
        self.cache[(lawd, ym)] = rows
        return rows


def months_back(n: int, today: dt.date) -> list[str]:
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def same_complex(row: dict, item: dict) -> bool:
    """같은 법정동 + 같은 지번. 지번만 보면 다른 동의 같은 번지가 섞인다.
    jibun_addr_code = 법정동코드(10) + 산여부(1) + 본번(4) + 부번(4)"""
    code = item["jibun_addr_code"]
    bon, bu = int(code[11:15]), int(code[15:19])
    jb = f"{bon}" + (f"-{bu}" if bu else "")
    dong = (item.get("jibun_region_3depth_name") or "").replace(" ", "")
    return bool(dong) and (row.get("umdNm") or "").replace(" ", "") == dong and (row.get("jibun") or "") == jb


def market_price(molit: Molit, item: dict, today: dt.date) -> dict | None:
    code, area = item.get("jibun_addr_code") or "", item.get("m_build_area")
    if len(code) < 19 or not area:
        return None
    deals = []
    for ym in months_back(MARKET_MONTHS, today):
        for r in molit.month(code[:5], ym):
            if r.get("cdealType") or not same_complex(r, item):  # 해제된 거래 제외
                continue
            try:
                ar = float(r.get("excluUseAr") or 0)
                amt = int((r.get("dealAmount") or "0").replace(",", "")) * 10_000
            except ValueError:
                continue
            if abs(ar - float(area)) <= SAME_AREA_TOL:
                deals.append({
                    "date": f"{r['dealYear']}-{int(r['dealMonth']):02d}-{int(r['dealDay']):02d}",
                    "price": amt, "area": ar, "floor": r.get("floor"), "apt": r.get("aptNm"),
                })
    deals.sort(key=lambda d: d["date"], reverse=True)
    if not deals:
        return {"updated": today.isoformat(), "count": 0, "months": MARKET_MONTHS}
    recent = [d["price"] for d in deals[:5]]
    return {
        "updated": today.isoformat(), "months": MARKET_MONTHS, "count": len(deals),
        "latest": deals[0], "recent_avg": int(statistics.mean(recent)), "recent_n": len(recent),
        "deals": deals[:10],
    }


# ---------------------------------------------------------------- 저장소 ---
# items 컬럼
#   first_seen    처음 목록에서 본 날 (최저가 조건과 무관하게 감정가 10억↑이면 추적)
#   qualified_on  최저가 20억 이하 조건까지 처음 만족한 날 (NULL = 아직 가격 대기)
#   entry_reason  'new'(새 물건) | 'price_drop'(유찰 등으로 최저가가 내려와 조건 진입)
#   baseline      최초 수집 때 이미 조건을 만족하던 물건 (신규로 치지 않음)
#   viewed_at     사용자가 눌러 본 시각 (NULL 이면 신규 탭에 계속 남음)
#   starred       집중 모니터링 표시
#   prev_low_price 직전 최저가 (가격이 바뀌면 기록)
def db(memory: bool = False) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(":memory:" if memory else DB_PATH, timeout=30)
    con.execute(
        """CREATE TABLE IF NOT EXISTS items (
            m_code TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            baseline INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL)"""
    )
    cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    for c, typ in (("detail", "TEXT"), ("result", "TEXT"), ("market", "TEXT"), ("qualified_on", "TEXT"),
                   ("entry_reason", "TEXT"), ("viewed_at", "TEXT"), ("starred", "INTEGER DEFAULT 0"),
                   ("prev_low_price", "INTEGER"), ("hidden", "INTEGER DEFAULT 0")):
        if c not in cols:
            con.execute(f"ALTER TABLE items ADD COLUMN {c} {typ}")
    if "qualified_on" not in cols:
        # 기존 DB 이관: 최초 수집 물건 중 지금 최저가 조건을 만족하는 것은 '이미 알고 있던 물건'
        con.execute(
            "UPDATE items SET qualified_on=first_seen, entry_reason='new' "
            "WHERE baseline=1 AND json_extract(data,'$.low_price') <= ?", (MAX_LOW_PRICE,))
        con.execute("UPDATE items SET baseline=0 WHERE qualified_on IS NULL")
    con.execute("CREATE TABLE IF NOT EXISTS runs (run_at TEXT PRIMARY KEY, run_date TEXT, total INTEGER, new_count INTEGER)")
    con.commit()
    return con


def qualifies(s: dict) -> bool:
    return 0 < (s.get("low_price") or 0) <= MAX_LOW_PRICE


def save(con: sqlite3.Connection, items: list[dict], today: str) -> list[dict]:
    """오늘 목록을 저장하고, 오늘 처음 조건(최저가 20억↓)을 만족하게 된 물건을 돌려준다."""
    first_run = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    entered = []
    for it in items:
        s = slim(it)
        data = json.dumps(s, ensure_ascii=False)
        row = con.execute("SELECT qualified_on, data FROM items WHERE m_code=?", (s["m_code"],)).fetchone()
        if row is None:
            q = qualifies(s)
            con.execute(
                "INSERT INTO items(m_code, first_seen, last_seen, baseline, data, qualified_on, entry_reason) VALUES (?,?,?,?,?,?,?)",
                (s["m_code"], today, today, 1 if (first_run and q) else 0, data,
                 today if q else None, "new" if q else None),
            )
            if q and not first_run:
                entered.append(dict(s, entry_reason="new"))
            continue
        qualified_on, old = row
        old_low = json.loads(old).get("low_price")
        con.execute("UPDATE items SET last_seen=?, data=? WHERE m_code=?", (today, data, s["m_code"]))
        if old_low and s.get("low_price") and old_low != s["low_price"]:
            con.execute("UPDATE items SET prev_low_price=? WHERE m_code=?", (old_low, s["m_code"]))
        if qualified_on is None and qualifies(s):
            # 유찰 등으로 최저가가 20억 이하로 내려와 조건에 새로 들어온 물건
            con.execute(
                "UPDATE items SET qualified_on=?, entry_reason='price_drop', baseline=0, viewed_at=NULL WHERE m_code=?",
                (today, s["m_code"]))
            entered.append(dict(s, entry_reason="price_drop", prev_low_price=old_low))
    qualified_today = sum(1 for it in items if qualifies(slim(it)))
    con.execute(
        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?)",
        (dt.datetime.now().isoformat(timespec="seconds"), today, qualified_today, len(entered)),
    )
    con.commit()
    return entered


def update_details(con: sqlite3.Connection, today: str) -> None:
    """조건을 만족한 적 있는 물건 중 상세 정보가 없거나, 매각기일이 지났는데 결과가 아직 없는 것만 조회."""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=RESULT_TRACK_DAYS)).isoformat()
    rows = con.execute(
        "SELECT m_code, last_seen, data, detail, result, qualified_on, starred FROM items").fetchall()
    todo = []
    for m_code, last_seen, data, detail, result, qualified_on, starred in rows:
        d = json.loads(data)
        res = json.loads(result) if result else None
        bid_passed = (d.get("m_bid_date") or "9999") <= today  # 당일 결과도 확인
        dropped = last_seen < today  # 매각기일 전에 목록에서 빠짐 (변경/취하 등)
        tracked = qualified_on or starred
        needs_result = bool(tracked) and (bid_passed or dropped) and not (res and res.get("state") == "매각") and last_seen >= cutoff
        if (detail is None and (last_seen == today or result)) or needs_result:
            todo.append((m_code, d, needs_result))
    for n, (m_code, d, needs_result) in enumerate(todo):
        try:
            det = fetch_detail(d["case_url"])
        except Exception as e:
            print(f"  상세 조회 실패 {d['case_num']}: {e}", file=sys.stderr)
            continue
        res = sale_result(d, det) if needs_result else None
        con.execute(
            "UPDATE items SET detail=?, result=COALESCE(?, result) WHERE m_code=?",
            (json.dumps(det, ensure_ascii=False), json.dumps(res, ensure_ascii=False) if res else None, m_code),
        )
        con.commit()
        if n < len(todo) - 1:
            time.sleep(DELAY)
    print(f"  상세 페이지 조회 {len(todo)}건")


def update_market(con: sqlite3.Connection, key: str, today: str) -> None:
    molit = Molit(key)
    rows = con.execute(
        "SELECT m_code, data FROM items WHERE last_seen=? OR (result IS NOT NULL AND market IS NULL)", (today,)).fetchall()
    ok = 0
    for m_code, data in rows:
        try:
            mk = market_price(molit, json.loads(data), dt.date.fromisoformat(today))
        except Exception as e:
            print(f"  실거래 조회 실패: {e}", file=sys.stderr)
            return
        con.execute("UPDATE items SET market=? WHERE m_code=?", (json.dumps(mk, ensure_ascii=False), m_code))
        ok += 1
    con.commit()
    print(f"  실거래 시세 갱신 {ok}건 (API 호출 {len(molit.cache)}회분)")


def page_data(con: sqlite3.Connection) -> dict:
    """웹 화면에 넘길 데이터. 기준일은 마지막 수집일."""
    last = con.execute("SELECT run_at, run_date FROM runs ORDER BY run_at DESC LIMIT 1").fetchone()
    ref = last[1] if last else dt.date.today().isoformat()
    first = con.execute("SELECT MIN(run_date) FROM runs").fetchone()[0]
    items = []
    for (m_code, first_seen, last_seen, baseline, data, detail, result, market, qualified_on,
         entry_reason, viewed_at, starred, prev_low, hidden) in con.execute(
        "SELECT m_code, first_seen, last_seen, baseline, data, detail, result, market, qualified_on, "
        "entry_reason, viewed_at, starred, prev_low_price, hidden FROM items"
    ):
        d = json.loads(data)
        det = json.loads(detail) if detail else {}
        d.update(
            first_seen=first_seen, last_seen=last_seen, baseline=bool(baseline), active=last_seen == ref,
            qualified_on=qualified_on, qualifies=qualifies(d), entry_reason=entry_reason,
            viewed=bool(viewed_at), starred=bool(starred), hidden=bool(hidden), prev_low_price=prev_low,
            result=json.loads(result) if result else None,
            market=json.loads(market) if market else None,
            danji_name=det.get("danji_name"), naver_apt_url=det.get("naver_apt_url"),
            danji_trade_avg=det.get("danji_trade_avg"),
        )
        items.append(d)
    return {
        "today": ref, "first_run": first, "last_run": last[0] if last else None, "items": items,
        "max_low_price": MAX_LOW_PRICE,
        "stats": json.loads(STATS_PATH.read_text(encoding="utf-8")) if STATS_PATH.exists() else None, "has_molit_key": bool(load_env().get("MOLIT_API_KEY")) or any(i["market"] for i in items),
    }


def render_page(con: sqlite3.Connection, extra: dict | None = None) -> str:
    template = (ROOT / "report_template.html").read_text(encoding="utf-8")
    payload = json.dumps({**page_data(con), **(extra or {})}, ensure_ascii=False)
    return template.replace("/*__DATA__*/null", payload.replace("</", "<\\/"))


# ------------------------------------------------- GitHub 용 JSON 저장소 ---
ITEM_COLS = ["m_code", "first_seen", "last_seen", "baseline", "data", "detail", "result", "market",
             "qualified_on", "entry_reason", "prev_low_price"]
JSON_COLS = {"data", "detail", "result", "market"}


def load_store(con: sqlite3.Connection) -> None:
    """store.json → (메모리) SQLite. state.json 의 ★ 표시도 반영 (매각 결과 추적용)."""
    if STORE_PATH.exists():
        store = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        for row in store.get("items", []):
            vals = [json.dumps(row.get(c), ensure_ascii=False) if c in JSON_COLS and row.get(c) is not None
                    else row.get(c) for c in ITEM_COLS]
            con.execute(f"INSERT INTO items({','.join(ITEM_COLS)}) VALUES ({','.join('?' * len(ITEM_COLS))})", vals)
        con.executemany("INSERT INTO runs VALUES (?,?,?,?)",
                        [(r["run_at"], r["run_date"], r["total"], r["new_count"]) for r in store.get("runs", [])])
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("items", {})
        con.executemany("UPDATE items SET starred=?, viewed_at=?, hidden=? WHERE m_code=?",
                        [(1 if v.get("starred") else 0, v.get("viewed_at"), 1 if v.get("hidden") else 0, k)
                         for k, v in state.items()])
    con.commit()


def dump_store(con: sqlite3.Connection) -> None:
    """(메모리) SQLite → store.json. 물건 한 줄에 하나씩 써서 git 변경 내역이 작게 남게 한다."""
    rows = []
    for r in con.execute(f"SELECT {','.join(ITEM_COLS)} FROM items ORDER BY m_code"):
        d = {c: (json.loads(v) if c in JSON_COLS and v else v) for c, v in zip(ITEM_COLS, r)}
        rows.append(json.dumps(d, ensure_ascii=False, sort_keys=True))
    runs = [json.dumps({"run_at": a, "run_date": b, "total": c, "new_count": d}, ensure_ascii=False)
            for a, b, c, d in con.execute("SELECT * FROM runs ORDER BY run_at")]
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text('{"items": [\n' + ",\n".join(rows) + '\n],\n"runs": [\n' + ",\n".join(runs) + "\n]}\n",
                          encoding="utf-8")


def export_local_to_store() -> None:
    """지금까지 로컬(SQLite)에 쌓인 데이터와 확인함/★ 표시를 GitHub 용 파일로 옮긴다 (처음 한 번)."""
    con = db()
    dump_store(con)
    state = {m: {k: v for k, v in (("viewed_at", va), ("starred", bool(st)), ("hidden", bool(hd))) if v}
             for m, va, st, hd in con.execute("SELECT m_code, viewed_at, starred, hidden FROM items")
             if va or st or hd}
    STATE_PATH.write_text(json.dumps({"items": state}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"→ {STORE_PATH.relative_to(ROOT)} ({con.execute('SELECT COUNT(*) FROM items').fetchone()[0]}건), "
          f"{STATE_PATH.relative_to(ROOT)} ({len(state)}건)")


def build_site(con: sqlite3.Connection, out_dir: Path) -> None:
    """GitHub Pages 용 정적 페이지. 확인함/★ 표시는 브라우저가 GitHub 의 state.json 을 직접 읽고 쓴다."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    extra = {"mode": "github", "repo": repo, "branch": os.environ.get("GITHUB_REF_NAME") or "main",
             "state_path": "data/state.json", "firebase_url": firebase_url()}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(render_page(con, extra), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("")
    print(f"  사이트 → {out_dir}/index.html")


# ------------------------------------------------------------------ 알림 ---
def load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"\'')
    env.update({k: v for k, v in os.environ.items() if k.startswith(("TELEGRAM_", "MOLIT_", "ALERT_", "PAGE_URL", "BOARD_KEY"))})
    return env


def firebase_url() -> str:
    if not FIREBASE_PATH.exists():
        return ""
    return (json.loads(FIREBASE_PATH.read_text(encoding="utf-8")).get("databaseURL") or "").rstrip("/")


def load_shared(con: sqlite3.Connection, env: dict) -> None:
    """공유 DB 의 ★·숨김을 가져옴 (★ 물건은 매각 결과를 계속 추적하므로)."""
    url, key = firebase_url(), env.get("BOARD_KEY")
    if not (url and key):
        return
    try:
        state = json.loads(http_get(f"{url}/boards/{urllib.parse.quote(key)}/items.json")) or {}
    except Exception as e:
        print(f"  공유 DB 읽기 실패: {type(e).__name__}", file=sys.stderr)  # 주소에 키가 있어 내용은 출력 안 함
        return
    con.executemany("UPDATE items SET starred=?, hidden=? WHERE m_code=?",
                    [(1 if v.get("starred") else 0, 1 if v.get("hidden") else 0, k) for k, v in state.items()])
    con.commit()


def page_url(env: dict) -> str:
    """알림에 넣는 공개 페이지 주소. BOARD_KEY 가 있으면 ★·확인함을 저장할 수 있는 공유 링크."""
    repo = env.get("GITHUB_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    if repo:
        owner, name = repo.split("/", 1)
        url = f"https://{owner.lower()}.github.io/{name}/"
    else:
        url = env.get("PAGE_URL") or PUBLIC_URL
    key = env.get("BOARD_KEY")
    return f"{url}#k={key}" if key and firebase_url() else url


def telegram_recipients(env: dict) -> list[str]:
    """알림을 받을 사람(채팅 ID) 허용 목록. 이 목록에 없는 사람에게는 절대 보내지 않는다."""
    raw = env.get("TELEGRAM_CHAT_IDS") or env.get("TELEGRAM_CHAT_ID") or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def telegram_send(token: str, chat: str, text: str) -> None:
    body = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20).read()


def split_messages(blocks: list[str], limit: int = 3900) -> list[str]:
    out, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{b}" if cur else b
    return out + ([cur] if cur else [])


def notify_telegram(env: dict, new: list[dict], today: str) -> None:
    """TELEGRAM_TOKEN 과 TELEGRAM_CHAT_IDS(허용 목록)가 있으면 오늘 조건에 들어온 물건을 전송."""
    token, chats = env.get("TELEGRAM_TOKEN"), telegram_recipients(env)
    if not (token and chats):
        return
    # 화면 기본값과 같게: 선순위임차인/임차권등기 물건은 제외 (ALERT_SHOW_TAGS 로 포함 가능)
    show = {t.strip() for t in env.get("ALERT_SHOW_TAGS", "").split(",") if t.strip()}
    new = [s for s in new if not any(t in TENANT_TAGS and t not in show for t in (s.get("special_right") or []))]
    new = sorted(new, key=lambda s: (s.get("low_price") or 0) / (s.get("m_evaluate_price") or 1))
    if not new:
        blocks = [f"{today} 조건에 새로 들어온 물건 없음 (서울 아파트·감정가 10억↑·최저가 20억↓·지분 제외)\n{page_url(env)}"]
    else:
        blocks = [f"🏢 {today} 조건 진입 {len(new)}건\n(서울 아파트·감정가 10억↑·최저가 20억↓·지분 제외)\n{page_url(env)}"]
    for s in new:
        tag = "📉 유찰로 진입" if s.get("entry_reason") == "price_drop" else "🆕 새로 등록"
        blocks.append(
            f"\n• [{tag}] {s['case_num']} {s.get('2depth_addr') or ''}\n  {s['addr']}\n"
            f"  감정 {s['eval_price']} / 최저 {s['low_price_kor']} ({s['state']}) 매각기일 {s['m_bid_date']}\n"
            f"  {SITE}{s['case_url']}"
        )
    for chat in chats:
        for text in split_messages(blocks):
            try:
                telegram_send(token, chat, text)
            except Exception as e:  # 알림 실패가 수집을 막지 않도록
                print(f"텔레그램 전송 실패 (chat …{chat[-3:]}): {e}", file=sys.stderr)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="경매마당 서울 아파트 수집기")
    ap.add_argument("--store", choices=["sqlite", "json"], default="sqlite",
                    help="sqlite=로컬(data/auction.db), json=GitHub(data/store.json)")
    ap.add_argument("--site", metavar="DIR", help="정적 페이지(GitHub Pages)를 이 폴더에 생성")
    ap.add_argument("--site-only", action="store_true", help="수집 없이 페이지만 다시 생성")
    ap.add_argument("--export-store", action="store_true", help="로컬 DB → data/store.json, data/state.json")
    ap.add_argument("--results-only", action="store_true", help="목록 수집·알림 없이 매각결과만 갱신")
    args = ap.parse_args()

    if args.export_store:
        export_local_to_store()
        return 0
    env = load_env()
    con = db(memory=args.store == "json")
    if args.store == "json":
        load_store(con)
    load_shared(con, env)

    today = dt.datetime.now(KST).date().isoformat()
    molit_key = env.get("MOLIT_API_KEY")

    def refresh_results() -> None:
        added = save_results(con, fetch_results(today), today)
        print(f"  매각결과: 최근 {RESULT_SCAN_DAYS}일 확인, 새 매각액 {added}건")
        update_details(con, today)
        if molit_key:
            update_market(con, molit_key, today)
        else:
            print("  MOLIT_API_KEY 없음 → 같은 평형 실거래 시세는 건너뜀")

    if args.results_only:
        refresh_results()
        if args.store == "json":
            dump_store(con)
    elif not args.site_only:
        items = [it for it in fetch_all() if is_wanted(it)]
        new = save(con, items, today)
        print(f"[{today}] 추적 {len(items)}건 (최저가 20억↓ {sum(qualifies(slim(i)) for i in items)}건), 오늘 조건 진입 {len(new)}건")
        for s in new:
            why = "유찰로 진입" if s["entry_reason"] == "price_drop" else "새로 등록"
            print(f"  + [{why}] {s['case_num']} {s['addr']} 감정 {s['eval_price']} 최저 {s['low_price_kor']}")
        refresh_results()
        if args.store == "json":
            dump_store(con)
        notify_telegram(env, new, today)
        try:  # 통계는 부가 기능: 실패해도 수집·알림에는 영향 없음
            update_stats(today)
        except Exception as e:
            print(f"  통계 갱신 실패: {e}", file=sys.stderr)

    if args.site:
        build_site(con, Path(args.site))
    elif args.store == "sqlite":
        print(f"  화면 → {LOCAL_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
