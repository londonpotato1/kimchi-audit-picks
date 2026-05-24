#!/usr/bin/env python3
"""빗썸 BASE 체인 입출금중지 27종 데이터 빌드 (정기실사 암살픽 BASE 탭).

선례: bnb_freeze_dashboard/build_data.py
- 마스터 index.html const D(450종)에서 24종 분석값 추출 (재사용)
- 27종 전부 CoinGecko MC live 갱신 — ID는 BASE 체인 컨트랙트 보유 + 영문명 교차로 확정 (티커충돌 방지)
- 3종(OPG/UP/VVV)은 3/31 분석값 없음 → MC만 있는 placeholder
- MC 갱신 시 iv_mc_ratio = old * mc_old/mc_new 로 재계산 (internal_value는 3/31 고정)
출력: data_base27.json + data_base27.js.txt(const D_BASE=...) + 검증 요약
주의: index.html D_BASE는 자동 교체하지 않음 — 재실행 후 data_base27.js.txt를 수동 반영해야 drift 방지.
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib import request, parse

DIR = Path(__file__).resolve().parent
INDEX = DIR / "index.html"

# 빗썸 공지 1653364 순서 (= 표 노출 순서)
BASE_LIST = [
    "AERO", "AVNT", "AWE", "B3", "BRETT", "C", "CARV", "EDGE", "ELSA",
    "FLOCK", "GPS", "HOME", "KAITO", "MIRA", "OPG", "PROMPT", "RECALL",
    "SAPIEN", "SIGN", "THQ", "TOSHI", "TOWNS", "TRUST", "UP", "VIRTUAL",
    "VVV", "ZORA",
]

# 빗썸 공식 한글명 (공지 1차출처)
KOR_NAME = {
    "AERO": "에어로드롬파이낸스", "AVNT": "아반티스", "AWE": "에이더블유이",
    "B3": "비쓰리", "BRETT": "브렛", "C": "체인베이스토큰", "CARV": "카브",
    "EDGE": "디피니티브", "ELSA": "헤이엘사", "FLOCK": "플록", "GPS": "고플러스",
    "HOME": "디파이앱", "KAITO": "카이토", "MIRA": "미라", "OPG": "오픈그라디언트",
    "PROMPT": "웨이파인더", "RECALL": "리콜", "SAPIEN": "사피엔", "SIGN": "사인",
    "THQ": "테오릭", "TOSHI": "토시", "TOWNS": "타운즈", "TRUST": "인튜이션",
    "UP": "슈퍼폼", "VIRTUAL": "버추얼프로토콜", "VVV": "베니스토큰", "ZORA": "조라",
}

# CoinGecko 영문명 힌트 (티커충돌 disambiguation — base 컨트랙트 후보 중 이름 매칭용)
NAME_HINT = {
    "AERO": "aerodrome", "AVNT": "avantis", "AWE": "awe", "B3": "b3",
    "BRETT": "brett", "C": "chainbase", "CARV": "carv", "EDGE": "definitive",
    "ELSA": "elsa", "FLOCK": "flock", "GPS": "goplus", "HOME": "defiapp",
    "KAITO": "kaito", "MIRA": "mira", "OPG": "opengradient", "PROMPT": "wayfinder",
    "RECALL": "recall", "SAPIEN": "sapien", "SIGN": "sign", "THQ": "theoriq",
    "TOSHI": "toshi", "TOWNS": "towns", "TRUST": "intuition", "UP": "superform",
    "VIRTUAL": "virtual", "VVV": "venice", "ZORA": "zora",
}

# 사전 검증 완료 ID (search + /coins/{id} platforms.base 확인) — 자동매칭 override
GECKO_ID_OVERRIDE = {
    "OPG": "opengradient",   # base 0xfbc2...f5eb
    "UP": "superform",       # base 0x5b21...c86b
    "VVV": "venice-token",   # base 0xacfe...21bf (base 전용)
}

# 빗썸 내부 API 라이브 스냅샷 (gw.bithumb.com/exchange/v1/trade/* — 비로그인 공개 v1 엔드포인트).
# 수집 2026-05-24 10:30 KST | 환율 USD/KRW=1517 (gw .../comn/exrate).
# 3종(OPG/UP/VVV)은 3/31 마스터 분석에 없어 이 시점 스냅샷으로 보강 (행마다 as_of 표기).
# 매핑 검증: VIRTUAL(C0988) 대조군 — holdingPct 5=5 정확일치, holders 62,279 vs 저장 62,469(-0.3%).
# 필드: accumulationDepositAmt(내부유통량), depositChangeRate(24H %), numberOfHolders,
#       holdingPercentage(고래보유%), tradingPercentage(고래거래%), 빗썸 KRW 종가.
BITHUMB_SNAPSHOT_DATE = "2026-05-24"
FX_USD_KRW = 1517
BITHUMB_API = {
    "OPG": {"code": "C1340", "amt": 6311443,  "rate": -0.68, "holders": 995,  "hold_pct": 37, "trade_pct": 50, "price_krw": 326},
    "UP":  {"code": "C1289", "amt": 20895801, "rate": -2.31, "holders": 2983, "hold_pct": 16, "trade_pct": 21, "price_krw": 180},
    "VVV": {"code": "C1324", "amt": 104151,   "rate": -1.28, "holders": 2846, "hold_pct": 50, "trade_pct": 62, "price_krw": 27620},
}

CG = "https://api.coingecko.com/api/v3"
UA = {"User-Agent": "Mozilla/5.0"}


def fmt_mc(v):
    if v is None:
        return "—"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def get_json(url, timeout=30, retries=3):
    """CoinGecko 무료티어 rate-limit 대응 — 빈응답/429 시 백오프 재시도."""
    for i in range(retries):
        try:
            with request.urlopen(request.Request(url, headers=UA), timeout=timeout) as r:
                raw = r.read().decode()
            return json.loads(raw)
        except Exception as e:
            wait = 5 * (i + 1)
            print(f"  재시도 {i+1}/{retries} ({wait}s): {e}", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"CoinGecko 호출 실패: {url}")


def parse_const_d(html_text):
    m = re.search(r"const\s+D\s*=\s*(\[.*?\])\s*;", html_text, re.DOTALL)
    if not m:
        raise SystemExit("const D 배열을 찾지 못했습니다")
    return json.loads(m.group(1))


def resolve_ids():
    """27종 → CoinGecko ID 확정. base 컨트랙트 보유 + 영문명 매칭. 애매하면 ⚠."""
    print("CoinGecko coins/list (include_platform) 로드...")
    coins = get_json(f"{CG}/coins/list?include_platform=true")
    # 심볼별 base 컨트랙트 보유 후보
    by_sym = {}
    for c in coins:
        sym = (c.get("symbol") or "").upper()
        if (c.get("platforms") or {}).get("base"):
            by_sym.setdefault(sym, []).append(c)

    resolved, flags = {}, []
    for sym in BASE_LIST:
        if sym in GECKO_ID_OVERRIDE:
            resolved[sym] = GECKO_ID_OVERRIDE[sym]
            continue
        cands = by_sym.get(sym, [])
        if len(cands) == 1:
            resolved[sym] = cands[0]["id"]
        elif len(cands) > 1:
            hint = NAME_HINT.get(sym, "").lower()
            hit = [c for c in cands if hint and hint in (c.get("name") or "").lower()]
            if len(hit) == 1:
                resolved[sym] = hit[0]["id"]
            else:
                resolved[sym] = None
                flags.append((sym, "다중후보", [(c["id"], c["name"]) for c in cands][:6]))
        else:
            resolved[sym] = None
            flags.append((sym, "base후보없음", []))
    return resolved, flags


def fetch_markets(ids):
    """확정 ID들 MC+유통량 일괄 조회 → {id: {"mc": market_cap, "circ": circulating_supply}}."""
    csv = ",".join(parse.quote(i) for i in ids if i)
    data = get_json(f"{CG}/coins/markets?vs_currency=usd&ids={csv}&per_page=250&page=1")
    if not isinstance(data, list):
        return {}
    return {d["id"]: {"mc": d.get("market_cap"), "circ": d.get("circulating_supply")} for d in data}


def bithumb_fields(sym, mc_usd, circ):
    """BITHUMB_API 스냅샷 + CoinGecko(mc_usd, circ)로 내부가치 파생값 산출.
    internal_value = 내부유통량 × 빗썸 현재가(KRW)
    bithumb_ratio  = 내부유통량 / 전체유통량(CoinGecko circulating_supply)
    iv_mc_ratio    = internal_value / (mc_USD × FX) × 100  (내부가치의 시총 대비 %)
    net_deposit    = 24H 순변동 = amt × rate/(100+rate)  (depositChangeRate 역산)
    """
    b = BITHUMB_API[sym]
    amt, rate = b["amt"], b["rate"]
    iv = round(amt * b["price_krw"])
    return {
        "internal_value": iv,
        "iv_fmt": f"{round(iv/1e8)}억" if iv else "—",
        "cum_deposit": amt,
        "net_deposit": round(amt * rate / (100 + rate)),
        "holders": b["holders"],
        "hold_pct": b["hold_pct"],
        "trade_pct": b["trade_pct"],
        "iv_mc_ratio": round(iv / (mc_usd * FX_USD_KRW) * 100, 1) if mc_usd else 0,
        "bithumb_ratio": (amt / circ) if circ else 0,
    }


def make_placeholder(coin, mc, circ=None):
    """3/31 분석값 없는 신규 3종 — MC 실값 + (BITHUMB_API 보유 시) 빗썸 내부 스냅샷.
    gap/prev/audit 계열은 0 유지 (프리미엄 분석 없음 — 정직 표기). as_of로 시점 disclose."""
    row = {
        "coin": coin, "score": 0, "mc": mc, "mc_fmt": fmt_mc(mc),
        "has_gap": False, "gap_days": 0, "avg_gap": 0, "max_gap": 0,
        "internal_value": 0, "iv_fmt": "—", "cum_deposit": 0, "net_deposit": 0,
        "holders": 0, "hold_pct": 0, "trade_pct": 0, "iv_mc_ratio": 0,
        "bithumb_ratio": 0, "prev_max": 0, "prev_avg": 0, "prev_0930": 0,
        "audit_0331": 0, "external": True,
    }
    if coin in BITHUMB_API:
        if mc is None or circ is None:
            print(f"  ⚠ {coin}: CoinGecko mc/circ 누락 (mc={mc}, circ={circ}) → "
                  f"iv_mc_ratio/bithumb_ratio가 0으로 떨어짐. '내부유통량 있는데 비중 0%' "
                  f"행내 모순 발생 — 수동 확인 필요", file=sys.stderr)
        row.update(bithumb_fields(coin, mc, circ))
        row["as_of"] = BITHUMB_SNAPSHOT_DATE
    return row


def main():
    html = INDEX.read_text(encoding="utf-8")
    D = parse_const_d(html)
    by_coin = {r["coin"]: r for r in D}
    print(f"마스터 D: {len(D)}종 | 매칭: {sum(1 for s in BASE_LIST if s in by_coin)}/27")

    resolved, flags = resolve_ids()
    if flags:
        print("\n⚠️ ID 미확정 (수동 GECKO_ID_OVERRIDE 추가 필요):")
        for sym, why, cands in flags:
            print(f"  {sym} [{KOR_NAME[sym]}] — {why}: {cands}")
        print("→ 위 심볼 확정 전 MC 갱신 중단. (확정분만 진행하려면 무시 가능)\n")

    ids = [i for i in resolved.values() if i]
    print(f"확정 ID {len(ids)}/27 → markets MC 조회...")
    mcs = fetch_markets(ids)

    rows = []
    for sym in BASE_LIST:
        gid = resolved.get(sym)
        mkt = mcs.get(gid) if gid else None
        mc_new = mkt["mc"] if mkt else None
        if sym in by_coin:
            row = dict(by_coin[sym])
            mc_old = row.get("mc") or 0
            if mc_new and mc_old > 0:
                ratio_old = row.get("iv_mc_ratio") or 0
                row["mc"] = mc_new
                row["mc_fmt"] = fmt_mc(mc_new)
                # internal_value 3/31 고정 → iv_mc_ratio는 mc에 반비례 재계산
                row["iv_mc_ratio"] = round(ratio_old * mc_old / mc_new, 1)
            elif not mc_new:  # None 또는 0 → 갱신 실패, 3/31 값 유지 표시
                row["mc_stale"] = True
            row["external"] = False
        else:
            row = make_placeholder(sym, mc_new, mkt["circ"] if mkt else None)
        row["kor_name"] = KOR_NAME[sym]
        row["gecko_id"] = gid or ""
        rows.append(row)

    # 정합성: iv_mc_ratio 비정상(>500% = 내부가치가 시총의 5배 초과) 플래그
    for r in rows:
        if (r.get("iv_mc_ratio") or 0) > 500:
            print(f"  ⚠ {r['coin']} iv_mc_ratio={r['iv_mc_ratio']}% (비정상 의심 — IV 3/31 vs MC live)")

    (DIR / "data_base27.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (DIR / "data_base27.js.txt").write_text(
        "const D_BASE=" + json.dumps(rows, ensure_ascii=False) + ";", encoding="utf-8")

    print("\n=== 27종 요약 (검증용) ===")
    print(f"{'SYM':7s}{'한글명':14s}{'gecko_id':20s}{'MC':>9s}  iv_mc%  flag")
    for r in rows:
        flag = "신규" if r.get("external") else ("STALE" if r.get("mc_stale") else "")
        kor = r["kor_name"]
        pad = 14 - sum(2 if ord(c) > 127 else 1 for c in kor)
        print(f"{r['coin']:7s}{kor}{' '*max(pad,1)}{r['gecko_id']:20s}{r['mc_fmt']:>9s}  "
              f"{r.get('iv_mc_ratio',0):>5}  {flag}")
    print(f"\n저장: data_base27.json / data_base27.js.txt ({len(rows)}종)")


if __name__ == "__main__":
    main()
