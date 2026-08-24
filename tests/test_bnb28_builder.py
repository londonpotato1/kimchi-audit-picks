import math
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import base28_live_sources as live
import build_bnb28

EXPECTED_SYMBOLS = [
    "ACE", "AEON", "ASTER", "BANK", "BNB", "BSB", "C98", "CAKE",
    "COOKIE", "CYS", "D", "EDU", "FLOKI", "GMT", "HOOK", "IOST",
    "LISTA", "MONKY", "PARTI", "PUMPBTC", "PURSE", "SFP", "SOLV",
    "THE", "UB", "USD1", "XTER", "XVS",
]
ROOT = Path(__file__).resolve().parents[1]


def fake_live_metrics() -> dict[str, build_bnb28.LiveMetric]:
    return {
        symbol: build_bnb28.LiveMetric(
            coin_type=f"C{index:04d}",
            market_cap_usd=2_000_000 + index * 10_000,
            circulating_supply=1_000_000 + index * 1_000,
            price_krw=200 + index,
            accumulation_deposit_amt=20_000 + index,
            purity_deposit=-200 - index,
            number_of_holders=2_000 + index,
            holding_percentage=10 + index,
            trading_percentage=20 + index,
            bithumb_timestamp="2026-08-24 21:15:00",
            coingecko_updated_at="2026-08-24T12:14:00.000Z",
            source_note="fake established sources",
        )
        for index, symbol in enumerate(EXPECTED_SYMBOLS, start=1)
    }


def test_shared_live_source_imports_with_repository_python3() -> None:
    python3 = shutil.which("python3", path=os.defpath)
    assert python3 is not None
    result = subprocess.run(
        [python3, "-B", "-c", "import base28_live_sources"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_official_notice_order_is_exact_and_unique() -> None:
    assert build_bnb28.OFFICIAL_SYMBOLS == EXPECTED_SYMBOLS
    assert len(set(build_bnb28.OFFICIAL_SYMBOLS)) == 28


def test_legacy_fields_only_carry_from_april_bnb_rows() -> None:
    rows = build_bnb28.build_rows(
        live_metrics=fake_live_metrics(),
        fx_usd_krw=1_400.0,
        fetched_at="2026-08-24T21:15:00+09:00",
    )
    by_symbol = {str(row["coin"]): row for row in rows}

    assert by_symbol["ACE"]["score"] == 60
    assert by_symbol["ACE"]["history_source"] == "bnb_freeze_2026-04-28"
    assert by_symbol["AEON"]["score"] is None
    assert by_symbol["AEON"]["history_source"] is None
    assert by_symbol["ACE"]["event_notice"] == "1654569"
    assert "현재" not in str(by_symbol["ACE"]["history_note"])


def test_missing_live_metric_is_explicit_and_never_zero_filled() -> None:
    metrics = fake_live_metrics()
    del metrics["AEON"]
    rows = build_bnb28.build_rows(
        live_metrics=metrics,
        fx_usd_krw=1_400.0,
        source_errors={"AEON": "Bithumb: ticker unavailable; CoinGecko: 429"},
        fetched_at="2026-08-24T21:15:00+09:00",
    )
    aeon = next(row for row in rows if row["coin"] == "AEON")

    missing_fields = (
        "mc", "internal_value", "cum_deposit", "holders", "iv_mc_ratio", "bithumb_ratio"
    )
    assert all(aeon[key] is None for key in missing_fields)
    assert aeon["live_status"] == "missing"
    assert "429" in str(aeon["source_errors"])
    assert len({frozenset(row) for row in rows}) == 1


def test_invalid_live_price_is_missing_without_aborting_other_rows() -> None:
    metrics = fake_live_metrics()
    invalid_prices = {"ACE": 0.0, "AEON": -1.0, "ASTER": math.nan}
    for symbol, price in invalid_prices.items():
        metrics[symbol] = replace(metrics[symbol], price_krw=price)

    rows = build_bnb28.build_rows(
        live_metrics=metrics,
        fx_usd_krw=1_400.0,
        fetched_at="2026-08-24T21:15:00+09:00",
    )
    by_symbol = {str(row["coin"]): row for row in rows}

    for symbol in invalid_prices:
        assert by_symbol[symbol]["live_status"] == "missing"
        assert by_symbol[symbol]["internal_value"] is None
        assert "price" in str(by_symbol[symbol]["source_errors"])
    assert by_symbol["BANK"]["live_status"] == "complete"


def test_invalid_external_timestamp_marks_only_that_symbol_missing() -> None:
    metrics = fake_live_metrics()
    metrics["ACE"] = replace(
        metrics["ACE"], bithumb_timestamp='</script><img src=x onerror="boom">'
    )

    rows = build_bnb28.build_rows(
        live_metrics=metrics,
        fx_usd_krw=1_400.0,
        fetched_at="2026-08-24T21:15:00+09:00",
    )
    by_symbol = {str(row["coin"]): row for row in rows}

    assert by_symbol["ACE"]["live_status"] == "missing"
    assert "timestamp" in str(by_symbol["ACE"]["source_errors"])
    assert by_symbol["AEON"]["live_status"] == "complete"


def test_inline_json_escapes_script_breakout_text() -> None:
    metrics = fake_live_metrics()
    hostile = '</script><img src=x onerror="boom">&\u2028\u2029'
    metrics["ACE"] = replace(metrics["ACE"], source_note=hostile)
    rows = build_bnb28.build_rows(
        live_metrics=metrics,
        fx_usd_krw=1_400.0,
        fetched_at="2026-08-24T21:15:00+09:00",
    )

    serialized = build_bnb28.inline_json(rows)

    assert all(character not in serialized for character in "<>&\u2028\u2029")
    assert "\\u003c/script\\u003e" in serialized
    assert "\\u2028\\u2029" in serialized
    page = build_bnb28.embed_rows("<script>const D_BNB28=[];</script>", rows)
    assert page.lower().count("</script>") == 1
    assert "<img" not in page.lower()


def test_live_overlay_uses_base28_formula_definitions() -> None:
    metrics = fake_live_metrics()
    rows = build_bnb28.build_rows(
        live_metrics=metrics,
        fx_usd_krw=1_400.0,
        fetched_at="2026-08-24T21:15:00+09:00",
    )
    ace = rows[0]
    metric = metrics["ACE"]
    internal_value = round(metric.accumulation_deposit_amt * metric.price_krw)

    assert ace["internal_value"] == internal_value
    assert ace["bithumb_ratio"] == (
        metric.accumulation_deposit_amt / metric.circulating_supply
    )
    assert ace["iv_mc_ratio"] == round(
        internal_value / (metric.market_cap_usd * 1_400.0) * 100, 1
    )
    assert ace["net_deposit"] == metric.purity_deposit
    assert all(
        math.isfinite(value)
        for row in rows
        for value in row.values()
        if isinstance(value, float)
    )


@pytest.mark.parametrize(
    "value", [True, False, math.nan, math.inf, -math.inf, "NaN", "Infinity", "bad"]
)
def test_number_value_rejects_bool_non_finite_and_conversion_errors(
    value: live.JsonValue,
) -> None:
    with pytest.raises(live.LiveSourceError):
        _ = live.number_value(value, "synthetic")


def test_number_value_preserves_valid_comma_formatted_string() -> None:
    assert live.number_value("1,234.5", "synthetic") == 1_234.5


def test_invalid_metric_domains_are_isolated_without_changing_valid_rows() -> None:
    metrics = fake_live_metrics()
    metrics["ACE"] = replace(metrics["ACE"], market_cap_usd=math.inf)
    metrics["AEON"] = replace(metrics["AEON"], circulating_supply=math.inf)
    metrics["ASTER"] = replace(metrics["ASTER"], accumulation_deposit_amt=-1.0)
    metrics["BANK"] = replace(metrics["BANK"], accumulation_deposit_amt=math.inf)
    metrics["BNB"] = replace(metrics["BNB"], number_of_holders=-1)
    metrics["BSB"] = replace(metrics["BSB"], holding_percentage=101)
    metrics["C98"] = replace(metrics["C98"], trading_percentage=-1)

    rows = build_bnb28.build_rows(
        live_metrics=metrics,
        fx_usd_krw=1_400.0,
        fetched_at="2026-08-24T21:15:00+09:00",
    )
    by_symbol = {str(row["coin"]): row for row in rows}

    for symbol in ("ACE", "AEON", "ASTER", "BANK", "BNB", "BSB", "C98"):
        assert by_symbol[symbol]["live_status"] == "missing"
        assert by_symbol[symbol]["internal_value"] is None
    assert by_symbol["CAKE"]["live_status"] == "complete"
    assert by_symbol["CAKE"]["internal_value"] == round(
        metrics["CAKE"].accumulation_deposit_amt * metrics["CAKE"].price_krw
    )


@pytest.mark.parametrize("fx_usd_krw", [math.nan, math.inf, 0.0, -1.0])
def test_invalid_global_fx_makes_all_live_values_unavailable(fx_usd_krw: float) -> None:
    rows = build_bnb28.build_rows(
        live_metrics=fake_live_metrics(),
        fx_usd_krw=fx_usd_krw,
        fetched_at="2026-08-24T21:15:00+09:00",
    )

    assert all(row["live_status"] == "missing" for row in rows)
    assert all(row["internal_value"] is None for row in rows)
    assert all("fx_usd_krw" in str(row["source_errors"]) for row in rows)
