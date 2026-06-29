#!/usr/bin/env python3
"""
미매칭 종목 MC 보강 — CoinGecko search 로 gecko_id 확보 + 빗썸 한글명 교차검증.
internal_value/cum_deposit(빗썸, 빌드에서 갱신됨)은 그대로 두고 mc/bithumb_ratio/iv_mc_ratio 갱신.
"""
import json, re, time, shutil, datetime, urllib.request, urllib.error

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
FX = 1542.5
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def fmt_mc(v):
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.0f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def cg(url, retries=6):
    for i in range(retries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(20 * (i + 1)); continue
            if i == retries - 1: raise
            time.sleep(3)
        except Exception:
            if i == retries - 1: raise
            time.sleep(3)
    raise RuntimeError(url)


html = open("index.html", encoding="utf-8").read()
m = re.search(r"const D=(\[.*?\]);", html, re.DOTALL)
D = json.loads(m.group(1))
gm = json.load(open("gecko_map.json"))
unmatched = [r["coin"] for r in D if r["coin"] not in gm]

# 빗썸 한글명(intro) 교차검증용
req = urllib.request.Request("https://gw.bithumb.com/exchange/v1/comn/intro", headers=UA)
coinlist = json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]["coinList"]
kor = {c["coinSymbol"]: c.get("coinName", "") for c in coinlist}

add, report = {}, []
for s in unmatched:
    d = cg(f"https://api.coingecko.com/api/v3/search?query={s}")
    exact = [c for c in d.get("coins", []) if c.get("symbol", "").upper() == s]
    if exact:
        top = exact[0]  # search는 market_cap_rank 순 → 최상위 = 메이저
        add[s] = top["id"]
        report.append((s, top["id"], top.get("name", ""), kor.get(s, ""), top.get("market_cap_rank")))
    else:
        report.append((s, "—없음—", "", kor.get(s, ""), None))
    time.sleep(2)

# markets로 mc/circ 갱신
updated = 0
if add:
    ids = ",".join(add.values())
    mk = cg(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&per_page=250&page=1")
    mkbyid = {x["id"]: x for x in mk if isinstance(x, dict) and x.get("id")}
    for r in D:
        s = r["coin"]
        gid = add.get(s)
        mx = mkbyid.get(gid) if gid else None
        if not mx:
            continue
        mc, cs = mx.get("market_cap"), mx.get("circulating_supply")
        if isinstance(mc, (int, float)) and mc > 0:
            r["mc"] = float(mc)
            r["mc_fmt"] = fmt_mc(float(mc))
            if r.get("internal_value"):
                r["iv_mc_ratio"] = round(r["internal_value"] / (float(mc) * FX) * 100, 1)
        if isinstance(cs, (int, float)) and cs > 0 and r.get("cum_deposit"):
            r["bithumb_ratio"] = r["cum_deposit"] / float(cs)
        gm[s] = gid
        updated += 1

print("=== 보강 매핑 검증 (CoinGecko name ↔ 빗썸 한글명 ↔ rank) ===")
for s, gid, cgname, korname, rank in report:
    print(f"  {s:7s} → {gid:22s} | CG:{cgname[:22]:22s} 빗썸:{korname[:14]:14s} rank#{rank}")

new_html = html[:m.start()] + "const D=" + json.dumps(D, ensure_ascii=False) + ";" + html[m.end():]
shutil.copy("index.html", f"index.html.bak_{TS}")
open("index.html", "w", encoding="utf-8").write(new_html)
json.dump(gm, open("gecko_map.json", "w"), ensure_ascii=False)
print(f"\nMC 보강 {updated}종 · 백업 index.html.bak_{TS}")
