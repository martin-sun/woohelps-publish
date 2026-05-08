import asyncio
import json
import re

import httpx
from loguru import logger

from src.models.activity import ProcessedActivity


def parse_fee_amount(price: str | None) -> tuple[float, bool]:
    """解析价格字符串，返回 (金额, 是否免费)。"""
    if not price:
        return 0.0, True
    price = price.strip()
    if price.lower() in ("free", "free!"):
        return 0.0, True
    numbers = re.findall(r"\$(\d+(?:\.\d+)?)", price)
    if numbers:
        return float(numbers[0]), False
    numbers = re.findall(r"(\d+(?:\.\d+)?)", price)
    if numbers:
        return float(numbers[0]), False
    return 0.0, False


class WoohelpsPublisher:
    def __init__(self, base_url: str, login_session: str):
        self.base_url = base_url
        self.login_session = login_session
        self.client = httpx.AsyncClient(timeout=30.0)
        self._city_map: dict[str, int] = {}

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def fetch_city_mapping(self):
        """启动时调用平台 API 获取城市 ID 映射"""
        resp = await self.client.get(
            f"{self.base_url}/api/applet/city/hot/get/",
            headers={"LOGIN_SESSION": self.login_session},
        )
        data = resp.json()
        cities = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(cities, list):
            for city in cities:
                eng_name = city.get("eng_name") or city.get("city_eng_name", "")
                city_id = city.get("id") or city.get("city_id")
                if eng_name and city_id:
                    self._city_map[eng_name.lower()] = city_id
        logger.info(f"Fetched city mapping: {self._city_map}")

    def get_city_id(self, eng_name: str) -> int | None:
        return self._city_map.get(eng_name.lower())

    async def publish_activity(self, activity: ProcessedActivity, city_id: int) -> dict:
        """发布活动到海外新生活平台（fee_amount/fee_parsed_free 应在调用前已设置）"""
        data = {
            "name": activity.title_zh,
            "description": activity.description_zh,
            "html": activity.html_zh,
            "city_id": city_id,
            "start_time": activity.start_time_utc.strftime("%Y-%m-%d %H:%M") if activity.start_time_utc else "",
            "end_time": activity.end_time_utc.strftime("%Y-%m-%d %H:%M") if activity.end_time_utc else "",
            "address": activity.address,
            "img": activity.image_url or "",
            "imgs": json.dumps(activity.image_urls),
            "fee_type": 1 if activity.fee_parsed_free else 2,
            "fee": activity.fee_amount,
            "enroll_type": 1,
            "remind_type": 1,
            "groupon_type": 1,
        }
        headers = {"LOGIN_SESSION": self.login_session}

        for attempt in range(3):
            response = await self.client.post(
                f"{self.base_url}/api/applet/activity/release/",
                data=data,
                headers=headers,
            )
            result = response.json()
            errcode = result.get("errcode", -1)
            if errcode == 0 or errcode in (101, 201):
                return result
            if errcode == 500 and attempt < 2:
                logger.warning(f"Publish got 500 for {activity.source_id}, retrying ({attempt + 1}/3)...")
                await asyncio.sleep(2 ** attempt)
                continue
            return result

        return result
