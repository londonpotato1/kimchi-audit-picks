#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pypdf", "cryptography"]
# ///
"""9/30 정기실사 탭 0단계 — 종목 우주 확정 + CoinGecko ID 가격 대조 검증 + 업비트 7/1 실사보고서 파싱.

우주 = 빗썸 KRW ∪ 업비트 KRW (v1 market/all).
CoinGecko ID는 국내 KRW 현재가 / (CoinGecko USD × 빗썸 USDT/KRW) 비율이 0.8~1.25 인 후보만 채택
(8/25 BNB28 때 28종 중 5종 오매핑 재발 방지). 기존 gecko_map 이 통과하면 우선.
출력: audit0930_universe.json, upbit_report_0701.json
주의: cg_markets_cache.json 은 만료가 없다 — 다른 날 다시 돌릴 때는 지우고 실행 (시총·유통량이 캐시 시점에 고정).
"""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

DIR = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
CG = "https://api.coingecko.com/api/v3"
UPBIT_REPORT = "https://static.upbit.com/reports/upbit_asset_report_202607_en.pdf"
PARITY = (0.8, 1.25)
SYMBOL_ALIAS = {"MET2": "MET", "META2": "META", "UP2": "UP"}  # 거래소 심볼 ≠ CoinGecko 심볼
STABLES = {"USDT", "USDC", "USD1", "PYUSD", "RLUSD", "EURC", "USDG", "USDE", "USDS", "DAI",
           "FDUSD", "TUSD", "USD0", "JPYC", "USDQ", "USDF"}


def get(url: str, retries: int = 6):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and i < retries - 1:  # CoinGecko 무료 한도 초과 시 403도 반환
                time.sleep(60 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i == retries - 1:
                raise
            time.sleep(3)
    raise RuntimeError(url)


def krw_markets(base: str) -> dict[str, dict[str, str]]:
    return {m["market"][4:]: m for m in get(f"{base}/v1/market/all") if m["market"].startswith("KRW-")}


def krw_prices(base: str, symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(symbols), 150):  # 빗썸 10/13부터 markets 200개 제한
        markets = ",".join(f"KRW-{s}" for s in symbols[i:i + 150])
        for t in get(f"{base}/v1/ticker?markets={quote(markets, safe=',-')}"):
            if t.get("trade_price"):
                out[t["market"][4:]] = float(t["trade_price"])
        time.sleep(0.3)
    return out


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def parse_upbit_report() -> dict[str, int]:
    from pypdf import PdfReader
    pdf = DIR / "upbit_asset_report_202607_en.pdf"
    if not pdf.exists():
        req = urllib.request.Request(UPBIT_REPORT, headers={"User-Agent": "Mozilla/5.0"})
        pdf.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    text = "\n".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
    rows: dict[str, int] = {}
    for m in re.finditer(r"^\s*(\d+)\s+([A-Z0-9]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%", text, re.M):
        rows[m.group(2)] = int(m.group(3).replace(",", ""))  # A열 = 고객 위탁량(전자장부)
    if len(rows) < 700:
        raise SystemExit(f"업비트 보고서 파싱 이상: {len(rows)}행")
    return rows


def main() -> None:
    bithumb = krw_markets("https://api.bithumb.com")
    upbit = krw_markets("https://api.upbit.com")
    symbols = sorted(set(bithumb) | set(upbit))
    bprice = krw_prices("https://api.bithumb.com", sorted(bithumb))
    uprice = krw_prices("https://api.upbit.com", sorted(upbit))
    print(f"빗썸 KRW {len(bithumb)} · 업비트 KRW {len(upbit)} · 합집합 {len(symbols)} · 가격 {len(bprice)}/{len(uprice)}")

    gecko_map: dict[str, str] = json.loads((DIR / "gecko_map.json").read_text())
    from bnb28_event import GECKO_ID as BNB28_VERIFIED  # 8/25 가격 대조 검증 완료분
    gecko_map.update(BNB28_VERIFIED)
    usdt_krw = bprice["USDT"]
    # CoinGecko 무료 한도가 빡빡하고 이 IP에서 /coins/markets 는 403 (2026-09-29) → simple/price + 파일 캐시
    cache, list_cache = DIR / "cg_markets_cache.json", DIR / "cg_coins_list.json"
    markets: dict[str, dict] = json.loads(cache.read_text()) if cache.exists() else {}
    if not list_cache.exists():
        list_cache.write_text(json.dumps(get(f"{CG}/coins/list")))
    coins = json.loads(list_cache.read_text())
    names = {c["id"]: c["name"] for c in coins}
    by_symbol: dict[str, list[str]] = {}
    for c in coins:
        by_symbol.setdefault(c["symbol"].upper(), []).append(c["id"])

    def load(ids) -> None:
        todo = sorted(set(ids) - set(markets))
        for i in range(0, len(todo), 100):
            time.sleep(20)
            batch = todo[i:i + 100]
            data = get(f"{CG}/simple/price?ids={','.join(quote(x) for x in batch)}"
                       "&vs_currencies=usd&include_market_cap=true&include_last_updated_at=true")
            for gid in batch:
                p = data.get(gid) or {}
                price, mc, ts = p.get("usd"), p.get("usd_market_cap"), p.get("last_updated_at")
                markets[gid] = {
                    "name": names.get(gid, ""), "current_price": price, "market_cap": mc or None,
                    "circulating_supply": mc / price if price and mc else None,  # CoinGecko mc = 가격 × 유통량
                    "last_updated": datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z") if ts else None,
                }
            cache.write_text(json.dumps(markets))

    def parity(s: str, gid: str) -> float | None:
        dom, mk = bprice.get(s) or uprice.get(s), markets.get(gid)
        if not (dom and mk and mk.get("current_price")):
            return None
        ratio = dom / (mk["current_price"] * usdt_krw)
        return ratio if PARITY[0] <= ratio <= PARITY[1] else None

    load(gecko_map[s] for s in symbols if s in gecko_map)
    pending = [s for s in symbols if not (s in gecko_map and parity(s, gecko_map[s]) is not None)]
    load(i for s in pending for i in by_symbol.get(SYMBOL_ALIAS.get(s, s), []))
    print(f"기존 매핑 통과 {len(symbols) - len(pending)} · 후보 재탐색 {len(pending)}종 · CoinGecko 시세 {len(markets)}개")
    cands = {s: list(dict.fromkeys(([gecko_map[s]] if s in gecko_map else [])
                                   + (by_symbol.get(SYMBOL_ALIAS.get(s, s), []) if s in pending else [])))
             for s in symbols}

    def by_mc(o: tuple) -> float:
        return o[2].get("market_cap") or 0

    universe = []
    for s in symbols:
        info = bithumb.get(s) or upbit[s]
        eng = norm(info.get("english_name", ""))

        def named(name: str) -> bool:
            return bool(eng) and bool(norm(name)) and (eng in norm(name) or norm(name) in eng)

        ok = [(gid, r, markets[gid]) for gid in cands[s] if (r := parity(s, gid)) is not None]
        choice, status = None, "fail"
        if ok:
            mapped = [o for o in ok if o[0] == gecko_map.get(s)]
            by_name = [o for o in ok if named(o[2]["name"])]
            if mapped:
                choice, status = mapped[0], "map_ok"
            elif by_name:  # 이름 일치 여럿(원본·브리지 토큰)이면 시총 큰 원본
                choice, status = max(by_name, key=by_mc), "auto_name"
            else:
                choice = max(ok, key=by_mc)
                status = "auto_rank" if len(ok) == 1 else "ambiguous"
        elif (prev := gecko_map.get(s)) and (mk := markets.get(prev)) and mk.get("current_price") and named(mk["name"]):
            # 가격 괴리가 실제 김프인 종목 (입출금 중지 등) — 기존 매핑 + 이름 일치면 유지, 비율 = 현재 김프
            dom = bprice.get(s) or uprice.get(s)
            choice, status = (prev, dom / (mk["current_price"] * usdt_krw), mk), "map_kimp"
        universe.append({
            "coin": s, "kor_name": info.get("korean_name"), "eng_name": info.get("english_name"),
            "on_bithumb": s in bithumb, "on_upbit": s in upbit, "stable": s in STABLES,
            "price_krw_bithumb": bprice.get(s), "price_krw_upbit": uprice.get(s),
            "gecko_id": choice[0] if choice else None,
            "gecko_name": choice[2]["name"] if choice else None,
            "parity": round(choice[1], 4) if choice else None,
            "id_status": status, "id_candidates_ok": len(ok),
            "prev_gecko_id": gecko_map.get(s),
            "mc_usd": choice[2].get("market_cap") if choice else None,
            "circulating_supply": choice[2].get("circulating_supply") if choice else None,
            "cg_last_updated": choice[2].get("last_updated") if choice else None,
        })

    report = parse_upbit_report()
    (DIR / "upbit_report_0701.json").write_text(json.dumps(report, indent=1) + "\n")
    (DIR / "audit0930_universe.json").write_text(json.dumps(universe, ensure_ascii=False, indent=1) + "\n")
    st: dict[str, int] = {}
    for u in universe:
        st[u["id_status"]] = st.get(u["id_status"], 0) + 1
    print("ID 상태:", st, "| 업비트 보고서", len(report), "행")
    changed = [u for u in universe if u["prev_gecko_id"] and u["gecko_id"] and u["prev_gecko_id"] != u["gecko_id"]]
    print("기존 매핑과 달라진 종목:", [(u["coin"], u["prev_gecko_id"], "→", u["gecko_id"]) for u in changed])
    print("검토 필요(fail/ambiguous):", [(u["coin"], u["id_status"], u["eng_name"]) for u in universe
                                    if u["id_status"] in ("fail", "ambiguous")])
    print("현재 김프로 가격 괴리(map_kimp):", [(u["coin"], f"{(u['parity'] - 1) * 100:+.0f}%") for u in universe
                                          if u["id_status"] == "map_kimp"])
    print("브리지·래핑 이름 의심:", [(u["coin"], u["gecko_name"]) for u in universe
                             if u["gecko_name"] and re.search(r"bridged|wrapped", u["gecko_name"], re.I)])


if __name__ == "__main__":
    main()
