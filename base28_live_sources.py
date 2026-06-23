#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Final, Union, cast
from urllib import error, parse, request


BITHUMB_GW: Final = "https://gw.bithumb.com"
CG: Final = "https://api.coingecko.com/api/v3"
MARKET_KRW: Final = "C0100"
OBSERVER_LISTS: Final = json.dumps(
    {"ticker": {"coinType": "ALL", "tickType": "MID"}, "transaction": {"limit": 31}},
    separators=(",", ":"),
)
HEADERS: Final = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.bithumb.com/",
    "Accept": "application/json, text/javascript, */*",
}


MetricNumber = Union[int, float]
Scalar = Union[str, int, float, bool]
JsonValue = Union[Scalar, None, list["JsonValue"], dict[str, "JsonValue"]]


class LiveSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveMetric:
    coin_type: str
    market_cap_usd: MetricNumber
    circulating_supply: MetricNumber
    price_krw: MetricNumber
    accumulation_deposit_amt: MetricNumber
    purity_deposit: int
    number_of_holders: int
    holding_percentage: int
    trading_percentage: int
    bithumb_timestamp: str
    coingecko_updated_at: str
    source_note: str


def json_object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise LiveSourceError(f"{context}: JSON 객체가 아닙니다")
    return value


def json_array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise LiveSourceError(f"{context}: JSON 배열이 아닙니다")
    return value


def text_value(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        raise LiveSourceError(f"{context}: 문자열 값이 아닙니다")
    return value


def number_value(value: JsonValue, context: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if cleaned and cleaned != "-":
            return float(cleaned)
    raise LiveSourceError(f"{context}: 숫자 값이 아닙니다 ({value!r})")


def int_value(value: JsonValue, context: str) -> int:
    return int(round(number_value(value, context)))


def read_response_data(payload: JsonValue, context: str) -> dict[str, JsonValue]:
    obj = json_object(payload, context)
    status = obj.get("status")
    if status not in (200, "200", "0000"):
        raise LiveSourceError(
            f"{context}: API status={status} code={obj.get('code')} message={obj.get('message')}"
        )
    return json_object(obj.get("data"), f"{context}.data")


def fetch_json(url: str, context: str, retries: int = 3) -> JsonValue:
    for attempt in range(1, retries + 1):
        try:
            req = request.Request(url, headers=HEADERS)
            with request.urlopen(req, timeout=30) as response:
                return cast(JsonValue, json.loads(response.read().decode("utf-8")))
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries:
                raise LiveSourceError(f"{context}: API 호출 실패 ({exc})") from exc
            time.sleep(1.5 * attempt)
    raise LiveSourceError(f"{context}: API 호출 실패")


def fetch_coin_type_map(symbols: list[str]) -> dict[str, str]:
    payload = fetch_json(f"{BITHUMB_GW}/exchange/v1/comn/intro", "Bithumb intro")
    data = read_response_data(payload, "Bithumb intro")
    coin_list = json_array(data.get("coinList"), "Bithumb intro.coinList")
    by_symbol: dict[str, str] = {}
    for raw_coin in coin_list:
        coin = json_object(raw_coin, "Bithumb intro.coin")
        symbol = coin.get("coinSymbol")
        coin_type = coin.get("coinType")
        if isinstance(symbol, str) and isinstance(coin_type, str):
            by_symbol[symbol] = coin_type
    missing = [symbol for symbol in symbols if symbol not in by_symbol]
    if missing:
        raise LiveSourceError(f"Bithumb coinType 누락: {missing}")
    return {symbol: by_symbol[symbol] for symbol in symbols}


def fetch_fx_usd_krw() -> float:
    payload = fetch_json(f"{BITHUMB_GW}/exchange/v1/comn/exrate", "Bithumb exrate")
    data = read_response_data(payload, "Bithumb exrate")
    rates = json_array(data.get("currencyRateList"), "Bithumb exrate.currencyRateList")
    for raw_rate in rates:
        rate = json_object(raw_rate, "Bithumb exrate.rate")
        if rate.get("currency") == "USD":
            return number_value(rate.get("rate"), "Bithumb USD/KRW")
    raise LiveSourceError("Bithumb USD/KRW 환율 누락")


def fetch_coingecko_markets(
    symbols: list[str],
    gecko_ids: dict[str, str],
) -> dict[str, dict[str, JsonValue]]:
    ids_csv = ",".join(parse.quote(gecko_ids[symbol]) for symbol in symbols)
    url = f"{CG}/coins/markets?vs_currency=usd&ids={ids_csv}&per_page=250&page=1"
    payload = fetch_json(url, "CoinGecko markets")
    markets = json_array(payload, "CoinGecko markets")
    by_id: dict[str, dict[str, JsonValue]] = {}
    for raw_market in markets:
        market = json_object(raw_market, "CoinGecko market")
        market_id = market.get("id")
        if isinstance(market_id, str):
            by_id[market_id] = market
    missing = [symbol for symbol, gecko_id in gecko_ids.items() if gecko_id not in by_id]
    if missing:
        raise LiveSourceError(f"CoinGecko markets 누락: {missing}")
    return by_id


def fetch_observer_tickers() -> dict[str, dict[str, JsonValue]]:
    lists = parse.quote(OBSERVER_LISTS)
    url = (
        f"{BITHUMB_GW}/observer/trade/v1/info?coin=ALL&crncCd={MARKET_KRW}"
        f"&crncCd=C0101&lists={lists}&type=custom"
    )
    payload = fetch_json(url, "Bithumb observer")
    data = read_response_data(payload, "Bithumb observer")
    krw = json_object(data.get(MARKET_KRW), "Bithumb observer.C0100")
    ticker = json_object(krw.get("ticker"), "Bithumb observer.C0100.ticker")
    return {key: json_object(value, f"Bithumb ticker.{key}") for key, value in ticker.items()}


def fetch_metric_data(path: str, context: str) -> dict[str, JsonValue]:
    payload = fetch_json(f"{BITHUMB_GW}/exchange{path}", context)
    return read_response_data(payload, context)


def fetch_live_metrics(
    symbols: list[str],
    gecko_ids: dict[str, str],
) -> tuple[dict[str, LiveMetric], float]:
    coin_types = fetch_coin_type_map(symbols)
    markets = fetch_coingecko_markets(symbols, gecko_ids)
    tickers = fetch_observer_tickers()
    fx_usd_krw = fetch_fx_usd_krw()
    metrics: dict[str, LiveMetric] = {}
    for symbol in symbols:
        coin_type = coin_types[symbol]
        market = markets[gecko_ids[symbol]]
        ticker = tickers.get(coin_type)
        if ticker is None:
            raise LiveSourceError(f"{symbol}: Bithumb observer ticker 누락 ({coin_type})")
        accumulation = fetch_metric_data(
            f"/v1/trade/accumulation/deposit/{coin_type}-{MARKET_KRW}",
            f"{symbol} accumulation",
        )
        purity = fetch_metric_data(
            f"/v1/trade/purity/deposit/{coin_type}-{MARKET_KRW}",
            f"{symbol} purity",
        )
        holders = fetch_metric_data(f"/v1/trade/holders/{coin_type}", f"{symbol} holders")
        holder_share = fetch_metric_data(
            f"/v1/trade/top/holder/share/{coin_type}",
            f"{symbol} top holder",
        )
        trader_share = fetch_metric_data(
            f"/v1/trade/top/trader/share/{coin_type}",
            f"{symbol} top trader",
        )
        price_krw = number_value(ticker.get("closePrice"), f"{symbol} observer closePrice")
        if price_krw <= 0:
            raise LiveSourceError(f"{symbol}: Bithumb observer price가 0 이하입니다")
        metrics[symbol] = LiveMetric(
            coin_type=coin_type,
            market_cap_usd=number_value(market.get("market_cap"), f"{symbol} market_cap"),
            circulating_supply=number_value(market.get("circulating_supply"), f"{symbol} circulating_supply"),
            price_krw=price_krw,
            accumulation_deposit_amt=number_value(
                accumulation.get("accumulationDepositAmt"),
                f"{symbol} accumulationDepositAmt",
            ),
            purity_deposit=int_value(purity.get("purityDeposit"), f"{symbol} purityDeposit"),
            number_of_holders=int_value(holders.get("numberOfHolders"), f"{symbol} numberOfHolders"),
            holding_percentage=int_value(
                holder_share.get("holdingPercentage"),
                f"{symbol} holdingPercentage",
            ),
            trading_percentage=int_value(
                trader_share.get("tradingPercentage"),
                f"{symbol} tradingPercentage",
            ),
            bithumb_timestamp=text_value(accumulation.get("timestamp"), f"{symbol} bithumb timestamp"),
            coingecko_updated_at=text_value(market.get("last_updated"), f"{symbol} CoinGecko last_updated"),
            source_note="CoinGecko markets + Bithumb observer/accumulation/purity/holders/top-share",
        )
        time.sleep(0.04)
    return metrics, fx_usd_krw
