#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# How to run:
#   uv run build_bnb28.py

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib import parse

import base28_live_sources as live
import build_base28 as base
from bnb28_event import ASSETS, GECKO_ID, KOR_NAME, OFFICIAL_SYMBOLS, EventAsset

__all__ = (
    "ASSETS", "GECKO_ID", "KOR_NAME", "OFFICIAL_SYMBOLS", "EventAsset",
    "LiveMetric", "build_rows", "embed_rows", "inline_json",
)
LiveMetric = live.LiveMetric
DIR = Path(__file__).resolve().parent
HISTORY_FIELDS = (
    "score", "has_gap", "gap_days", "avg_gap", "max_gap", "prev_max",
    "prev_avg", "prev_0930", "audit_0331",
)
BITHUMB_TIMESTAMP: Final = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
COINGECKO_TIMESTAMP: Final = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"
)

type Scalar = str | int | float | bool | None
type Row = dict[str, Scalar]


@dataclass(frozen=True, slots=True)
class FetchResult:
    metrics: dict[str, live.LiveMetric]
    fx_usd_krw: float | None
    errors: dict[str, str]
    fetched_at: str


def history_rows(index_path: Path, history_path: Path) -> dict[str, Row]:
    dashboard = base.parse_const_array(index_path.read_text(encoding="utf-8"), "D")
    dashboard_symbols = {base.string_field(row, "coin") for row in dashboard}
    april = base.parse_const_array(history_path.read_text(encoding="utf-8"), "D")
    valid: dict[str, Row] = {}
    for old_row in april:
        symbol = base.string_field(old_row, "coin")
        available = symbol in dashboard_symbols or symbol in {"MONKY", "PURSE"}
        if symbol in OFFICIAL_SYMBOLS and available:
            valid[symbol] = {key: old_row.get(key) for key in HISTORY_FIELDS}
    return valid


def empty_row(symbol: str, legacy: Row | None, fetched_at: str) -> Row:
    row: Row = {
        "coin": symbol, "score": None, "mc": None, "mc_fmt": "—",
        "has_gap": None, "gap_days": None, "avg_gap": None, "max_gap": None,
        "internal_value": None, "iv_fmt": "—", "cum_deposit": None,
        "net_deposit": None, "holders": None, "hold_pct": None,
        "trade_pct": None, "iv_mc_ratio": None, "bithumb_ratio": None,
        "prev_max": None, "prev_avg": None, "prev_0930": None,
        "audit_0331": None, "external": legacy is None, "kor_name": KOR_NAME[symbol],
        "gecko_id": GECKO_ID[symbol], "event_notice": "1654569",
        "event_at": "2026-08-25 09:00 KST", "history_source": None,
        "history_note": "승계 가능한 과거 BNB 이벤트 행 없음",
        "fetched_at": fetched_at, "live_status": "missing",
        "source_provenance": "Bithumb + CoinGecko markets",
        "source_errors": "완전한 현재 지표 없음",
        "bithumb_code": None, "price_krw": None, "as_of": None,
        "mc_as_of": None, "bithumb_as_of": None, "data_note": None,
    }
    if legacy is not None:
        row.update(legacy)
        row["history_source"] = "bnb_freeze_2026-04-28"
        row["history_note"] = "2026-04-28 BNB 이벤트의 점수/갭 이력"
    return row


def validate_timestamp(value: str, pattern: re.Pattern[str], source: str) -> None:
    if pattern.fullmatch(value) is None:
        raise live.LiveSourceError(f"{source} timestamp 형식 오류")
    try:
        _ = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise live.LiveSourceError(f"{source} timestamp 값 오류") from exc


def apply_live(row: Row, metric: live.LiveMetric, fx_usd_krw: float) -> None:
    coin = row["coin"]
    if not isinstance(coin, str):
        raise live.LiveSourceError("live 행 coin 문자열 누락")
    positive = {
        "market_cap_usd": metric.market_cap_usd,
        "circulating_supply": metric.circulating_supply,
        "price_krw": metric.price_krw,
        "fx_usd_krw": fx_usd_krw,
    }
    for name, value in positive.items():
        if not math.isfinite(float(value)) or value <= 0:
            raise live.LiveSourceError(f"{coin}: {name}가 유한한 양수가 아님")
    if not math.isfinite(float(metric.accumulation_deposit_amt)) or metric.accumulation_deposit_amt < 0:
        raise live.LiveSourceError(f"{coin}: accumulation_deposit_amt가 유한한 음이 아닌 값이 아님")
    if not math.isfinite(float(metric.number_of_holders)) or metric.number_of_holders < 0:
        raise live.LiveSourceError(f"{coin}: number_of_holders가 유한한 음이 아닌 값이 아님")
    for name, value in {
        "holding_percentage": metric.holding_percentage,
        "trading_percentage": metric.trading_percentage,
    }.items():
        if not math.isfinite(float(value)) or not 0 <= value <= 100:
            raise live.LiveSourceError(f"{coin}: {name}가 유한한 0..100 값이 아님")
    validate_timestamp(metric.bithumb_timestamp, BITHUMB_TIMESTAMP, "Bithumb")
    validate_timestamp(metric.coingecko_updated_at, COINGECKO_TIMESTAMP, "CoinGecko")
    shared_row: base.Row = {"coin": coin}
    _ = base.apply_live_metric(shared_row, metric, fx_usd_krw)
    row.update(shared_row)
    row.update({
        "live_status": "complete", "source_provenance": metric.source_note,
        "source_errors": "", "data_note": (
            f"{metric.bithumb_timestamp} KST Bithumb 내부지표 + "
            f"{metric.coingecko_updated_at} CoinGecko MC. 점수/갭은 4월 이력일 수 있음."
        ),
    })


def validate_rows(rows: list[Row]) -> None:
    if [row.get("coin") for row in rows] != OFFICIAL_SYMBOLS:
        raise live.LiveSourceError("공지 순서 또는 종목 수 불일치")
    if len(set(OFFICIAL_SYMBOLS)) != 28:
        raise live.LiveSourceError("공지 심볼 중복")
    schema = frozenset(rows[0])
    for row in rows:
        if frozenset(row) != schema:
            raise live.LiveSourceError(f"{row['coin']}: 출력 스키마 불일치")
        if any(isinstance(value, float) and not math.isfinite(value) for value in row.values()):
            raise live.LiveSourceError(f"{row['coin']}: 유한하지 않은 숫자")


def build_rows(
    index_path: Path = DIR / "index.html",
    history_path: Path = DIR / "bnb_freeze.html",
    live_metrics: dict[str, live.LiveMetric] | None = None,
    fx_usd_krw: float | None = None,
    source_errors: dict[str, str] | None = None,
    fetched_at: str | None = None,
) -> list[Row]:
    if live_metrics is None:
        fetched = fetch_current()
        live_metrics, fx_usd_krw = fetched.metrics, fetched.fx_usd_krw
        source_errors, fetched_at = fetched.errors, fetched.fetched_at
    timestamp = fetched_at or datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    errors = source_errors or {}
    legacy_by_symbol = history_rows(index_path, history_path)
    rows: list[Row] = []
    for symbol in OFFICIAL_SYMBOLS:
        row = empty_row(symbol, legacy_by_symbol.get(symbol), timestamp)
        metric = live_metrics.get(symbol)
        if metric is not None and fx_usd_krw is not None:
            try:
                apply_live(row, metric, fx_usd_krw)
            except (live.LiveSourceError, base.BuildError, OverflowError, ValueError) as exc:
                row["source_errors"] = str(exc)
        elif symbol in errors:
            row["source_errors"] = errors[symbol]
        rows.append(row)
    validate_rows(rows)
    return rows


def fetch_markets() -> tuple[dict[str, dict[str, live.JsonValue]], str | None]:
    ids = ",".join(parse.quote(GECKO_ID[symbol]) for symbol in OFFICIAL_SYMBOLS)
    try:
        url = f"{live.CG}/coins/markets?vs_currency=usd&ids={ids}&per_page=250&page=1"
        payload = live.fetch_json(url, "CoinGecko BNB28", 2)
        markets: dict[str, dict[str, live.JsonValue]] = {}
        for raw_market in live.json_array(payload, "CoinGecko BNB28"):
            market = live.json_object(raw_market, "CoinGecko BNB28 market")
            if isinstance(market.get("id"), str):
                markets[live.text_value(market["id"], "CoinGecko market id")] = dict(market)
        return markets, None
    except live.LiveSourceError as exc:
        return {}, str(exc)


def fetch_current() -> FetchResult:
    fetched_at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    errors: dict[str, str] = {}
    try:
        coin_types = live.fetch_coin_type_map(OFFICIAL_SYMBOLS)
        tickers = live.fetch_observer_tickers()
        fx_usd_krw: float | None = live.fetch_fx_usd_krw()
    except live.LiveSourceError as exc:
        errors = {symbol: f"Bithumb: {exc}" for symbol in OFFICIAL_SYMBOLS}
        return FetchResult({}, None, errors, fetched_at)
    markets, market_error = fetch_markets()
    metrics: dict[str, live.LiveMetric] = {}
    for symbol in OFFICIAL_SYMBOLS:
        ticker = tickers.get(coin_types[symbol])
        market = markets.get(GECKO_ID[symbol])
        if ticker is None or market is None:
            reasons = (["Bithumb observer ticker 누락"] if ticker is None else [])
            if market is None:
                reasons.append(market_error or "CoinGecko market 누락")
            errors[symbol] = "; ".join(reasons)
            continue
        coin_type = coin_types[symbol]
        try:
            acc = live.fetch_metric_data(
                f"/v1/trade/accumulation/deposit/{coin_type}-{live.MARKET_KRW}", symbol
            )
            purity = live.fetch_metric_data(
                f"/v1/trade/purity/deposit/{coin_type}-{live.MARKET_KRW}", symbol
            )
            holders = live.fetch_metric_data(f"/v1/trade/holders/{coin_type}", symbol)
            holder_share = live.fetch_metric_data(f"/v1/trade/top/holder/share/{coin_type}", symbol)
            trader_share = live.fetch_metric_data(f"/v1/trade/top/trader/share/{coin_type}", symbol)
            metrics[symbol] = live.LiveMetric(
                coin_type=coin_type,
                market_cap_usd=live.number_value(market.get("market_cap"), symbol),
                circulating_supply=live.number_value(market.get("circulating_supply"), symbol),
                price_krw=live.number_value(ticker.get("closePrice"), symbol),
                accumulation_deposit_amt=live.number_value(acc.get("accumulationDepositAmt"), symbol),
                purity_deposit=live.int_value(purity.get("purityDeposit"), symbol),
                number_of_holders=live.int_value(holders.get("numberOfHolders"), symbol),
                holding_percentage=live.int_value(holder_share.get("holdingPercentage"), symbol),
                trading_percentage=live.int_value(trader_share.get("tradingPercentage"), symbol),
                bithumb_timestamp=live.text_value(acc.get("timestamp"), symbol),
                coingecko_updated_at=live.text_value(market.get("last_updated"), symbol),
                source_note=(
                    "Bithumb observer/accumulation/purity/holders/top-share + CoinGecko markets"
                ),
            )
        except (live.LiveSourceError, ValueError) as exc:
            errors[symbol] = str(exc)
    return FetchResult(metrics, fx_usd_krw, errors, fetched_at)


def inline_json(rows: list[Row]) -> str:
    return (
        json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def embed_rows(html: str, rows: list[Row]) -> str:
    replacement = f"const D_BNB28={inline_json(rows)};"
    updated, count = re.subn(
        r"const D_BNB28=\[.*?\];", lambda _match: replacement, html, count=1
    )
    if count != 1:
        raise live.LiveSourceError("index.html D_BNB28 삽입 지점 누락")
    return updated


def write_outputs(rows: list[Row], *, json_path: Path = DIR / "data_bnb28.json",
                  html_path: Path = DIR / "index.html") -> None:
    _ = json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html = html_path.read_text(encoding="utf-8")
    updated = embed_rows(html, rows)
    _ = html_path.write_text(updated, encoding="utf-8")


def main() -> None:
    rows = build_rows()
    write_outputs(rows)
    complete = sum(row["live_status"] == "complete" for row in rows)
    print(f"BNB28 {len(rows)}종 저장 · live complete {complete}/28")
    for row in rows:
        if row["live_status"] != "complete":
            print(f"{row['coin']}: {row['source_errors']}")


if __name__ == "__main__":
    main()
