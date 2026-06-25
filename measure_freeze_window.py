#!/usr/bin/env python3
"""
입출막 기간 정밀 김프 측정 (빗썸 공지 1653364: 5/28 19:00 중지 ~ 5/29 재개).
- strict: 5/28 19:00 ~ 5/29 03:00 (Base 업그레이드 시점 = 100% 막힌 8시간)
- full  : 5/28 19:00 ~ 5/30 00:00 (재개일 5/29 전체 포함, 보수적 상한)
시계열을 premium_series.json 에 캐시 (윈도우 재분석 시 재호출 불필요).
"""
import json, urllib.request, urllib.error, datetime, time, os

KST = datetime.timezone(datetime.timedelta(hours=9))
UTC = datetime.timezone.utc
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def hj(url, retries=6):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(20 * (i + 1)); continue
            if i == retries - 1: raise
            time.sleep(2 * (i + 1))
        except Exception:
            if i == retries - 1: raise
            time.sleep(2 * (i + 1))
    raise RuntimeError(url)


def bt1h(sym):
    d = hj(f"https://api.bithumb.com/public/candlestick/{sym}_KRW/1h")
    if d.get("status") != "0000":
        return None, None
    c = {int(int(r[0]) // 3600000 * 3600): float(r[2]) for r in d["data"]}
    h = {int(int(r[0]) // 3600000 * 3600): float(r[3]) for r in d["data"]}
    return c, h


def cg1h(gid, frm, to):
    d = hj(f"https://api.coingecko.com/api/v3/coins/{gid}/market_chart/range"
           f"?vs_currency=usd&from={frm}&to={to}")
    return {int(int(ts) // 3600000 * 3600): p for ts, p in d.get("prices", [])}


def ep(y, mo, d, h):
    return int(datetime.datetime(y, mo, d, h, tzinfo=UTC).timestamp())


freeze = ep(2026, 5, 28, 10)        # 5/28 19:00 KST
end_strict = ep(2026, 5, 28, 18)    # 5/29 03:00 KST (업그레이드 = 확실 막힘 끝)
end_full = ep(2026, 5, 29, 15)      # 5/30 00:00 KST (재개일 전체 포함)
base_start = freeze - 24 * 3600
cg_frm, cg_to = ep(2026, 5, 25, 0), ep(2026, 5, 31, 0)

coins = {d["coin"]: d["gecko_id"] for d in json.load(open("data_base27.json"))}
rate_c, _ = bt1h("USDT")
cache_path = "premium_series.json"
cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

results = []
for coin, gid in coins.items():
    try:
        if coin in cache:
            series = {int(k): v for k, v in cache[coin].items()}
        else:
            _, bt_h = bt1h(coin); time.sleep(0.15)
            cg = cg1h(gid, cg_frm, cg_to); time.sleep(5)
            if not bt_h:
                results.append({"coin": coin, "err": "bithumb"}); continue
            series = {h: bt_h[h] / (cg[h] * rate_c[h]) * 100 - 100
                      for h in bt_h if h in cg and h in rate_c and cg[h] > 0 and rate_c[h] > 0}
            cache[coin] = {str(k): round(v, 3) for k, v in series.items()}
            json.dump(cache, open(cache_path, "w"))
        base = [p for h, p in series.items() if base_start <= h < freeze]
        baseline = sum(base) / len(base) if base else 0.0

        def wmax(end):
            w = [p for h, p in series.items() if freeze <= h <= end]
            return max(w) if w else None
        ms, mf = wmax(end_strict), wmax(end_full)
        results.append({
            "coin": coin, "baseline": round(baseline, 2),
            "max_strict": round(ms, 2) if ms is not None else None,
            "max_full": round(mf, 2) if mf is not None else None,
            "delta_strict": round(ms - baseline, 2) if ms is not None else None,
            "delta_full": round(mf - baseline, 2) if mf is not None else None,
        })
        print(f"  {coin:8s} base{baseline:>6.1f}  막힘8h최고{(ms or 0):>6.1f}  재개일포함{(mf or 0):>6.1f}")
    except Exception as e:
        results.append({"coin": coin, "err": str(e)[:40]})
        print(f"  {coin:8s} ERR {str(e)[:40]}")

ok = [r for r in results if r.get("delta_full") is not None]
ok.sort(key=lambda x: -x["delta_full"])
json.dump(results, open("audit_0528_freeze_only.json", "w"), ensure_ascii=False, indent=2)
print("\n=== 입출막 기간 김프 펌핑 (재개 5/29) — 입출막 순효과 ===")
print(f"{'코인':<8}{'baseline':>9}{'막힘8h최고':>11}{'재개일포함':>11}")
for r in ok:
    print(f"{r['coin']:<8}{r['baseline']:>9}{r['max_strict']:>11}{r['max_full']:>11}")
errs = [r for r in results if r.get("delta_full") is None]
if errs:
    print("[실패]", ", ".join(r["coin"] for r in errs))
print(f"\n성공 {len(ok)}/27 · 저장: audit_0528_freeze_only.json + premium_series.json")
