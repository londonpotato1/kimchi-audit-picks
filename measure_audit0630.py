#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""정기실사 입출금 중지 구간 김프 실측 — 빗썸·업비트 각각. `--event=0630`(기본) / `--event=0331` / `--event=0930`.

김프(%) = 국내 KRW 15분봉 종가 / (해외 USDT 15분봉 종가 × 같은 거래소 USDT/KRW 15분봉 종가) × 100 − 100
- 15분봉 종가 (2026-10-01 1시간봉에서 전환 — 1시간 안에 꺼지는 급등·급락을 1시간 종가가 놓침:
  9/30 PROS 업비트 역현선 1h −2.2%p vs 15m −5.7%p), 중지 구간만 측정 (재개 후 제외)
- 해외 기준가: 바이낸스 현물 → Gate 현물 → 바이비트 현물 (거래소 실거래 종가만). Gate 15분봉은 최근 10000개
  (~104일)까지만 줘서 3/31 은 바이비트로 넘어감. CoinGecko 는 15분 시세가 없어 쓰지 않음 — 해외 거래소 페어가
  없는 코인은 측정 제외.
- 직전 24h 평균 김프가 ±15% 밖이면 그 거래소 페어는 버림 (동명이인 또는 이미 격리된 고김프).
- 국내 호가 한 칸이 가격의 2% 초과인 초저가 코인은 김프가 호가단위 허수라 제외.
- pre_delta = 중지 직전 3시간 최대 김프 − 같은 기준선 (중지 직전에 튀고 중지와 함께 꺼지는 펌핑 포착).
- Δ = 중지 구간 최대 김프 − 직전 24h 평균 김프. 최대−평균이라 잡음만으로도 양수가 나오므로
  `--control` 로 하루 전 같은 시간대(평상시)를 같은 방식으로 재서 비교한다.
- `--futures`: 해외 기준가를 USDS 무기한(바이낸스 → 바이비트 → Gate)으로 바꿔 역현선을 잰다.
  rev_delta = 중지 구간 최저 (국내 현물 − 해외 무기한) − 같은 기준선. 음수일수록 국내가 선물보다 싸게 벌어짐
  (IN 6/30 업비트, WLD·PROS·SOON 9/30 — 김프 Δ 는 최대만 봐서 안 잡힘).
입력: data_D_{event}.json (그 실사 직전 대시보드 스냅샷 — 측정 대상 종목)
출력: audit_{event}_{futures_}premium.json / _control.json (성공분 보존, 재실행 시 실패분만 재측정)
"""
import datetime as dt
import json
import sys
import time
import urllib.error
from pathlib import Path
from urllib.parse import quote

from build_audit0930_ids import get, krw_markets

DIR = Path(__file__).resolve().parent
KST = dt.timezone(dt.timedelta(hours=9))
H = 3600
# 캔들 시작 시각 기준 [start, end). 업비트는 "순차 재개" 시작 시각을 몰라 재개 완료 시각보다
# 한 시간 이상 앞 정시에서 자름 (06:55 완료 → 05:00, 05:15 → 04:00, 04:58 → 03:00).
AUDITS = {
    # 빗썸 공지 1653832 17:00~03:00 · 업비트 공지 6320 20:00~08:00 예정, 06:55 재개 완료
    "0630": {"bithumb": (dt.datetime(2026, 6, 30, 17, tzinfo=KST), dt.datetime(2026, 7, 1, 3, tzinfo=KST)),
             "upbit": (dt.datetime(2026, 6, 30, 20, tzinfo=KST), dt.datetime(2026, 7, 1, 5, tzinfo=KST))},
    # 빗썸 공지 1654966 17:00~03:00 (10/1 03:00 완료) · 업비트 공지 6610 20:00~08:00 예정, 04:58 재개 완료
    "0930": {"bithumb": (dt.datetime(2026, 9, 30, 17, tzinfo=KST), dt.datetime(2026, 10, 1, 3, tzinfo=KST)),
             "upbit": (dt.datetime(2026, 9, 30, 20, tzinfo=KST), dt.datetime(2026, 10, 1, 3, tzinfo=KST))},
    # 빗썸 공지 1652422 17:00~03:00 · 업비트 공지 6090 20:00~08:00 예정, 05:15 재개 완료
    "0331": {"bithumb": (dt.datetime(2026, 3, 31, 17, tzinfo=KST), dt.datetime(2026, 4, 1, 3, tzinfo=KST)),
             "upbit": (dt.datetime(2026, 3, 31, 20, tzinfo=KST), dt.datetime(2026, 4, 1, 4, tzinfo=KST))},
}
if "--event" in sys.argv:  # 띄어 쓰면 조용히 기본값(0630)으로 돌아 그 결과 파일을 건드리게 됨
    raise SystemExit("--event=0331 처럼 = 로 붙여 쓰세요")
EVENT = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--event=")), "0630")
AUDIT = AUDITS[EVENT]
CONTROL = "--control" in sys.argv
FUTURES = "--futures" in sys.argv
SHIFT = dt.timedelta(hours=24 if CONTROL else 0)
EVENTS = {ex: (s - SHIFT, e - SHIFT) for ex, (s, e) in AUDIT.items()}
OUT = DIR / f"audit_{EVENT}_{'futures_' if FUTURES else ''}{'control' if CONTROL else 'premium'}.json"
STEP_MIN = 15
TIME_FMT = "%m-%d %H:%M"
PERP_ALIAS = {"PROS": "PHAROS"}  # 바이낸스·바이비트 무기한만 PHAROSUSDT (바이낸스 현물 PROS = 옛 Prosper, 2026-09-30 실측)
G_FROM = int(min(s for s, _ in EVENTS.values()).timestamp()) - 24 * H
G_TO = int(max(e for _, e in EVENTS.values()).timestamp()) + H
MAX_BASELINE = 15.0
MAX_TICK = 0.02
PRE_HOURS = 3


def domestic(exchange: str, sym: str) -> dict[int, float]:
    """캔들 시작(UTC epoch) → 종가. 거래 없는 시간은 캔들 자체가 없음."""
    end = EVENTS[exchange][1]
    if exchange == "bithumb":  # to = KST 문자열, 해당 시각 캔들 제외
        url = f"https://api.bithumb.com/v1/candles/minutes/{STEP_MIN}?market=KRW-{sym}&to={quote(end.strftime('%Y-%m-%d %H:%M:%S'))}&count={40 * 60 // STEP_MIN}"
    else:  # 업비트 to = ISO(+09:00), 해당 시각 이전
        url = f"https://api.upbit.com/v1/candles/minutes/{STEP_MIN}?market=KRW-{sym}&to={quote(end.isoformat())}&count={40 * 60 // STEP_MIN}"
    rows = get(url)
    time.sleep(0.15)
    if not isinstance(rows, list):  # 빗썸 상장폐지 마켓은 HTTP 200 + {"error": {"name": 404}}
        return {}
    return {int(dt.datetime.fromisoformat(r["candle_date_time_utc"]).replace(tzinfo=dt.UTC).timestamp()):
            float(r["trade_price"]) for r in rows}


def binance(sym: str) -> dict[int, float]:
    kl = get(f"https://api.binance.com/api/v3/klines?symbol={sym}USDT&interval=15m"
             f"&startTime={G_FROM * 1000}&endTime={G_TO * 1000}&limit=200")
    return {int(k[0]) // 1000: float(k[4]) for k in kl}


def gate(sym: str) -> dict[int, float]:
    kl = get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={sym}_USDT&interval=15m&from={G_FROM}&to={G_TO}")
    return {int(k[0]): float(k[2]) for k in kl}


def bybit(sym: str) -> dict[int, float]:
    j = get(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={sym}USDT"
            f"&interval=15&start={G_FROM * 1000}&end={G_TO * 1000}&limit=200")
    return {int(k[0]) // 1000: float(k[4]) for k in j["result"]["list"]} if j.get("retCode") == 0 else {}


def binance_perp(sym: str) -> dict[int, float]:
    for prefix, factor in (("", 1), ("1000", 1000)):  # 1000PEPE 처럼 1000개 묶음 티커는 가격 ÷1000
        try:
            kl = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={prefix}{PERP_ALIAS.get(sym, sym)}USDT"
                     f"&interval=15m&startTime={G_FROM * 1000}&endTime={G_TO * 1000}&limit=200")
        except urllib.error.HTTPError as e:
            if e.code == 400:  # 없는 심볼
                continue
            raise
        return {int(k[0]) // 1000: float(k[4]) / factor for k in kl}
    return {}


def bybit_perp(sym: str) -> dict[int, float]:
    for prefix, factor in (("", 1), ("1000", 1000)):
        j = get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={prefix}{PERP_ALIAS.get(sym, sym)}USDT"
                f"&interval=15&start={G_FROM * 1000}&end={G_TO * 1000}&limit=200")
        if j.get("retCode") == 0 and j["result"]["list"]:  # 없는 심볼은 HTTP 200 + retCode≠0
            return {int(k[0]) // 1000: float(k[4]) / factor for k in j["result"]["list"]}
    return {}


def gate_perp(sym: str) -> dict[int, float]:
    kl = get(f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={sym}_USDT&interval=15m&from={G_FROM}&to={G_TO}")
    return {int(k["t"]): float(k["c"]) for k in kl}


def measure(exchange: str, dom: dict[int, float], fx: dict[int, float], glob: dict[int, float]) -> dict | float | None:
    """성공 → 결과 dict, 기준 김프 과대 → 그 기준 김프(float), 데이터 부족 → None."""
    start, end = (int(t.timestamp()) for t in EVENTS[exchange])
    prem = {h: dom[h] / (glob[h] * fx[h]) * 100 - 100 for h in dom if h in fx and glob.get(h)}
    base = [p for h, p in prem.items() if start - 24 * H <= h < start]
    win = [(h, p) for h, p in prem.items() if start <= h < end]
    dom_base = sum(1 for h in dom if start - 24 * H <= h < start)
    dom_win = sum(1 for h in dom if start <= h < end)
    # 최소 관측 개수 (기준선 6개·구간 3개) + 국내 캔들 대비 해외·환율 짝 70% — 저유동 코인은 15분봉이 듬성듬성
    if len(base) < max(6, 0.7 * dom_base) or len(win) < max(3, 0.7 * dom_win):
        return None
    baseline = sum(base) / len(base)
    if abs(baseline) > MAX_BASELINE:
        return baseline
    peak_h, peak = max(win, key=lambda x: x[1])
    low_h, low = min(win, key=lambda x: x[1])
    # 실사 직전 3시간 — 중지 직전에 튀고 중지와 함께 꺼지는 펌핑 (O 9/30 16:45 +16.4%p)
    pre = [(h, p) for h, p in prem.items() if start - PRE_HOURS * H <= h < start]
    pre_h, pre_p = max(pre, key=lambda x: x[1]) if pre else (None, None)
    return {
        "baseline": round(baseline, 2), "max_prem": round(peak, 2),
        "mean_prem": round(sum(p for _, p in win) / len(win), 2),
        "delta": round(peak - baseline, 2),
        "max_time": dt.datetime.fromtimestamp(peak_h, KST).strftime(TIME_FMT),
        "pre_delta": round(pre_p - baseline, 2) if pre else None,
        "pre_time": dt.datetime.fromtimestamp(pre_h, KST).strftime(TIME_FMT) if pre else None,
        "min_prem": round(low, 2), "rev_delta": round(low - baseline, 2),
        "min_time": dt.datetime.fromtimestamp(low_h, KST).strftime(TIME_FMT),
        "n_base": len(base), "n_win": len(win),
    }


def choose(exchange: str, dom: dict[int, float], fx: dict[int, float], series, sources) -> dict:
    guards: list[str] = []
    for source in sources:
        m = measure(exchange, dom, fx, series(source))
        if isinstance(m, dict):
            return {"source": source} | m
        if isinstance(m, float):
            guards.append(f"{source} {m:+.1f}%")
    if guards:
        return {"err": f"기준 김프 과대 ({', '.join(guards)}) — 동명이인 페어 또는 이미 격리된 고김프"}
    return {"err": "해외 기준가 없음 또는 국내 거래 부족"}


def tick_ratio(dom: dict[int, float]) -> float:
    """관측 가격 사이 최소 간격 ÷ 중앙 가격. 초저가 코인은 호가 한 칸이 수십 % (NFT 6/30: 0.0004↔0.0005 = 25%)."""
    levels = sorted(set(dom.values()))
    steps = [b - a for a, b in zip(levels, levels[1:])]
    return min(steps) / levels[len(levels) // 2] if steps else 0.0


def main() -> None:
    before = json.loads((DIR / f"data_D_{EVENT}.json").read_text())  # 그 실사 직전 대시보드 스냅샷
    sources = ("binance_perp", "bybit_perp", "gate_perp") if FUTURES else ("binance", "gate", "bybit")
    fetchers = {"binance": binance, "gate": gate, "bybit": bybit, "binance_perp": binance_perp, "bybit_perp": bybit_perp,
                "gate_perp": gate_perp}
    targets = {
        "bithumb": sorted((set(krw_markets("https://api.bithumb.com")) | {r["coin"] for r in before}) - {"USDT"}),
        "upbit": sorted(set(krw_markets("https://api.upbit.com")) - {"USDT"}),
    }
    done = {}
    if OUT.exists():
        done = {(r["exchange"], r["coin"]): r for r in json.loads(OUT.read_text()) if "delta" in r}
    fx = {ex: domestic(ex, "USDT") for ex in EVENTS}
    glob_cache: dict[tuple[str, str], dict[int, float]] = {}

    def glob(source: str, sym: str) -> dict[int, float]:
        key = (source, sym)
        if key not in glob_cache:
            try:
                glob_cache[key] = fetchers[source](sym)
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError):
                glob_cache[key] = {}
        return glob_cache[key]

    results = list(done.values())
    for ex, coins in targets.items():
        todo = [c for c in coins if (ex, c) not in done]
        print(f"[{ex}] 대상 {len(coins)} · 기존 성공 {len(coins) - len(todo)} · 측정 {len(todo)}")
        for i, sym in enumerate(todo, 1):
            row = {"exchange": ex, "coin": sym}
            try:
                dom = domestic(ex, sym)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                results.append(row | {"err": f"국내 캔들 없음 ({e})"})
                continue
            if not dom:
                results.append(row | {"err": "국내 캔들 0 (당시 미상장 추정)"})
                continue
            if (tick := tick_ratio(dom)) > MAX_TICK:
                results.append(row | {"err": f"호가단위 왜곡 (1틱 = 가격의 {tick * 100:.1f}%)"})
                continue
            results.append(row | choose(ex, dom, fx[ex], lambda source: glob(source, sym), sources))
            if i % 50 == 0:
                OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1))
                print(f"  {i}/{len(todo)}")
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    for ex in EVENTS:
        ok = sorted((r for r in results if r["exchange"] == ex and "delta" in r),
                    key=lambda r: r["rev_delta"] if FUTURES else -r["delta"])
        src: dict[str, int] = {}
        for r in ok:
            src[r["source"]] = src.get(r["source"], 0) + 1
        label = f"{EVENT} " + ("평상시 대조(하루 전)" if CONTROL else "실사")
        print(f"\n=== {ex} {label} {'역현선' if FUTURES else 'Δ김프'} 상위 15 (성공 {len(ok)}/{len(targets[ex])}, 소스 {src}) ===")
        for r in ok[:15]:
            if FUTURES:
                print(f"  {r['coin']:8} base {r['baseline']:6.2f}  min {r['min_prem']:6.2f} @{r['min_time']}  역Δ {r['rev_delta']:6.2f}  [{r['source']}]")
            else:
                print(f"  {r['coin']:8} base {r['baseline']:6.2f}  max {r['max_prem']:6.2f} @{r['max_time']}  Δ {r['delta']:6.2f}  [{r['source']}]")


if __name__ == "__main__":
    main()
