"""城市数据加载器：启动时从 v2 API 获取城市列表，填充 settings.CITIES

替代旧的硬编码 CITIES 字典。城市列表与小程序/网站/App 保持一致（同源 v2 API）。
"""
import httpx
from loguru import logger

from src.config.settings import CITIES


def _slugify(eng_name: str) -> str:
    """将英文城市名转为 slug（与 parse_city_from_address 规则一致）

    "Toronto" -> "toronto"
    "Richmond Hill" -> "richmond-hill"
    "St. Catharines" -> "st-catharines"
    """
    return eng_name.lower().replace(" ", "-").replace(".", "").replace("'", "")


async def load_cities(base_url: str, login_session: str) -> None:
    """从 v2 API 加载城市列表，填充 settings.CITIES

    v2 API 端点: {base_url}/api/applet/city/hot/v2/get
    返回结构: {"countries": [{"id", "cities": [{"id", "name", "eng_name", "province", ...}]}]}
    """
    headers = {"LOGIN-SESSION": login_session}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{base_url}/api/applet/city/hot/v2/get",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    CITIES.clear()
    for country in data.get("countries", []):
        for city in country.get("cities", []):
            eng_name = city.get("eng_name") or ""
            slug = _slugify(eng_name)
            if not slug:
                continue
            CITIES[slug] = {
                "name": city.get("name") or "",
                "eng_name": eng_name,
                "province": (city.get("province") or "").upper(),
                "id": city.get("id"),
            }

    logger.info(f"Loaded {len(CITIES)} cities from v2 API: {list(CITIES.keys())}")
