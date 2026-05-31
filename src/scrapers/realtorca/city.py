"""城市解析：从加拿大地址中提取统一的城市 slug"""

from loguru import logger

from src.config.settings import CITIES

PROVINCES = {
    "alberta", "british columbia", "manitoba", "new brunswick",
    "newfoundland and labrador", "nova scotia", "ontario",
    "prince edward island", "quebec", "saskatchewan",
    "ab", "bc", "mb", "nb", "nl", "ns", "on", "pe", "qc", "sk", "yt", "nt", "nu",
}

CITY_ALIASES = {
    # 可按需扩展别名映射
}


def parse_city_from_address(address_text: str) -> str:
    """从 realtor.ca AddressText 中提取城市 slug。

    示例：
        "105 615 Stensrud ROAD|Saskatoon, Saskatchewan S7W0A1" -> "saskatoon"
        "123 Main St, Toronto, Ontario M5V 2T6" -> "toronto"
        "45 Bay St, St. Catharines, Ontario L2R 1A1" -> "st-catharines"
    """
    # 1. 分离街道与市省邮编
    parts = address_text.split("|")
    city_part = parts[-1] if len(parts) > 1 else address_text

    # 2. 按逗号分割，取城市片段（通常是倒数第二段）
    segments = [s.strip() for s in city_part.split(",")]
    city = segments[-2] if len(segments) >= 2 else segments[0]

    # 3. 容错：若城市片段被省份污染，尝试取前一段
    if city.lower() in PROVINCES and len(segments) >= 3:
        city = segments[-3]

    # 4. 标准化为 slug
    city_slug = city.lower().replace(" ", "-").replace(".", "").replace("'", "")
    city_slug = CITY_ALIASES.get(city_slug, city_slug)

    # 5. 校验：若城市不在已知列表中，记录警告（仍可继续，防止未知城市导致后续映射失败）
    if city_slug not in CITIES:
        logger.warning(f"Unknown city slug '{city_slug}' parsed from address: {address_text!r}")

    return city_slug
