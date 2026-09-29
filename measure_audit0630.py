#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""6/30 정기실사 입출금 중지 구간 김프 실측 — 빗썸·업비트 각각.

김프(%) = 국내 KRW 1h 종가 / (해외 USDT 1h 종가 × 같은 거래소 USDT/KRW 1h 종가) × 100 − 100
- 종가 기준 (저유동 알트의 순간 윅 제외), 중지 구간만 측정 (재개 후 제외)
- 해외 기준가: 바이낸스 현물 → Gate 현물 → CoinGecko(검증 ID). CoinGecko 가중평균은 국내 비중이 큰
  코인에서 국내가에 끌려 김프를 작게 잡음 (HOOK 6/30 실측 ~1%p) → 거래소 실거래 종가 우선.
- 직전 24h 평균 김프가 ±15% 밖이면 그 거래소 페어는 버림 (동명이인 또는 이미 격리된 고김프).
  이때 CoinGecko 로 우회하지 않음 — 국내가가 섞인 평균이라 Δ 가 조작됨 (TAIKO: CG +14.8%p vs Gate −0.3%p).
  CoinGecko 는 거래소 페어가 없거나 해당 구간 데이터가 모자랄 때만.
- 국내 호가 한 칸이 가격의 2% 초과인 초저가 코인은 김프가 호가단위 허수라 제외.
- Δ = 중지 구간 최대 김프 − 직전 24h 평균 김프. 최대−평균이라 잡음만으로도 양수가 나오므로
  `--control` 로 하루 전 같은 시간대(평상시)를 같은 방식으로 재서 비교한다.
출력: audit_0630_premium.json / audit_0630_control.json (성공분 보존, 재실행 시 실패분만 재측정)
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
# 캔들 시작 시각 기준 [start, end). 빗썸 공지 1653832 17:00~03:00.
# 업비트 공지 6320 20:00~08:00 예정, 순차 재개 후 06:55 완료 — 재개 시작 시각을 몰라 05:00 에서 자름.
AUDIT = {
    "bithumb": (dt.datetime(2026, 6, 30, 17, tzinfo=KST), dt.datetime(2026, 7, 1, 3, tzinfo=KST)),
    "upbit": (dt.datetime(2026, 6, 30, 20, tzinfo=KST), dt.datetime(2026, 7, 1, 5, tzinfo=KST)),
}
CONTROL = "--control" in sys.argv
SHIFT = dt.timedelta(hours=24 if CONTROL else 0)
EVENTS = {ex: (s - SHIFT, e - SHIFT) for ex, (s, e) in AUDIT.items()}
OUT = DIR / ("audit_0630_control.json" if CONTROL else "audit_0630_premium.json")
G_FROM = int(min(s for s, _ in EVENTS.values()).timestamp()) - 24 * H
G_TO = int(max(e for _, e in EVENTS.values()).timestamp()) + H
MAX_BASELINE = 15.0
MAX_TICK = 0.02


def domestic(exchange: str, sym: str) -> dict[int, float]:
    """캔들 시작(UTC epoch) → 종가. 거래 없는 시간은 캔들 자체가 없음."""
    end = EVENTS[exchange][1]
    if exchange == "bithumb":  # to = KST 문자열, 해당 시각 캔들 제외
        url = f"https://api.bithumb.com/v1/candles/minutes/60?market=KRW-{sym}&to={quote(end.strftime('%Y-%m-%d %H:%M:%S'))}&count=40"
    else:  # 업비트 to = ISO(+09:00), 해당 시각 이전
        url = f"https://api.upbit.com/v1/candles/minutes/60?market=KRW-{sym}&to={quote(end.isoformat())}&count=40"
    rows = get(url)
    time.sleep(0.15)
    if not isinstance(rows, list):  # 빗썸 상장폐지 마켓은 HTTP 200 + {"error": {"name": 404}}
        return {}
    return {int(dt.datetime.fromisoformat(r["candle_date_time_utc"]).replace(tzinfo=dt.UTC).timestamp()):
            float(r["trade_price"]) for r in rows}


def binance(sym: str) -> dict[int, float]:
    kl = get(f"https://api.binance.com/api/v3/klines?symbol={sym}USDT&interval=1h"
             f"&startTime={G_FROM * 1000}&endTime={G_TO * 1000}&limit=100")
    return {int(k[0]) // 1000: float(k[4]) for k in kl}


def gate(sym: str) -> dict[int, float]:
    kl = get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={sym}_USDT&interval=1h&from={G_FROM}&to={G_TO}")
    return {int(k[0]): float(k[2]) for k in kl}


def coingecko(gid: str) -> dict[int, float]:
    pts = get(f"https://api.coingecko.com/api/v3/coins/{quote(gid)}/market_chart/range"
              f"?vs_currency=usd&from={G_FROM}&to={G_TO + H}")["prices"]
    time.sleep(13)  # 무료 한도 (2026-09-29 403/429 실측)
    out: dict[int, float] = {}
    for ts_ms, price in pts:  # 캔들 종료 시각(h+1h)에 가장 가까운 점(±30분)을 그 캔들의 종가로
        end = round(ts_ms / 1000 / H) * H
        if abs(ts_ms / 1000 - end) <= 1800:
            out[end - H] = float(price)
    return out


def measure(exchange: str, dom: dict[int, float], fx: dict[int, float], glob: dict[int, float]) -> dict | float | None:
    """성공 → 결과 dict, 기준 김프 과대 → 그 기준 김프(float), 데이터 부족 → None."""
    start, end = (int(t.timestamp()) for t in EVENTS[exchange])
    prem = {h: dom[h] / (glob[h] * fx[h]) * 100 - 100 for h in dom if h in fx and glob.get(h)}
    base = [p for h, p in prem.items() if start - 24 * H <= h < start]
    win = [(h, p) for h, p in prem.items() if start <= h < end]
    dom_base = sum(1 for h in dom if start - 24 * H <= h < start)
    dom_win = sum(1 for h in dom if start <= h < end)
    if len(base) < max(6, 0.7 * dom_base) or len(win) < max(3, 0.7 * dom_win):
        return None
    baseline = sum(base) / len(base)
    if abs(baseline) > MAX_BASELINE:
        return baseline
    peak_h, peak = max(win, key=lambda x: x[1])
    return {
        "baseline": round(baseline, 2), "max_prem": round(peak, 2),
        "mean_prem": round(sum(p for _, p in win) / len(win), 2),
        "delta": round(peak - baseline, 2),
        "max_time": dt.datetime.fromtimestamp(peak_h, KST).strftime("%m-%d %H시"),
        "n_base": len(base), "n_win": len(win), "dom_win_hours": dom_win,
    }


def choose(exchange: str, dom: dict[int, float], fx: dict[int, float], series, sources) -> dict:
    guards: list[str] = []
    for source in sources:
        if source == "coingecko" and guards:
            break
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
    before = json.loads((DIR / "data_D_0630.json").read_text())
    gecko = json.loads((DIR / "gecko_map.json").read_text())
    universe_file = DIR / "audit0930_universe.json"
    if universe_file.exists():  # 0단계 가격 대조 통과 ID 우선
        gecko.update({u["coin"]: u["gecko_id"] for u in json.loads(universe_file.read_text()) if u["gecko_id"]})
    sources = ("binance", "gate") if "--no-coingecko" in sys.argv else ("binance", "gate", "coingecko")
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
                if source == "binance":
                    glob_cache[key] = binance(sym)
                elif source == "gate":
                    glob_cache[key] = gate(sym)
                else:
                    glob_cache[key] = coingecko(gecko[sym]) if sym in gecko else {}
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
        ok = sorted((r for r in results if r["exchange"] == ex and "delta" in r), key=lambda r: -r["delta"])
        src: dict[str, int] = {}
        for r in ok:
            src[r["source"]] = src.get(r["source"], 0) + 1
        label = "평상시 대조(하루 전)" if CONTROL else "6/30 실사"
        print(f"\n=== {ex} {label} Δ김프 상위 15 (성공 {len(ok)}/{len(targets[ex])}, 소스 {src}) ===")
        for r in ok[:15]:
            print(f"  {r['coin']:8} base {r['baseline']:6.2f}  max {r['max_prem']:6.2f} @{r['max_time']}  Δ {r['delta']:6.2f}  [{r['source']}]")


if __name__ == "__main__":
    main()
