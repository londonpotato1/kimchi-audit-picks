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


def test_results_only_keeps_pre_audit_snapshot_and_pre_keys_only_for_0930(monkeypatch) -> None:
    row = b.empty_row(universe_entry()) | {"mc": 123.0, "internal_value": 456}
    meas = {("0930", "premium", "bithumb", "ABC"): {"delta": 9.0, "max_prem": 10.0, "max_time": "t", "source": "gate",
                                                    "pre_delta": 16.0, "pre_time": "09-30 16시"},
            ("0630", "futures_premium", "upbit", "ABC"): {"delta": 1.0, "rev_delta": -40.1, "min_prem": -40.6,
                                                          "min_time": "07-01 01시", "source": "binance_perp"}}
    monkeypatch.setattr(b, "load_measurements", lambda: meas)

    [row] = b.add_results([row])

    assert row["mc"] == 123.0 and row["internal_value"] == 456  # 실사 전 스냅샷 그대로
    assert row["a0930_bt"] == 9.0 and row["a0930_bt_pre"] == 16.0 and row["a0930_up"] is None
    assert not any(k.endswith("_pre") for k in row if not k.startswith("a0930_"))
    assert row["rev0630_up"] == -40.1 and row["rev0630_up_min"] == -40.6 and row["rev0630_up_src"] == "binance_perp"
    assert row["a0630_up"] is None and row["rev0930_bt"] is None  # 역현선은 김프 Δ 컬럼과 섞이지 않음
    assert set(row) == set(b.empty_row(universe_entry()))


def test_missing_measurement_file_stops_build(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(b, "DIR", tmp_path)  # 측정 파일 없음 → 기존 결과가 빈칸으로 덮이지 않게 중단
    with pytest.raises(SystemExit, match=r"audit_\d{4}_premium.json 없음"):
        b.load_measurements()
    for event in ("0331", "0630", "0930"):
        (tmp_path / f"audit_{event}_premium.json").write_text("[]")
    with pytest.raises(SystemExit, match="audit_0630_futures_premium.json 없음 .*--futures"):
        b.load_measurements()


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


def test_measure_reports_pre_freeze_spike_separately() -> None:
    start, _ = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    glob = hourly(pre, 40, 1.0)
    dom = hourly(pre, 40, 1400.0)
    dom[int(start.timestamp()) - 3600] = 1400.0 * 1.2  # 중지 1시간 전 +20% (O 9/30 16시 패턴)

    r = m.measure("bithumb", dom, fx, glob)

    assert r is not None and r["delta"] < 1  # 중지 구간에는 펌핑 없음
    assert r["pre_delta"] == round(20 - 20 / 24, 2)
    assert r["pre_time"].endswith(f"{(start - dt.timedelta(hours=1)).hour:02d}:00")


def test_measure_reports_reverse_gap_low_inside_window_only() -> None:
    start, end = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    perp = hourly(pre, 40, 1.0)
    dom = hourly(pre, 40, 1400.0)
    dom[int(start.timestamp()) + 5 * 3600] = 1400.0 * 0.6  # 중지 중 국내가 선물보다 40% 쌈 (IN 6/30 업비트 패턴)
    dom[int(start.timestamp()) - 2 * 3600] = 1400.0 * 0.5  # 중지 전 저점은 역현선이 아님 (기준선에만 반영)

    r = m.measure("bithumb", dom, fx, perp)

    assert r is not None and r["min_prem"] == -40.0
    assert r["rev_delta"] == round(-40 - (-50 / 24), 2)
    assert r["min_time"].endswith(f"{(start + dt.timedelta(hours=5)).hour:02d}:00")


def test_measure_rejects_mismatched_overseas_pair_and_sparse_series() -> None:
    start, _ = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    dom = hourly(pre, 40, 1400.0)

    assert m.measure("bithumb", dom, fx, hourly(pre, 40, 0.5)) == 100.0  # 기준 김프 과대 → 기준 김프 반환
    assert m.measure("bithumb", dom, fx, hourly(pre, 20, 1.0)) is None  # 중지 구간 해외가 없음


def test_choose_reports_baseline_guard_when_no_exchange_pair_passes() -> None:
    start, _ = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    dom = hourly(pre, 40, 1400.0 * 1.3)  # 이미 +30% 격리 고김프 (TAIKO 6/30)
    series = {"binance": {}, "gate": hourly(pre, 40, 1.0)}

    r = m.choose("bithumb", dom, fx, series.get, ("binance", "gate"))

    assert "delta" not in r and "gate +30.0%" in r["err"]


def test_choose_falls_back_to_gate_when_no_binance_pair() -> None:
    start, _ = m.EVENTS["bithumb"]
    pre = start - dt.timedelta(hours=24)
    fx = hourly(pre, 40, 1400.0)
    dom = hourly(pre, 40, 1400.0)
    series = {"binance": {}, "gate": hourly(pre, 40, 1.0)}

    r = m.choose("bithumb", dom, fx, series.get, ("binance", "gate"))

    assert r["source"] == "gate" and r["delta"] == 0.0


def test_audit_windows_match_notices_and_stop_before_upbit_reopen() -> None:
    hours = {ev: {ex: (s.hour, e.hour) for ex, (s, e) in w.items()} for ev, w in m.AUDITS.items()}
    assert hours["0630"] == {"bithumb": (17, 3), "upbit": (20, 5)}  # 업비트 06:55 재개 완료
    assert hours["0331"] == {"bithumb": (17, 3), "upbit": (20, 4)}  # 업비트 05:15 재개 완료
    assert m.EVENT == "0630" and m.OUT.name == "audit_0630_premium.json"


def test_perp_fetchers_handle_1000_prefix_missing_symbol_and_pros_alias(monkeypatch) -> None:
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        if "fapi.binance.com" in url:
            if "symbol=PEPEUSDT" in url:  # 바이낸스 무기한에 없는 심볼 = HTTP 400
                raise m.urllib.error.HTTPError(url, 400, "Bad Request", None, None)
            return [[3_600_000, "0", "0", "0", "4.27"]]
        if "symbol=PEPEUSDT" in url:  # 바이비트 없는 심볼 = HTTP 200 + retCode≠0
            return {"retCode": 10001, "result": {"list": []}}
        return {"retCode": 0, "result": {"list": [["7200000", "0", "0", "0", "4.25"], ["3600000", "0", "0", "0", "4.20"]]}}

    monkeypatch.setattr(m, "get", fake_get)

    assert m.binance_perp("PEPE") == {3600: 4.27 / 1000}  # 1000PEPE 가격 ÷1000
    assert m.bybit_perp("PEPE") == {7200: 4.25 / 1000, 3600: 4.20 / 1000}  # 최신순 응답도 시각 키로
    m.binance_perp("PROS")
    m.bybit_perp("PROS")
    assert "symbol=PHAROSUSDT" in calls[-2] and "symbol=PHAROSUSDT" in calls[-1]  # 바이낸스·바이비트 무기한만 PHAROS
    assert m.bybit("PEPE") == {} and "category=spot" in calls[-1]  # 현물은 접두·별칭 없이 그대로, 없는 심볼 = 빈값
    assert m.bybit("POPCAT") == {7200: 4.25, 3600: 4.20} and "symbol=POPCATUSDT" in calls[-1]


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
    assert head.count('class="r aud"') == 10  # 업비트비중 · 3/31 빗썸Δ · 6/30·9/30 빗썸·업비트Δ · 6/30·9/30 빗썸·업비트 역현선
    assert head.count('class="r nonaud"') == 2  # 3/31 월간 최대김프(봇) · 5/28 입출막 → 9/30 탭에서 숨김
    assert html.count("deltaCell(r.a0630_") == 2 and html.count("deltaCell(r.a0331_") == 1
    assert html.count("deltaCell(r.a0930_") == 2
    assert html.count("revCell(r.rev0630_") == 2 and html.count("revCell(r.rev0930_") == 2
    assert "k.startsWith('rev')?1:-1" in html  # 역현선 첫 클릭 = 음수 큰 순
    assert "'<td class=\"r aud\">'+up+'</td>'" in html
    # 9/30 탭 기본 = 시총 작은 순 (3/31·6/30 백테스트에서 가장 안정)
    assert "if(isAudit)document.getElementById('fSort').value='mc';" in html
    assert "t.innerHTML+=" not in html  # 행마다 표 전체 재파싱 → 476행 6.4초 (2026-09-29 실측)
    assert "rowsHtml.join('')" in html


def test_delta_cell_escapes_tooltip_and_handles_missing() -> None:
    html = INDEX.read_text(encoding="utf-8")
    funcs = [re.search(rf"function {name}\([^)]*\)\{{.*?\}}\n", html, re.DOTALL) for name in ("known", "escapeHtml")]
    delta = re.search(r"function deltaCell\(.*?\}\n", html, re.DOTALL)
    rev = re.search(r"function revCell\(.*?\}\n", html, re.DOTALL)
    assert all(funcs) and delta and rev
    script = "".join(f.group(0) for f in funcs) + delta.group(0) + rev.group(0) + (
        "const assert=require('node:assert/strict');"
        "assert.ok(deltaCell(null,null,null,null,'#000').includes('—'));"
        "const h=deltaCell(3.25,5,'<b>',\"x\\\"><img\",'#000');"
        "assert.ok(h.includes('+3.3%p'));assert.equal(h.includes('<img'),false);assert.equal(h.includes('<b>'),false);"
        "assert.ok(deltaCell(-1.3,-0.7,'x','gate','#000',16.03,'09-30 16시').includes('⚡직전+16'));"
        "assert.equal(deltaCell(1,2,'x','gate','#000',2,'t').includes('⚡'),false);"
        "assert.ok(revCell(null,null,null,null,'#000').includes('—'));"
        "const rv=revCell(-40.14,-40.6,'<i>','binance_perp','#000');"
        "assert.ok(rv.includes('-40.1%p'));assert.ok(rv.includes('무기한 binance'));assert.equal(rv.includes('<i>'),false);"
    )
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
