#!/usr/bin/env python3
"""
전체 450종(D) 실시간 풀 갱신 — 6/30 정기실사 기준.
빗썸 gw API(내부지표) + CoinGecko markets(MC/유통량) 로 갱신.
갱신: internal_value, iv_fmt, cum_deposit, net_deposit, holders, hold_pct,
      trade_pct, bithumb_ratio, iv_mc_ratio, mc, mc_fmt, price_krw, as_of
유지(과거): score, has_gap, gap_days, avg_gap, max_gap, prev_*, audit_0331
미매칭 종목은 해당 필드 기존값 유지.
"""
import json, re, time, shutil, datetime, urllib.request, urllib.error, sys
sys.path.insert(0, ".")
from base28_live_sources import (
    fetch_observer_tickers, fetch_metric_data, fetch_fx_usd_krw,
    BITHUMB_GW, HEADERS, MARKET_KRW, number_value, int_value,
)

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
ASOF = "2026-06-30"


def fmt_mc(v):
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.0f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def fmt_krw(v):
    if v <= 0: return "—"
    if v >= 1e12: return f"{v/1e12:.1f}조"
    if v >= 1e8: return f"{round(v/1e8)}억"
    return f"{round(v/1e4)}만"


def cg(url, retries=6):
    for i in range(retries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read())
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(20 * (i + 1)); continue
            if i == retries - 1: raise
            time.sleep(3)
        except Exception:
            if i == retries - 1: raise
            time.sleep(3)
    raise RuntimeError(url)


def main():
    html = open("index.html", encoding="utf-8").read()
    m = re.search(r"const D=(\[.*?\]);", html, re.DOTALL)
    D = json.loads(m.group(1))
    syms = [r["coin"] for r in D]
    gecko_map = json.load(open("gecko_map.json"))

    # 빗썸 coinType (intro)
    req = urllib.request.Request(f"{BITHUMB_GW}/exchange/v1/comn/intro", headers=HEADERS)
    coinlist = json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]["coinList"]
    coin_types = {c["coinSymbol"]: c["coinType"] for c in coinlist if c.get("coinSymbol") and c.get("coinType")}

    # observer(전체 가격) + 환율
    tickers = fetch_observer_tickers()
    fx = fetch_fx_usd_krw()
    print(f"intro/observer/fx 준비 완료 (fx={fx})")

    # CoinGecko markets (gecko 매칭종, 250 배치, missing 허용)
    gecko_syms = [s for s in syms if s in gecko_map]
    markets = {}
    for i in range(0, len(gecko_syms), 250):
        batch = gecko_syms[i:i + 250]
        ids = ",".join(gecko_map[s] for s in batch)
        data = cg(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&per_page=250&page=1")
        for mk in data:
            if isinstance(mk, dict) and mk.get("id"):
                markets[mk["id"]] = mk
        time.sleep(3)
    print(f"CoinGecko markets: {len(markets)}종 (gecko 후보 {len(gecko_syms)})")

    updated = bithumb_only = 0
    for r in D:
        s = r["coin"]
        ctype = coin_types.get(s)
        if not ctype:
            continue  # 빗썸 미매칭 → 전체 유지
        ticker = tickers.get(ctype)
        if not ticker:
            continue
        try:
            price = number_value(ticker.get("closePrice"), s)
            if price <= 0:
                continue
            acc = fetch_metric_data(f"/v1/trade/accumulation/deposit/{ctype}-{MARKET_KRW}", s)
            pur = fetch_metric_data(f"/v1/trade/purity/deposit/{ctype}-{MARKET_KRW}", s)
            hol = fetch_metric_data(f"/v1/trade/holders/{ctype}", s)
            hsh = fetch_metric_data(f"/v1/trade/top/holder/share/{ctype}", s)
            tsh = fetch_metric_data(f"/v1/trade/top/trader/share/{ctype}", s)
            acc_amt = number_value(acc.get("accumulationDepositAmt"), s)
            iv = round(acc_amt * price)
            r["internal_value"] = iv
            r["iv_fmt"] = fmt_krw(iv)
            r["cum_deposit"] = round(acc_amt)
            r["net_deposit"] = int_value(pur.get("purityDeposit"), s)
            r["holders"] = int_value(hol.get("numberOfHolders"), s)
            r["hold_pct"] = int_value(hsh.get("holdingPercentage"), s)
            r["trade_pct"] = int_value(tsh.get("tradingPercentage"), s)
            r["price_krw"] = price
            # CoinGecko (gecko 매칭 + markets 응답)
            gid = gecko_map.get(s)
            mk = markets.get(gid) if gid else None
            if mk:
                mc = mk.get("market_cap")
                cs = mk.get("circulating_supply")
                if isinstance(mc, (int, float)) and mc > 0:
                    r["mc"] = float(mc)
                    r["mc_fmt"] = fmt_mc(float(mc))
                    r["iv_mc_ratio"] = round(iv / (float(mc) * fx) * 100, 1)
                if isinstance(cs, (int, float)) and cs > 0:
                    r["bithumb_ratio"] = acc_amt / float(cs)
                r["mc_as_of"] = mk.get("last_updated", "")
            else:
                bithumb_only += 1
                if r.get("mc", 0) and r["mc"] > 0:  # mc 미갱신이어도 새 iv로 비율 재계산
                    r["iv_mc_ratio"] = round(iv / (r["mc"] * fx) * 100, 1)
            r["as_of"] = ASOF
            updated += 1
            time.sleep(0.04)
        except Exception as e:
            print(f"  {s} 스킵: {str(e)[:50]}")
            continue

    # 검증: 필드 타입/NaN
    import math
    for r in D:
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                raise SystemExit(f"{r['coin']}: {k} NaN")

    new_html = html[:m.start()] + "const D=" + json.dumps(D, ensure_ascii=False) + ";" + html[m.end():]
    shutil.copy("index.html", f"index.html.bak_{TS}")
    open("index.html", "w", encoding="utf-8").write(new_html)
    json.dump(D, open("data_D_0630.json", "w"), ensure_ascii=False, indent=2)
    print(f"\n갱신 {updated}/450종 (빗썸+CoinGecko {updated-bithumb_only}, 빗썸만 {bithumb_only}) · 백업 index.html.bak_{TS}")


if __name__ == "__main__":
    main()
