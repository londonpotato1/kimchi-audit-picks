import json
import re
import subprocess
from pathlib import Path

import build_bnb28

INDEX = Path(__file__).resolve().parents[1] / "index.html"


def embedded_symbols() -> list[str]:
    html = INDEX.read_text(encoding="utf-8")
    match = re.search(r"const D_BNB28=(\[.*?\]);", html, re.DOTALL)
    if match is None:
        raise AssertionError("D_BNB28 missing")
    return re.findall(r'"coin":"([^"]+)"', match.group(1))


def run_node_helper_assertions(helper_name: str, assertions: str) -> None:
    html = INDEX.read_text(encoding="utf-8")
    match = re.search(
        rf"function {helper_name}\([^)]*\)\{{.*?\}}", html, re.DOTALL
    )
    if match is None:
        raise AssertionError(f"{helper_name} missing")
    _ = subprocess.run(
        ["node", "-e", f"{match.group(0)}\n{assertions}"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_index_embeds_exact_bnb28_dataset_and_identifiable_tab() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'id="tabBnb28"' in html
    assert "BNB 8/25" in html
    assert "const D_BNB28=" in html
    symbols = embedded_symbols()
    assert symbols == build_bnb28.OFFICIAL_SYMBOLS
    assert len(symbols) == 28


def test_bnb28_uses_shared_switch_render_status_path_with_proven_bsc_id() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert "isBnb28=(w==='bnb28')" in html
    assert "symbols:new Set(D_BNB28.map" in html
    assert "net:'BSC'" in html
    assert "total:28" in html
    assert "if(isBnb28)liveFreeze('bnb28')" in html
    assert "CUR=" in html
    assert "renderStats(CUR);resetFilter()" in html
    assert "r.live_status==='missing'" in html
    assert "data_bnb28 source_errors" in html


def test_existing_tabs_and_april_history_page_remain_preserved() -> None:
    html = INDEX.read_text(encoding="utf-8")

    markers = (
        'id="tabAll"',
        'id="tabBase"',
        'id="tabBase28"',
        "const D=",
        "const D_BASE=",
        "const D_BASE28=",
    )
    assert all(marker in html for marker in markers)
    assert './bnb_freeze.html' in html
    assert "2026.04.28 09:00 KST" in html


def test_iv_limit_helper_excludes_unavailable_values_when_limit_is_active() -> None:
    html = INDEX.read_text(encoding="utf-8")
    run_node_helper_assertions(
        "passesIvLimit",
        "const assert=require('node:assert/strict');assert.equal(passesIvLimit(null,100),false);assert.equal(passesIvLimit(Number.NaN,100),false);assert.equal(passesIvLimit(99,100),true);assert.equal(passesIvLimit(101,100),false);assert.equal(passesIvLimit(null,0),true);",
    )
    assert "if(!passesIvLimit(r.internal_value,iv))return false" in html


def test_render_escape_helper_encodes_hostile_html_context_text() -> None:
    html = INDEX.read_text(encoding="utf-8")
    run_node_helper_assertions(
        "escapeHtml",
        "const assert=require('node:assert/strict');const hostile='</script><img src=x onerror=\"boom\">&';const escaped=escapeHtml(hostile);assert.equal(escaped.includes('<'),false);assert.equal(escaped.includes('>'),false);assert.equal(escaped.includes('\"'),false);assert.equal(escaped,'&lt;/script&gt;&lt;img src=x onerror=&quot;boom&quot;&gt;&amp;');",
    )
    assert "escapeHtml(note)" in html


def test_output_writer_keeps_json_and_inline_rows_identical(tmp_path: Path) -> None:
    rows = [
        build_bnb28.empty_row("ACE", None, "2026-08-24T21:42:22+09:00"),
        build_bnb28.empty_row("AEON", None, "2026-08-24T21:42:22+09:00"),
    ]
    html_path = tmp_path / "index.html"
    json_path = tmp_path / "data_bnb28.json"
    _ = html_path.write_text("<script>const D_BNB28=[];</script>", encoding="utf-8")

    build_bnb28.write_outputs(rows, json_path=json_path, html_path=html_path)

    json_text = json_path.read_text(encoding="utf-8")
    html = html_path.read_text(encoding="utf-8")
    match = re.search(r"const D_BNB28=(\[.*?\]);", html, re.DOTALL)
    if match is None:
        raise AssertionError("temporary D_BNB28 missing")
    assert json_text == json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    assert match.group(1) == build_bnb28.inline_json(rows)
