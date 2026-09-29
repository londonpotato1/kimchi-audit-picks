#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""9/30 정기실사 탭 데이터 (D_AUDIT0930) — 빗썸 KRW ∪ 업비트 KRW 전수.

입력: audit0930_universe.json (가격 대조 검증된 CoinGecko ID·시총·유통량),
      upbit_report_0701.json (업비트 7/1 실사보고서 고객 위탁량), audit_0630_premium.json (6/30 실측)
라이브: 빗썸 gw 내부지표 5종 (build_bnb28 과 같은 경로·공식, base.apply_live_metric 재사용)
이력: 점수·갭·과거 실사(25.9/30, 12/31, 3/31)는 6/30 탭 D(없으면 data_D_0630) 에서 승계
"""
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

import base28_live_sources as live
import build_base28 as base
from build_bnb28 import inline_json

DIR = Path(__file__).resolve().parent
HISTORY = ("score", "has_gap", "gap_days", "avg_gap", "max_gap", "prev_0930", "prev_max", "audit_0331")
SOURCE_NOTE = "Bithumb observer, accumulation, purity, holders, top-share + CoinGecko markets"

type Row = dict[str, str | int | float | bool | None]


def empty_row(u: dict) -> Row:
    return {
        "coin": u["coin"], "kor_name": u["kor_name"], "score": None, "mc": None, "mc_fmt": "—",
        "has_gap": None, "gap_days": None, "avg_gap": None, "max_gap": None,
        "internal_value": None, "iv_fmt": "—", "cum_deposit": None, "net_deposit": None,
        "holders": None, "hold_pct": None, "trade_pct": None, "iv_mc_ratio": None, "bithumb_ratio": None,
        "prev_0930": None, "prev_max": None, "audit_0331": None,
        "a0630_bt": None, "a0630_bt_max": None, "a0630_bt_time": None, "a0630_bt_src": None,
        "a0630_up": None, "a0630_up_max": None, "a0630_up_time": None, "a0630_up_src": None,
        "upbit_qty": None, "upbit_ratio": None, "upbit_value": None,
        "upbit_symbol": u["coin"] if u["on_upbit"] else None,
        "on_bithumb": u["on_bithumb"], "on_upbit": u["on_upbit"], "stable": u["stable"],
        "gecko_id": u["gecko_id"], "id_status": u["id_status"],
        "bithumb_code": None, "price_krw": None, "as_of": None, "mc_as_of": None, "bithumb_as_of": None,
        "live_status": "missing" if u["on_bithumb"] else "upbit_only", "source_errors": "", "data_note": None,
        "history_note": "6/30 탭 이력 없음 (6/30 이후 상장 또는 업비트 전용)",
    }


def fill_bithumb(row: Row, u: dict, coin_type: str, ticker: dict | None, fx: float) -> None:
    coin = u["coin"]
    if ticker is None:
        row["source_errors"] = "빗썸 observer 시세 없음"
        return
    price = live.number_value(ticker.get("closePrice"), coin)
    acc = live.fetch_metric_data(f"/v1/trade/accumulation/deposit/{coin_type}-{live.MARKET_KRW}", coin)
    pur = live.fetch_metric_data(f"/v1/trade/purity/deposit/{coin_type}-{live.MARKET_KRW}", coin)
    hol = live.fetch_metric_data(f"/v1/trade/holders/{coin_type}", coin)
    hsh = live.fetch_metric_data(f"/v1/trade/top/holder/share/{coin_type}", coin)
    tsh = live.fetch_metric_data(f"/v1/trade/top/trader/share/{coin_type}", coin)
    if u["mc_usd"] and u["circulating_supply"]:
        metric = live.LiveMetric(
            coin_type=coin_type, market_cap_usd=u["mc_usd"], circulating_supply=u["circulating_supply"],
            price_krw=price,
            accumulation_deposit_amt=live.number_value(acc.get("accumulationDepositAmt"), coin),
            purity_deposit=live.int_value(pur.get("purityDeposit"), coin),
            number_of_holders=live.int_value(hol.get("numberOfHolders"), coin),
            holding_percentage=live.int_value(hsh.get("holdingPercentage"), coin),
            trading_percentage=live.int_value(tsh.get("tradingPercentage"), coin),
            bithumb_timestamp=live.text_value(acc.get("timestamp"), coin),
            coingecko_updated_at=u["cg_last_updated"] or "",
            source_note=SOURCE_NOTE,
        )
        base.apply_live_metric(row, metric, fx)
        row["live_status"] = "complete"
        return
    # 가격 대조를 통과한 CoinGecko ID 가 없으면 시총 파생값(빗썸비중·빗썸가치/MC)은 비워 둔다
    amt = live.number_value(acc.get("accumulationDepositAmt"), coin)
    iv = round(amt * price)
    stamp = live.text_value(acc.get("timestamp"), coin)
    row.update({
        "internal_value": iv, "iv_fmt": base.fmt_krw(iv), "cum_deposit": round(amt),
        "net_deposit": live.int_value(pur.get("purityDeposit"), coin),
        "holders": live.int_value(hol.get("numberOfHolders"), coin),
        "hold_pct": live.int_value(hsh.get("holdingPercentage"), coin),
        "trade_pct": live.int_value(tsh.get("tradingPercentage"), coin),
        "bithumb_code": coin_type, "price_krw": price, "bithumb_as_of": stamp, "as_of": stamp[:10],
        "live_status": "partial", "data_note": "CoinGecko ID 가격 대조 실패 — 시총·유통량 파생값 없음",
    })


def build_rows() -> list[Row]:
    universe = json.loads((DIR / "audit0930_universe.json").read_text())
    upbit_qty: dict[str, int] = json.loads((DIR / "upbit_report_0701.json").read_text())
    meas = {(r["exchange"], r["coin"]): r for r in json.loads((DIR / "audit_0630_premium.json").read_text())
            if "delta" in r}
    history = {r["coin"]: r for r in json.loads((DIR / "data_D_0630.json").read_text())}
    history.update({str(r["coin"]): r for r in base.parse_const_array((DIR / "index.html").read_text(), "D")})
    bithumb_syms = [u["coin"] for u in universe if u["on_bithumb"]]
    states = live.fetch_coin_states(bithumb_syms)
    tickers = live.fetch_observer_tickers()
    fx = live.fetch_fx_usd_krw()

    # 같은 코인이 거래소마다 심볼만 다른 경우 (빗썸 MET = 업비트 MET2 등) 한 행으로 합친다
    by_coin = {u["coin"]: u for u in universe}
    bithumb_by_gid = {u["gecko_id"]: u["coin"] for u in universe if u["on_bithumb"] and u["gecko_id"]}
    upbit_alias = {bithumb_by_gid[u["gecko_id"]]: u["coin"] for u in universe
                   if u["on_upbit"] and not u["on_bithumb"] and u["gecko_id"] in bithumb_by_gid}

    rows: list[Row] = []
    for u in universe:
        if u["stable"] or u["coin"] in upbit_alias.values():
            continue
        row = empty_row(u)
        coin = u["coin"]
        up_sym = upbit_alias.get(coin, coin if u["on_upbit"] else None)
        row.update({"on_upbit": up_sym is not None, "upbit_symbol": up_sym})
        if coin in history:
            row.update({k: history[coin].get(k) for k in HISTORY})
            row["history_note"] = "점수·갭·과거 실사는 6/30 탭 이력 승계"
        for key, prefix in ((("bithumb", coin), "a0630_bt"), (("upbit", up_sym), "a0630_up")):
            m = meas.get(key)
            if m:
                row.update({prefix: m["delta"], f"{prefix}_max": m["max_prem"],
                            f"{prefix}_time": m["max_time"], f"{prefix}_src": m["source"]})
        if u["on_bithumb"]:
            try:
                fill_bithumb(row, u, states[coin][0], tickers.get(states[coin][0]), fx)
            except (live.LiveSourceError, base.BuildError, ValueError, OverflowError) as e:
                row["source_errors"] = str(e)
        if up_sym in upbit_qty:
            qty = upbit_qty[up_sym]
            row["upbit_qty"] = qty
            if u["circulating_supply"]:
                row["upbit_ratio"] = qty / u["circulating_supply"]
            if by_coin[up_sym]["price_krw_upbit"]:
                row["upbit_value"] = round(qty * by_coin[up_sym]["price_krw_upbit"])
        rows.append(row)
    validate(rows)
    return rows


def validate(rows: list[Row]) -> None:
    coins = [r["coin"] for r in rows]
    if len(coins) != len(set(coins)):
        raise SystemExit("심볼 중복")
    schema = frozenset(rows[0])
    for r in rows:
        if frozenset(r) != schema:
            raise SystemExit(f"{r['coin']}: 스키마 불일치")
        for k, v in r.items():
            if isinstance(v, float) and not math.isfinite(v):
                raise SystemExit(f"{r['coin']}.{k}: 유한하지 않은 값")


def embed(html: str, rows: list[Row]) -> str:
    updated, n = re.subn(r"const D_AUDIT0930=\[.*?\];", lambda _m: f"const D_AUDIT0930={inline_json(rows)};",
                         html, count=1)
    if n != 1:
        raise SystemExit("index.html D_AUDIT0930 삽입 지점 없음")
    return updated


def main() -> None:
    rows = build_rows()
    (DIR / "data_audit0930.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    html = (DIR / "index.html").read_text(encoding="utf-8")
    (DIR / "index.html").write_text(embed(html, rows), encoding="utf-8")
    st: dict[str, int] = {}
    for r in rows:
        st[str(r["live_status"])] = st.get(str(r["live_status"]), 0) + 1
    print(f"D_AUDIT0930 {len(rows)}종 · {st} · {datetime.now(UTC).astimezone():%m-%d %H:%M}")
    for r in rows:
        if r["on_bithumb"] and r["live_status"] == "missing":
            print(f"  {r['coin']}: {r['source_errors']}")


if __name__ == "__main__":
    main()
