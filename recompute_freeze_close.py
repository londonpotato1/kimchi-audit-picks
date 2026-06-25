#!/usr/bin/env python3
"""
입출막 김프를 '종가(close)' 기준으로 재계산 (고가 wick 허수 제거).
prem_close = (prem_high+100) × (close/high) - 100
  premium_series.json (시각별 prem_high, 글로벌 캐시) 재활용 + 빗썸 close/high 만 추가.
"""
import json, urllib.request, datetime, time

UTC = datetime.timezone.utc
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def bt_ch(sym):
    """sym_KRW 1h → {hour: (close, high)}"""
    req = urllib.request.Request(f"https://api.bithumb.com/public/candlestick/{sym}_KRW/1h", headers=UA)
    d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    if d.get("status") != "0000":
        return None
    return {int(int(r[0]) // 3600000 * 3600): (float(r[2]), float(r[3])) for r in d["data"]}


def ep(y, mo, d, h):
    return int(datetime.datetime(y, mo, d, h, tzinfo=UTC).timestamp())


freeze = ep(2026, 5, 28, 10)        # 5/28 19:00 KST
end_strict = ep(2026, 5, 28, 18)    # 5/29 03:00 KST
end_full = ep(2026, 5, 29, 15)      # 5/30 00:00 KST
base_start = freeze - 24 * 3600

series = json.load(open("premium_series.json"))   # {coin: {str(hour): prem_high}}
results = []
for coin, ph_map in series.items():
    bt = bt_ch(coin)
    time.sleep(0.1)
    if not bt:
        results.append({"coin": coin, "err": "bithumb"}); continue
    pc = {}
    for hs, ph in ph_map.items():
        h = int(hs)
        if h in bt:
            close, high = bt[h]
            if high > 0:
                pc[h] = (ph + 100) * (close / high) - 100
    base = [p for h, p in pc.items() if base_start <= h < freeze]
    baseline = sum(base) / len(base) if base else 0.0
    ws = [p for h, p in pc.items() if freeze <= h <= end_strict]
    wf = [p for h, p in pc.items() if freeze <= h <= end_full]
    ms = max(ws) if ws else None
    mf = max(wf) if wf else None
    results.append({
        "coin": coin, "baseline": round(baseline, 2),
        "max_strict": round(ms, 2) if ms is not None else None,
        "max_full": round(mf, 2) if mf is not None else None,
        "delta_strict": round(ms - baseline, 2) if ms is not None else None,
        "delta_full": round(mf - baseline, 2) if mf is not None else None,
    })

ok = sorted([r for r in results if r.get("max_full") is not None], key=lambda x: -x["max_full"])
json.dump(results, open("audit_0528_freeze_close.json", "w"), ensure_ascii=False, indent=2)
print("=== 입출막 기간 김프 (종가 기준, wick 제거) — 재개 5/29 ===")
print(f"{'코인':<8}{'baseline':>9}{'막힘8h최고':>11}{'재개일포함':>11}")
for r in ok:
    print(f"{r['coin']:<8}{r['baseline']:>9}{r['max_strict']:>11}{r['max_full']:>11}")
print(f"\n성공 {len(ok)}/27 · 저장: audit_0528_freeze_close.json")
