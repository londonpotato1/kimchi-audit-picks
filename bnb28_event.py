from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class EventAsset:
    symbol: str
    kor_name: str
    gecko_id: str


ASSETS: Final[tuple[EventAsset, ...]] = (
    EventAsset("ACE", "퓨저니스트", "endurance"),
    EventAsset("AEON", "이온", "aeon-3"),
    EventAsset("ASTER", "아스터", "aster-2"),
    EventAsset("BANK", "로렌조프로토콜", "lorenzo-protocol"),
    EventAsset("BNB", "비앤비", "binancecoin"),
    EventAsset("BSB", "블록스트리트", "block-street"),
    EventAsset("C98", "코인98", "coin98"),
    EventAsset("CAKE", "팬케이크스왑", "pancakeswap-token"),
    EventAsset("COOKIE", "쿠키다오", "cookie"),
    EventAsset("CYS", "싸이식", "cysic"),
    EventAsset("D", "달오픈네트워크", "dar-open-network"),
    EventAsset("EDU", "오픈캠퍼스", "edu-coin"),
    EventAsset("FLOKI", "플로키", "floki"),
    EventAsset("GMT", "스테픈", "stepn"),
    EventAsset("HOOK", "훅트프로토콜", "hooked-protocol"),
    EventAsset("IOST", "이오스트", "iostoken"),
    EventAsset("LISTA", "리스타다오", "lista"),
    EventAsset("MONKY", "와이즈멍키", "wise-monkey"),
    EventAsset("PARTI", "파티클네트워크", "particle-network"),
    EventAsset("PUMPBTC", "펌프", "pumpbtc-2"),
    EventAsset("PURSE", "펄스토큰", "pundi-x-purse"),
    EventAsset("SFP", "세이프팔", "safepal"),
    EventAsset("SOLV", "솔브프로토콜", "solv-protocol"),
    EventAsset("THE", "테나", "thena"),
    EventAsset("UB", "유니베이스", "unibase"),
    EventAsset("USD1", "월드리버티파이낸셜유에스디", "usd1-wlfi"),
    EventAsset("XTER", "엑스테리오", "xterio"),
    EventAsset("XVS", "비너스", "venus"),
)

OFFICIAL_SYMBOLS: Final = [asset.symbol for asset in ASSETS]
KOR_NAME: Final = {asset.symbol: asset.kor_name for asset in ASSETS}
GECKO_ID: Final = {asset.symbol: asset.gecko_id for asset in ASSETS}
