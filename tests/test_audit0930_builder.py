import datetime as dt
import json
import math
import re
import subprocess
from pathlib import Path

import pytest

import base28_live_sources as live
import build_audit0930 as b
import measure_audit0630 as m

INDEX = Path(__file__).resolve().parents[1] / "index.html"
METRICS = {
    "/accumulation/deposit/": {"accumulationDepositAmt": "20000", "timestamp": "2026-09-29 14:00:00"},
    "/purity/deposit/": {"purityDeposit": "-5"},
    "/holders/": {"numberOfHolders": "1234"},
    "/top/holder/": {"holdingPercentage": "40"},
    "/top/trader/": {"tradingPercentage": "60"},
}


def universe_entry(**overrides) -> dict:
    entry = {
        "coin": "ABC", "kor_name": "에이비씨", "on_bithumb": True, "on_upbit": True, "stable": False,
        "gecko_id": "abc", "id_status": "map_ok", "mc_usd": 10_000_000.0, "circulating_supply": 1_000_000.0,
        "cg_last_updated": "2026-09-29T05:00:00.000Z", "price_krw_upbit": 14_000.0,
    }
    return entry | overrides


def fake_metric(path: str, context: str) -> dict:
    for key, value in METRICS.items():
        if key in path:
            return value
    raise AssertionError(path)


def test_complete_row_uses_shared_base28_formulas_and_keeps_schema(monkeypatch) -> None:
    monkeypatch.setattr(live, "fetch_metric_data", fake_metric)
    u = universe_entry()
    row = b.empty_row(u)

    b.fill_bithumb(row, u, "C0001", {"closePrice": "100"}, 1400.0)

    assert row["live_status"] == "complete"
    assert row["internal_value"] == 2_000_000
    assert row["bithumb_ratio"] == 20000 / 1_000_000
    assert row["iv_mc_ratio"] == round(2_000_000 / (10_000_000 * 1400.0) * 100, 1)
    assert row["net_deposit"] == -5 and row["trade_pct"] == 60
    assert set(row) == set(b.empty_row(u))


def test_row_without_verified_coingecko_id_leaves_supply_ratios_empty(monkeypatch) -> None:
    monkeypatch.setattr(live, "fetch_metric_data", fake_metric)
    u = universe_entry(gecko_id=None, id_status="fail", mc_usd=None, circulating_supply=None)
    row = b.empty_row(u)

    b.fill_bithumb(row, u, "C0001", {"closePrice": "100"}, 1400.0)

    assert row["live_status"] == "partial"
    assert row["internal_value"] == 2_000_000
    assert row["bithumb_ratio"] is None and row["iv_mc_ratio"] is None and row["mc"] is None
    assert set(row) == set(b.empty_row(u))


def test_missing_observer_ticker_is_explicit_missing(monkeypatch) -> None:
    monkeypatch.setattr(live, "fetch_metric_data", fake_metric)
    u = universe_entry()
    row = b.empty_row(u)

    b.fill_bithumb(row, u, "C0001", None, 1400.0)

    assert row["live_status"] == "missing"
    assert row["internal_value"] is None
    assert "observer" in str(row["source_errors"])


def test_validate_rejects_duplicates_and_non_finite() -> None:
    row = b.empty_row(universe_entry())
    with pytest.raises(SystemExit):
        b.validate([row, dict(row)])
    with pytest.raises(SystemExit):
        b.validate([row | {"coin": "X", "mc": math.nan}])


def test_embed_replaces_once_and_escapes_script_breakout() -> None:
    row = b.empty_row(universe_entry(kor_name='</script><img src=x onerror="boom">'))
    page = b.embed("<script>const D_AUDIT0930=[];</script>", [row])

    assert page.lower().count("</script>") == 1
    assert "<img" not in page
    with pytest.raises(SystemExit):
        b.embed("<script>const D=[];</script>", [row])


def hourly(start: dt.datetime, hours: int, value: float) -> dict[int, float]:
    base = int(start.timestamp())
    return {base + i * 3600: value for i in range(hours)}


def test_measure_delta_uses_only_freeze_window_and_24h_baseline() -> None:
    start, end = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    glob = hourly(pre, 40, 1.0)
    dom = hourly(pre, 40, 1400.0)  # 김프 0%
    s = int(start.timestamp())
    dom[s + 3 * 3600] = 1540.0  # 중지 중 +10%
    dom[int(end.timestamp())] = 2800.0  # 재개 직후 +100% 는 제외돼야 함

    r = m.measure("bithumb", dom, fx, glob)

    assert r is not None
    assert r["baseline"] == 0.0 and r["max_prem"] == 10.0 and r["delta"] == 10.0
    assert r["n_win"] == 10


def test_measure_rejects_mismatched_overseas_pair_and_sparse_series() -> None:
    start, _ = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    dom = hourly(pre, 40, 1400.0)

    assert m.measure("bithumb", dom, fx, hourly(pre, 40, 0.5)) == 100.0  # 기준 김프 과대 → 기준 김프 반환
    assert m.measure("bithumb", dom, fx, hourly(pre, 20, 1.0)) is None  # 중지 구간 해외가 없음


def test_choose_does_not_fall_back_to_coingecko_after_exchange_baseline_guard() -> None:
    start, _ = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    dom = hourly(pre, 40, 1400.0 * 1.3)  # 이미 +30% 격리 고김프 (TAIKO 6/30)
    cg_contaminated = hourly(pre, 40, 1.25)  # 국내가가 섞인 평균 → 기준선 통과해 가짜 Δ 를 만듦
    series = {"binance": {}, "gate": hourly(pre, 40, 1.0), "coingecko": cg_contaminated}

    r = m.choose("bithumb", dom, fx, series.get, ("binance", "gate", "coingecko"))

    assert "delta" not in r and "gate +30.0%" in r["err"]


def test_choose_uses_coingecko_only_when_no_exchange_pair() -> None:
    start, _ = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    dom = hourly(pre, 40, 1400.0)
    series = {"binance": {}, "gate": {}, "coingecko": hourly(pre, 40, 1.0)}

    r = m.choose("bithumb", dom, fx, series.get, ("binance", "gate", "coingecko"))

    assert r["source"] == "coingecko" and r["delta"] == 0.0


def test_upbit_window_stops_before_sequential_reopen() -> None:
    start, end = m.AUDIT["upbit"]
    assert (start.hour, end.hour) == (20, 5)


def test_tick_ratio_flags_sub_cent_coins_only() -> None:
    assert m.tick_ratio({0: 0.0004, 1: 0.0005, 2: 0.0004}) > m.MAX_TICK  # NFT 6/30 허수
    assert m.tick_ratio({0: 3.507, 1: 3.57, 2: 4.149}) < m.MAX_TICK
    assert m.tick_ratio({0: 12.0, 1: 12.0}) == 0.0


def embedded_rows() -> list[dict]:
    html = INDEX.read_text(encoding="utf-8")
    match = re.search(r"const D_AUDIT0930=(\[.*?\]);", html, re.DOTALL)
    if match is None:
        raise AssertionError("D_AUDIT0930 missing")
    return json.loads(match.group(1))


def test_index_audit_tab_is_default_and_wired() -> None:
    html = INDEX.read_text(encoding="utf-8")
    rows = embedded_rows()
    coins = [r["coin"] for r in rows]

    assert 'id="tabAudit"' in html and 'id="auditInfo"' in html and 'id="liveBoxAudit"' in html
    assert "switchTab('audit0930');" in html
    assert "isAudit=(w==='audit0930')" in html and "if(isAudit)liveFreeze('audit')" in html
    assert len(coins) == len(set(coins))
    assert not any(r["stable"] for r in rows)
    by = {r["coin"]: r for r in rows}
    for bithumb_sym, upbit_sym in (("WAXL", "AXL"), ("MET", "MET2"), ("UP", "UP2")):  # 같은 코인, 심볼만 다름
        assert upbit_sym not in by
        assert by[bithumb_sym]["on_upbit"] and by[bithumb_sym]["upbit_symbol"] == upbit_sym
    head = html[html.index("<thead>"):html.index("</thead>")]
    assert head.count('class="r aud"') == 3
    assert html.count("deltaCell(r.a0630_") == 2 and "'<td class=\"r aud\">'+up+'</td>'" in html
    assert "t.innerHTML+=" not in html  # 행마다 표 전체 재파싱 → 476행 6.4초 (2026-09-29 실측)
    assert "rowsHtml.join('')" in html


def test_delta_cell_escapes_tooltip_and_handles_missing() -> None:
    html = INDEX.read_text(encoding="utf-8")
    funcs = [re.search(rf"function {name}\([^)]*\)\{{.*?\}}\n", html, re.DOTALL) for name in ("known", "escapeHtml")]
    delta = re.search(r"function deltaCell\(.*?\}\n", html, re.DOTALL)
    assert all(funcs) and delta
    script = "".join(f.group(0) for f in funcs) + delta.group(0) + (
        "const assert=require('node:assert/strict');"
        "assert.ok(deltaCell(null,null,null,null,'#000').includes('—'));"
        "const h=deltaCell(3.25,5,'<b>',\"x\\\"><img\",'#000');"
        "assert.ok(h.includes('+3.3%p'));assert.equal(h.includes('<img'),false);assert.equal(h.includes('<b>'),false);"
    )
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
