#!/usr/bin/env python3
"""
index.html 의 D_BASE / D_BASE28 에 5/28 입출막 실측 김프(freeze_0528) 주입 + 백업.
- freeze_0528   : 입출막 기간(5/28 19시~5/29 재개) 최고 김프 (max_full)
- freeze_0528_d : baseline 대비 순효과 (delta_full)
UI(thead/render) 컬럼은 별도 Edit 로 추가 (정확 매칭). 이 스크립트는 데이터만.
"""
import json, re, shutil, sys, datetime

ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
freeze = {r["coin"]: r for r in json.load(open("audit_0528_freeze_close.json"))
          if r.get("max_strict") is not None}
html = open("index.html", encoding="utf-8").read()


def inject(html, name):
    m = re.search(rf"const {name}=(\[.*?\]);", html, re.DOTALL)
    if not m:
        raise SystemExit(f"{name} 배열 못 찾음")
    arr = json.loads(m.group(1))
    cnt = 0
    for r in arr:
        f = freeze.get(r["coin"])
        if f:
            r["freeze_0528"] = f["max_strict"]      # 확실히 막힌 8h(5/28 19시~5/29 03시) 종가 최고김프 = 순수 입출막 효과
            r["freeze_0528_f"] = f["max_full"]       # 재개일 포함 상한 (보조)
            r["freeze_0528_d"] = f["delta_strict"]   # baseline 대비 순효과
            cnt += 1
    print(f"  {name}: {cnt}/{len(arr)}종 주입")
    return html[:m.start()] + f"const {name}=" + json.dumps(arr, ensure_ascii=False) + ";" + html[m.end():]


html = inject(html, "D_BASE")
html = inject(html, "D_BASE28")
shutil.copy("index.html", f"index.html.bak_{ts}")
open("index.html", "w", encoding="utf-8").write(html)
print(f"  백업: index.html.bak_{ts}")
print(f"  주입 대상 {len(freeze)}종 (CTR 등 5월 실적 없는 종목은 미주입 → 대시보드 '-' 표시)")
