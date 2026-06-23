import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_base28

INDEX = ROOT / "index.html"


def extract_const_array(name: str) -> list[build_base28.Row]:
    html = INDEX.read_text(encoding="utf-8")
    return build_base28.parse_const_array(html, name)


class StaticDashboardBase28Test(unittest.TestCase):
    def test_index_exposes_new_base28_tab_without_breaking_base27(self):
        html = INDEX.read_text(encoding="utf-8")

        self.assertIn("id=\"tabBase\"", html)
        self.assertIn("id=\"tabBase28\"", html)
        self.assertIn("D_BASE", html)
        self.assertIn("D_BASE28", html)

        base27 = extract_const_array("D_BASE")
        base28 = extract_const_array("D_BASE28")

        self.assertEqual(len(base27), 27)
        self.assertEqual(len(base28), 28)
        base27_symbols = [build_base28.string_field(row, "coin") for row in base27]
        base28_symbols = [build_base28.string_field(row, "coin") for row in base28]
        self.assertNotIn("CTR", base27_symbols)
        self.assertIn("CTR", base28_symbols)

    def test_base28_live_status_uses_28_symbols_and_label(self):
        html = INDEX.read_text(encoding="utf-8")

        self.assertIn("BASE 점검 28종", html)
        self.assertIn("D_BASE28.map", html)
        self.assertIn("/28</b>", html)
        self.assertIn("시트레아", html)
        self.assertIn("r.data_note||", html)

    def test_embedded_base28_matches_builder_output(self):
        embedded_base28 = extract_const_array("D_BASE28")
        built_base28 = build_base28.load_rows(ROOT / "data_base28.json")

        self.assertEqual(embedded_base28, built_base28)

    def test_base28_is_not_a_blind_copy_of_base27_live_fields(self):
        base27 = {build_base28.string_field(row, "coin"): row for row in extract_const_array("D_BASE")}
        base28 = [row for row in extract_const_array("D_BASE28") if build_base28.string_field(row, "coin") != "CTR"]
        live_fields = {
            "mc",
            "internal_value",
            "cum_deposit",
            "net_deposit",
            "holders",
            "hold_pct",
            "trade_pct",
            "iv_mc_ratio",
            "bithumb_ratio",
        }

        copied = [
            build_base28.string_field(row, "coin")
            for row in base28
            if all(row.get(key) == base27[build_base28.string_field(row, "coin")].get(key) for key in live_fields)
        ]

        self.assertLess(len(copied), 5, f"BASE28 live fields still copied from BASE27: {copied}")


if __name__ == "__main__":
    _ = unittest.main()
