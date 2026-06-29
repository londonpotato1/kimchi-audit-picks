#!/usr/bin/env python3
"""
빗썸 상장 전체 코인의 CoinGecko gecko_id 매핑 구축 (티커충돌 회피).
CoinGecko /exchanges/bithumb/tickers (빗썸 거래소 컨텍스트라 base심볼→coin_id 정확).
KRW 마켓 우선. 결과: gecko_map.json {symbol: gecko_id}
"""
import json, urllib.request, urllib.error, time

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def cg(url, retries=6):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(20 * (i + 1)); continue
            if i == retries - 1: raise
            time.sleep(3)
        except Exception:
            if i == retries - 1: raise
            time.sleep(3)
    raise RuntimeError(url)


mapping = {}
krw = {}
for page in range(1, 9):
    d = cg(f"https://api.coingecko.com/api/v3/exchanges/bithumb/tickers?page={page}")
    ts = d.get("tickers", [])
    if not ts:
        break
    for t in ts:
        b, cid, tgt = t.get("base"), t.get("coin_id"), t.get("target")
        if not b or not cid:
            continue
        if tgt == "KRW":
            krw[b] = cid          # KRW 마켓 우선
        mapping.setdefault(b, cid)
    print(f"page{page}: {len(ts)}티커, 누적 {len(mapping)}종")
    time.sleep(5)

# KRW 우선 병합
mapping.update(krw)
json.dump(mapping, open("gecko_map.json", "w"), ensure_ascii=False, indent=0)
print(f"저장: gecko_map.json ({len(mapping)}종, KRW우선 {len(krw)})")
