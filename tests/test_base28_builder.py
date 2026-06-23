import math
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_base28


EXPECTED_SYMBOLS: list[str] = [
    "AERO", "AVNT", "AWE", "B3", "BRETT", "C", "CARV", "CTR", "EDGE",
    "ELSA", "FLOCK", "GPS", "HOME", "KAITO", "MIRA", "OPG", "PROMPT",
    "RECALL", "SAPIEN", "SIGN", "THQ", "TOSHI", "TOWNS", "TRUST", "UP",
    "VIRTUAL", "VVV", "ZORA",
]

REQUIRED_FIELDS: set[str] = {
    "coin",
    "score",
    "mc",
    "mc_fmt",
    "has_gap",
    "gap_days",
    "avg_gap",
    "max_gap",
    "internal_value",
    "iv_fmt",
    "cum_deposit",
    "net_deposit",
    "holders",
    "hold_pct",
    "trade_pct",
    "iv_mc_ratio",
    "bithumb_ratio",
    "prev_max",
    "prev_avg",
    "prev_0930",
    "audit_0331",
    "external",
    "kor_name",
    "gecko_id",
}


def fake_live_metrics() -> dict[str, build_base28.LiveMetric]:
    metrics: dict[str, build_base28.LiveMetric] = {}
    for index, symbol in enumerate(EXPECTED_SYMBOLS, start=1):
        metrics[symbol] = build_base28.LiveMetric(
            coin_type=f"C{index:04d}",
            market_cap_usd=1_000_000 + index * 10_000,
            circulating_supply=1_000_000 + index * 1_000,
            price_krw=100 + index,
            accumulation_deposit_amt=10_000 + index,
            purity_deposit=-100 - index,
            number_of_holders=1_000 + index,
            holding_percentage=20 + index,
            trading_percentage=30 + index,
            bithumb_timestamp="2026-06-23 00:44:23",
            coingecko_updated_at="2026-06-22T15:50:00.000Z",
            source_note="fake live source",
        )
    return metrics


class Base28BuilderTest(unittest.TestCase):
    def test_notice_1653817_symbol_order_has_28_and_ctr(self):
        self.assertEqual(build_base28.BASE28_LIST, EXPECTED_SYMBOLS)
        self.assertEqual(build_base28.KOR_NAME["CTR"], "시트레아")
        self.assertEqual(EXPECTED_SYMBOLS.index("CTR"), EXPECTED_SYMBOLS.index("CARV") + 1)

    def test_base28_rows_have_render_required_fields_without_nulls(self):
        rows = build_base28.load_rows(ROOT / "data_base28.json")

        self.assertEqual([build_base28.string_field(row, "coin") for row in rows], EXPECTED_SYMBOLS)
        for row in rows:
            coin = build_base28.string_field(row, "coin")
            with self.subTest(coin=coin):
                missing = REQUIRED_FIELDS - row.keys()
                self.assertFalse(missing)
                for key in REQUIRED_FIELDS:
                    self.assertIsNotNone(row[key], f"{coin} {key} must not be None")
                for key in ("mc", "internal_value", "iv_mc_ratio", "bithumb_ratio"):
                    value = row[key]
                    self.assertIsInstance(value, (int, float))
                    if isinstance(value, (int, float)):
                        self.assertFalse(math.isnan(value), f"{coin} {key} must not be NaN")
                self.assertIsInstance(row["has_gap"], bool)

    def test_live_metrics_replace_old_base_fields_for_all_28_symbols(self):
        metrics = fake_live_metrics()
        rows = build_base28.build_rows(ROOT / "index.html", live_metrics=metrics, fx_usd_krw=1500.0)

        self.assertEqual([build_base28.string_field(row, "coin") for row in rows], EXPECTED_SYMBOLS)
        for row in rows:
            symbol = build_base28.string_field(row, "coin")
            metric = metrics[symbol]
            with self.subTest(coin=symbol):
                self.assertEqual(row["mc"], metric.market_cap_usd)
                self.assertEqual(row["cum_deposit"], metric.accumulation_deposit_amt)
                self.assertEqual(row["net_deposit"], metric.purity_deposit)
                self.assertEqual(row["holders"], metric.number_of_holders)
                self.assertEqual(row["hold_pct"], metric.holding_percentage)
                self.assertEqual(row["trade_pct"], metric.trading_percentage)
                self.assertEqual(row["internal_value"], round(metric.accumulation_deposit_amt * metric.price_krw))
                self.assertEqual(row["bithumb_code"], metric.coin_type)
                self.assertEqual(row["as_of"], "2026-06-23")


if __name__ == "__main__":
    _ = unittest.main()
