#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Final, Optional, Union, cast

from base28_live_sources import LiveMetric, fetch_live_metrics


DIR: Final = Path(__file__).resolve().parent
INDEX: Final = DIR / "index.html"

BASE28_LIST: Final = [
    "AERO", "AVNT", "AWE", "B3", "BRETT", "C", "CARV", "CTR", "EDGE",
    "ELSA", "FLOCK", "GPS", "HOME", "KAITO", "MIRA", "OPG", "PROMPT",
    "RECALL", "SAPIEN", "SIGN", "THQ", "TOSHI", "TOWNS", "TRUST", "UP",
    "VIRTUAL", "VVV", "ZORA",
]

KOR_NAME: Final = {
    "AERO": "에어로드롬파이낸스",
    "AVNT": "아반티스",
    "AWE": "에이더블유이",
    "B3": "비쓰리",
    "BRETT": "브렛",
    "C": "체인베이스토큰",
    "CARV": "카브",
    "CTR": "시트레아",
    "EDGE": "디피니티브",
    "ELSA": "헤이엘사",
    "FLOCK": "플록",
    "GPS": "고플러스",
    "HOME": "디파이앱",
    "KAITO": "카이토",
    "MIRA": "미라",
    "OPG": "오픈그라디언트",
    "PROMPT": "웨이파인더",
    "RECALL": "리콜",
    "SAPIEN": "사피엔",
    "SIGN": "사인",
    "THQ": "테오릭",
    "TOSHI": "토시",
    "TOWNS": "타운즈",
    "TRUST": "인튜이션",
    "UP": "슈퍼폼",
    "VIRTUAL": "버추얼프로토콜",
    "VVV": "베니스토큰",
    "ZORA": "조라",
}

GECKO_ID_FALLBACK: Final = {"CTR": "citrea"}
REQUIRED_FIELDS: Final = (
    "coin", "score", "mc", "mc_fmt", "has_gap", "gap_days", "avg_gap",
    "max_gap", "internal_value", "iv_fmt", "cum_deposit", "net_deposit",
    "holders", "hold_pct", "trade_pct", "iv_mc_ratio", "bithumb_ratio",
    "prev_max", "prev_avg", "prev_0930", "audit_0331", "external",
    "kor_name", "gecko_id",
)

Number = Union[int, float]
RowValue = Union[str, int, float, bool]
JsonValue = Union[RowValue, None, list["JsonValue"], dict[str, "JsonValue"]]
Row = dict[str, RowValue]


class BuildError(RuntimeError):
    pass


def fmt_mc(value: Number) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.0f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def fmt_krw(value: Number) -> str:
    if value <= 0:
        return "—"
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.1f}조"
    if value >= 100_000_000:
        return f"{round(value / 100_000_000)}억"
    return f"{round(value / 10_000)}만"


def coerce_rows(raw_rows: JsonValue, source_name: str) -> list[Row]:
    if not isinstance(raw_rows, list):
        raise BuildError(f"{source_name} 값이 배열이 아닙니다")
    rows: list[Row] = []
    for index, raw_row in enumerate(raw_rows, start=1):
        if not isinstance(raw_row, dict):
            raise BuildError(f"{source_name}[{index}] 값이 객체가 아닙니다")
        row: Row = {}
        for key, value in raw_row.items():
            if not isinstance(value, (str, int, float, bool)):
                raise BuildError(f"{source_name}[{index}].{key} 값이 렌더링 가능한 원시값이 아닙니다")
            row[key] = value
        rows.append(row)
    return rows


def string_field(row: Row, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        coin = row.get("coin", "UNKNOWN")
        raise BuildError(f"{coin}: {key} 문자열 필드가 아닙니다")
    return value


def parse_const_array(html_text: str, name: str) -> list[Row]:
    match = re.search(rf"const\s+{name}\s*=\s*(\[.*?\])\s*;", html_text, re.DOTALL)
    if match is None:
        raise BuildError(f"{name} 배열을 찾지 못했습니다")
    raw_rows = cast(JsonValue, json.loads(match.group(1)))
    return coerce_rows(raw_rows, name)


def gecko_ids_from_sources(source_by_symbol: dict[str, Row]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for symbol in BASE28_LIST:
        source = source_by_symbol.get(symbol)
        if source is not None:
            ids[symbol] = string_field(source, "gecko_id")
            continue
        fallback = GECKO_ID_FALLBACK.get(symbol)
        if fallback is None:
            raise BuildError(f"{symbol}: CoinGecko ID 없음")
        ids[symbol] = fallback
    return ids


def load_rows(path: Path) -> list[Row]:
    raw_rows = cast(JsonValue, json.loads(path.read_text(encoding="utf-8")))
    return coerce_rows(raw_rows, path.name)


def make_external_row(symbol: str, gecko_id: str) -> Row:
    return {
        "coin": symbol,
        "score": 0,
        "mc": 0,
        "mc_fmt": "—",
        "has_gap": False,
        "gap_days": 0,
        "avg_gap": 0,
        "max_gap": 0,
        "internal_value": 0,
        "iv_fmt": "—",
        "cum_deposit": 0,
        "net_deposit": 0,
        "holders": 0,
        "hold_pct": 0,
        "trade_pct": 0,
        "iv_mc_ratio": 0,
        "bithumb_ratio": 0,
        "prev_max": 0,
        "prev_avg": 0,
        "prev_0930": 0,
        "audit_0331": 0,
        "external": True,
        "kor_name": KOR_NAME[symbol],
        "gecko_id": gecko_id,
    }


def history_row(row: Row, symbol: str) -> Row:
    next_row = dict(row)
    next_row["coin"] = symbol
    next_row["kor_name"] = KOR_NAME[symbol]
    _ = next_row.setdefault("external", False)
    for key in REQUIRED_FIELDS:
        if key not in next_row:
            raise BuildError(f"{symbol}: {key} 누락")
    return next_row


def apply_live_metric(row: Row, metric: LiveMetric, fx_usd_krw: float) -> Row:
    symbol = string_field(row, "coin")
    if metric.market_cap_usd <= 0:
        raise BuildError(f"{symbol}: market_cap_usd가 0 이하입니다")
    if metric.circulating_supply <= 0:
        raise BuildError(f"{symbol}: circulating_supply가 0 이하입니다")
    internal_value = round(metric.accumulation_deposit_amt * metric.price_krw)
    row["mc"] = metric.market_cap_usd
    row["mc_fmt"] = fmt_mc(metric.market_cap_usd)
    row["internal_value"] = internal_value
    row["iv_fmt"] = fmt_krw(internal_value)
    row["cum_deposit"] = round(metric.accumulation_deposit_amt)
    row["net_deposit"] = metric.purity_deposit
    row["holders"] = metric.number_of_holders
    row["hold_pct"] = metric.holding_percentage
    row["trade_pct"] = metric.trading_percentage
    row["iv_mc_ratio"] = round(internal_value / (metric.market_cap_usd * fx_usd_krw) * 100, 1)
    row["bithumb_ratio"] = metric.accumulation_deposit_amt / metric.circulating_supply
    row["bithumb_code"] = metric.coin_type
    row["price_krw"] = metric.price_krw
    row["as_of"] = metric.bithumb_timestamp[:10]
    row["mc_as_of"] = metric.coingecko_updated_at
    row["bithumb_as_of"] = metric.bithumb_timestamp
    row["data_note"] = (
        f"{metric.bithumb_timestamp} KST Bithumb 내부지표 + "
        f"{metric.coingecko_updated_at} CoinGecko MC. "
        "점수/갭/과거 실사 컬럼은 기존 대시보드 히스토리."
    )
    return row


def build_rows(
    index_path: Path = INDEX,
    live_metrics: Optional[dict[str, LiveMetric]] = None,
    fx_usd_krw: Optional[float] = None,
) -> list[Row]:
    source_rows = parse_const_array(index_path.read_text(encoding="utf-8"), "D_BASE")
    source_by_symbol = {string_field(row, "coin"): row for row in source_rows}
    gecko_ids = gecko_ids_from_sources(source_by_symbol)
    if live_metrics is None:
        live_metrics, fx_usd_krw = fetch_live_metrics(BASE28_LIST, gecko_ids)
    if fx_usd_krw is None:
        raise BuildError("USD/KRW 환율 누락")
    rows: list[Row] = []
    for symbol in BASE28_LIST:
        source = source_by_symbol.get(symbol)
        row = history_row(source, symbol) if source is not None else make_external_row(symbol, gecko_ids[symbol])
        metric = live_metrics.get(symbol)
        if metric is None:
            raise BuildError(f"{symbol}: live metric 누락")
        rows.append(apply_live_metric(row, metric, fx_usd_krw))
    validate_rows(rows)
    return rows


def validate_rows(rows: list[Row]) -> None:
    symbols = [string_field(row, "coin") for row in rows]
    if symbols != BASE28_LIST:
        raise BuildError(f"공지 순서 불일치: {symbols}")
    for row in rows:
        coin = string_field(row, "coin")
        for key in REQUIRED_FIELDS:
            if key not in row:
                raise BuildError(f"{coin}: {key} 누락")
            value = row[key]
            if isinstance(value, float) and math.isnan(value):
                raise BuildError(f"{coin}: {key} is NaN")


def write_outputs(rows: list[Row]) -> None:
    _ = (DIR / "data_base28.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _ = (DIR / "data_base28.js.txt").write_text(
        "const D_BASE28=" + json.dumps(rows, ensure_ascii=False) + ";",
        encoding="utf-8",
    )


def main() -> None:
    rows = build_rows()
    write_outputs(rows)
    print("=== BASE 28 요약 (공지 1653817) ===")
    for row in rows:
        coin = string_field(row, "coin")
        kor_name = string_field(row, "kor_name")
        mc_fmt = string_field(row, "mc_fmt")
        marker = "신규" if coin == "CTR" else ""
        print(f"{coin:7s}{kor_name:12s}{mc_fmt:>9s} {marker}")
    print(f"저장: data_base28.json / data_base28.js.txt ({len(rows)}종)")


if __name__ == "__main__":
    main()
