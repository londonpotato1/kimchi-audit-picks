#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""6/30 실사 백테스트 — 실사 직전 지표(data_D_0630.json, 6/30 02:05 스냅샷) → 실사 중 빗썸 Δ김프.

어떤 지표가 실사 김프 상승을 설명하는지 Spearman(순위)·Pearson 상관과 상위 20 적중으로 본다.
출력: audit_0630_backtest.json
"""
import json
import math
from pathlib import Path

DIR = Path(__file__).resolve().parent
FEATURES = {
    "bithumb_ratio": "빗썸비중", "iv_mc_ratio": "빗썸가치/MC", "internal_value": "내부가치(log)",
    "mc": "시총(log)", "holders": "보유자(log)", "hold_pct": "고래보유", "trade_pct": "고래거래",
    "score": "점수 v2", "max_gap": "최대갭", "avg_gap": "평균갭", "gap_days": "갭일",
    "prev_0930": "25.9/30 실사", "prev_max": "12/31 실사", "audit_0331": "3/31 실사",
}
LOG = {"internal_value", "mc", "holders"}


def ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def main() -> None:
    before = {r["coin"]: r for r in json.loads((DIR / "data_D_0630.json").read_text())}
    meas = json.loads((DIR / "audit_0630_premium.json").read_text())
    stables = {u["coin"] for u in json.loads((DIR / "audit0930_universe.json").read_text()) if u["stable"]}
    bt = {r["coin"]: r for r in meas if r["exchange"] == "bithumb" and "delta" in r and r["coin"] not in stables}
    up = {r["coin"]: r for r in meas if r["exchange"] == "upbit" and "delta" in r and r["coin"] not in stables}
    joined = [(c, before[c], bt[c]) for c in bt if c in before]
    deltas = [m["delta"] for _, _, m in joined]
    print(f"빗썸 6/30 실측 {len(bt)}종 · 실사 전 지표와 결합 {len(joined)}종")
    dist = {f"Δ≥{t}%": sum(1 for d in deltas if d >= t) for t in (2, 3, 5, 10, 20)}
    print("Δ 분포:", dist, "| 중앙값", round(sorted(deltas)[len(deltas) // 2], 2))

    corr = []
    for key, label in FEATURES.items():
        pairs = [(f[key], m["delta"]) for _, f, m in joined
                 if isinstance(f.get(key), (int, float)) and not isinstance(f.get(key), bool)
                 and (key not in LOG or f[key] > 0)]
        xs = [math.log10(x) if key in LOG else float(x) for x, _ in pairs]
        ys = [y for _, y in pairs]
        sp, pe = pearson(ranks(xs), ranks(ys)), pearson(xs, ys)
        corr.append({"feature": key, "label": label, "n": len(xs),
                     "spearman": round(sp, 3) if sp is not None else None,
                     "pearson": round(pe, 3) if pe is not None else None})
    corr.sort(key=lambda c: -abs(c["spearman"] or 0))
    print("\n지표 → 6/30 빗썸 Δ 상관 (Spearman 절댓값순)")
    for c in corr:
        print(f"  {c['label']:<12} n={c['n']:<4} spearman {str(c['spearman']):>7}  pearson {str(c['pearson']):>7}")

    actual_top = {c for c, _, _ in sorted(joined, key=lambda x: -x[2]["delta"])[:20]}
    hits = {}
    for key in ("score", "audit_0331", "prev_max", "prev_0930", "bithumb_ratio", "iv_mc_ratio", "max_gap"):
        pred = {c for c, f, _ in sorted(joined, key=lambda x: -(x[1].get(key) or -1e9))[:20]}
        hits[key] = len(pred & actual_top)
    print("\n상위 20 적중 (지표 상위20 ∩ 실제 Δ 상위20):", hits)
    print("실제 Δ 상위 20:", [(c, m["delta"]) for c, _, m in sorted(joined, key=lambda x: -x[2]["delta"])[:20]])

    on_upbit = {u["coin"] for u in json.loads((DIR / "audit0930_universe.json").read_text()) if u["on_upbit"]}
    for label, group in (("빗썸 단독", [x["delta"] for c, _, x in joined if c not in on_upbit]),
                         ("업비트 동시상장", [x["delta"] for c, _, x in joined if c in on_upbit])):
        if group:
            g = sorted(group)
            print(f"{label} {len(g)}종: Δ 중앙값 {g[len(g) // 2]:.2f} · 평균 {sum(g) / len(g):.2f} · Δ≥3% {sum(1 for d in g if d >= 3)}종")

    both = [(bt[c]["delta"], up[c]["delta"]) for c in bt if c in up]
    r_bu = pearson(ranks([a for a, _ in both]), ranks([b for _, b in both])) if both else None
    print(f"\n빗썸 Δ vs 업비트 Δ (양쪽 상장 {len(both)}종) Spearman {r_bu and round(r_bu, 3)}")
    updist = [r["delta"] for r in up.values()]
    print("업비트 Δ 분포:", {f"Δ≥{t}%": sum(1 for d in updist if d >= t) for t in (2, 3, 5, 10)})

    control_file = DIR / "audit_0630_control.json"
    control_summary = {}
    if control_file.exists():  # 하루 전 같은 시간대(평상시)를 같은 공식으로 — 최대−평균 잡음 바닥
        ctrl = {(r["exchange"], r["coin"]): r["delta"] for r in json.loads(control_file.read_text()) if "delta" in r}
        for ex, audit in (("bithumb", bt), ("upbit", up)):
            pairs = [(audit[c]["delta"], ctrl[(ex, c)]) for c in audit if (ex, c) in ctrl]
            if not pairs:
                continue
            a, c = sorted(p[0] for p in pairs), sorted(p[1] for p in pairs)
            s = {k: {"median": round(v[len(v) // 2], 2), **{f"ge{t}": sum(1 for x in v if x >= t) for t in (3, 5, 10)}}
                 for k, v in (("audit", a), ("control", c))}
            s["n"] = len(pairs)
            control_summary[ex] = s
            print(f"\n[{ex}] 실사 vs 평상시(하루 전 같은 시간) · 같은 {len(pairs)}종: "
                  f"Δ 중앙값 {s['audit']['median']} vs {s['control']['median']} · "
                  f"3%p↑ {s['audit']['ge3']} vs {s['control']['ge3']} · 5%p↑ {s['audit']['ge5']} vs {s['control']['ge5']} · "
                  f"10%p↑ {s['audit']['ge10']} vs {s['control']['ge10']}")

    (DIR / "audit_0630_backtest.json").write_text(json.dumps({
        "control": control_summary,
        "n": len(joined), "distribution": dist, "correlations": corr, "top20_hits": hits,
        "bithumb_upbit_spearman": r_bu,
    }, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
