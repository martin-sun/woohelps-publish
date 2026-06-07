import asyncio
import json
import re
import time

import httpx
from loguru import logger

from src.models.activity import ProcessedActivity
from src.models.property import Property

DO_SPACES_BASE = "https://woohelps.sgp1.digitaloceanspaces.com"


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


PROPERTY_TYPE_TAGS: dict[str, str] = {
    "Single Family": "独立屋",
    "Condo": "公寓",
    "Townhouse": "联排别墅",
    "Semi-Detached": "半独立屋",
    "Duplex": "双拼屋",
    "Triplex": "三拼屋",
    "Multi-Family": "多单元住宅",
    "House": "独立屋",
    "Apartment": "公寓",
    "Bungalow": "平房",
    "Commercial": "商业地产",
    "Retail": "零售商铺",
    "Hospitality": "酒店/旅馆",
    "Industrial": "工业厂房",
    "Business": "生意转让",
    "Office": "办公楼",
    "Mixed Use": "混合用途",
    "Agriculture": "农地",
    "Vacant Land": "空地",
    "Parking": "车位",
}


class WoohelpsPublisher:
    def __init__(self, base_url: str, login_session: str, user_id: str = "1"):
        self.base_url = base_url
        self.login_session = login_session
        self.user_id = user_id
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self._city_map: dict[str, int] = {}
        self._city_to_country: dict[int, int] = {}

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def fetch_city_mapping(self):
        """启动时调用平台 API 获取城市和国家 ID 映射"""
        headers = {"LOGIN-SESSION": self.login_session}
        resp = await self.client.get(
            f"{self.base_url}/api/applet/city/hot/get",
            headers=headers,
        )
        data = resp.json()
        for country in data.get("countries", []):
            country_id = country.get("id") or country.get("country_id")
            for city in country.get("cities", []):
                eng_name = city.get("eng_name") or city.get("city_eng_name", "")
                city_id = city.get("id") or city.get("city_id")
                if eng_name and city_id:
                    self._city_map[eng_name.lower()] = city_id
                    if country_id:
                        self._city_to_country[city_id] = country_id
        logger.info(f"Fetched city mapping: {self._city_map}")

    def get_city_id(self, eng_name: str) -> int | None:
        return self._city_map.get(eng_name.lower())

    def get_country_id(self, city_id: int) -> int | None:
        return self._city_to_country.get(city_id)

    # --- Image upload ---

    async def upload_image(self, image_url: str) -> str | None:
        """下载外部图片并上传到 DO Spaces，返回自有 URL。失败返回 None。"""
        return await self._upload_image_impl(image_url)

    async def _upload_image_impl(self, image_url: str) -> str | None:
        """下载外部图片并上传到 DO Spaces，返回自有 URL。失败返回 None。"""
        try:
            # 1. 下载图片
            resp = await self.client.get(image_url)
            if resp.status_code != 200:
                logger.warning(f"Failed to download image {image_url}: {resp.status_code}")
                return None
            image_bytes = resp.content
            if len(image_bytes) < 100:
                logger.warning(f"Image too small ({len(image_bytes)} bytes), skipping: {image_url}")
                return None

            # 2. 生成文件名
            from pathlib import PurePosixPath
            raw_name = PurePosixPath(image_url).name.split("?")[0] or "image.jpeg"
            if raw_name in ("image.png", "image.jpeg") or "." not in raw_name:
                import random, string
                prefix = "".join(random.choices(string.ascii_letters, k=8))
                suffix = random.randint(0, 9999)
                raw_name = f"{prefix}_{suffix}.jpeg"
            content_type = "image/jpeg"
            if raw_name.lower().endswith(".png"):
                content_type = "image/png"
            elif raw_name.lower().endswith(".webp"):
                content_type = "image/webp"

            # 3. 获取预签名 URL
            headers = {"LOGIN-SESSION": self.login_session}
            presign_resp = await self.client.get(
                f"{self.base_url}/api/applet/aws/generate-presigned-url",
                params={
                    "userId": self.user_id,
                    "fileType": "image",
                    "fileName": raw_name,
                    "contentType": content_type,
                },
                headers=headers,
            )
            if presign_resp.status_code != 200:
                logger.warning(f"Failed to get presigned URL for {raw_name}: {presign_resp.status_code}")
                return None
            presign_data = presign_resp.json()
            presign_url = presign_data.get("url", "")
            presign_fields = presign_data.get("fields", {})
            if not presign_url or not presign_fields:
                logger.warning(f"Invalid presigned response for {raw_name}")
                return None

            # 4. 上传到 DO Spaces
            scheme_end = presign_url.find("://")
            path_start = presign_url.find("/", scheme_end + 3) if scheme_end >= 0 else 0
            path = presign_url[path_start:] if path_start >= 0 else ""
            upload_url = f"{DO_SPACES_BASE}{path}"

            form_data = {k: (None, v) for k, v in presign_fields.items()}
            form_data["file"] = (raw_name, image_bytes, content_type)

            upload_resp = await self.client.post(
                upload_url,
                files=form_data,
            )
            if upload_resp.status_code < 200 or upload_resp.status_code >= 300:
                logger.warning(f"Failed to upload {raw_name}: {upload_resp.status_code}")
                return None

            result_url = f"{DO_SPACES_BASE}/{presign_fields['key']}"
            logger.info(f"Uploaded image: {result_url}")
            return result_url

        except Exception as e:
            logger.warning(f"Failed to upload image {image_url}: {e}")
            return None

    async def _upload_all_images(self, image_urls: list[str]) -> list[str]:
        """批量下载并上传图片，返回自有 URL 列表（跳过失败的）。"""
        if not image_urls:
            return []
        results = []
        for url in image_urls:
            uploaded = await self._upload_image_impl(url)
            if uploaded:
                results.append(uploaded)
        return results

    # --- Publish ---

    async def publish_activity(self, activity: ProcessedActivity, city_id: int) -> dict:
        """发布活动到海外新生活平台（miniapp 格式）"""

        # 1. 下载并上传所有图片到自有存储
        all_source_urls = []
        if activity.image_url:
            all_source_urls.append(activity.image_url)
        for url in activity.image_urls:
            if url and url not in all_source_urls:
                all_source_urls.append(url)

        uploaded_urls = await self._upload_all_images(all_source_urls)
        if not uploaded_urls:
            logger.error(f"No images uploaded for {activity.source_id}, aborting publish")
            return {"errcode": -1, "errmsg": "图片上传失败"}

        # 2. 构建 miniapp 格式 description
        description = json.dumps([
            {
                "type": 1,
                "text": json.dumps({"txt": activity.content_zh, "path": ""}, ensure_ascii=False),
            }
        ], ensure_ascii=False)

        # 3. 构建 payload
        data = {
            "name": activity.title_zh,
            "description": description,
            "city_id": city_id,
            "city_area_id": 0,
            "start_time": activity.start_time_utc.strftime("%Y-%m-%d %H:%M") if activity.start_time_utc else "",
            "end_time": activity.end_time_utc.strftime("%Y-%m-%d %H:%M") if activity.end_time_utc else "",
            "address": activity.address,
            "img": uploaded_urls[0],
            "imgs": json.dumps(uploaded_urls, ensure_ascii=False),
            "fee_type": 1 if activity.fee_parsed_free else 2,
            "fee": activity.fee_amount,
            "enroll_type": 1,
            "remind_type": 1,
            "groupon_type": 1,
        }
        headers = {"LOGIN-SESSION": self.login_session}

        for attempt in range(3):
            response = await self.client.post(
                f"{self.base_url}/api/applet/activity/release",
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


    async def publish_property(self, prop: Property, city_id: int) -> dict:
        """发布房产（售房）到海外新生活平台"""

        # 1. 收集所有图片 URL（直接使用 realtor.ca 原始链接，不上传）
        all_source_urls = []
        if prop.image_url:
            all_source_urls.append(prop.image_url)
        for url in prop.image_urls:
            if url and url not in all_source_urls:
                all_source_urls.append(url)

        if not all_source_urls:
            logger.error(f"No images available for property {prop.source_id}, aborting publish")
            return {"errcode": -1, "errmsg": "没有可用图片"}

        # 2. 构建 tags：首个标签为房源类型（中文映射），后面接亮点标签
        type_tag = PROPERTY_TYPE_TAGS.get(prop.property_type or "", prop.property_type or "")
        tags = [{"name": type_tag}] if type_tag else []
        for tag in prop.highlights[:4]:
            tags.append({"name": tag})
        if not tags:
            tags = []

        # 3. 构建 payload（Decimal 转 float 避免 JSON 序列化错误）
        data = {
            "name": prop.title_zh,
            "description": prop.content_zh,
            "price": str(prop.price_numeric or 0),
            "price_type": "sellhouse",
            "city_id": city_id,
            "address": prop.address,
            "locations": json.dumps({
                "latitude": float(prop.latitude or 0),
                "longitude": float(prop.longitude or 0),
            }),
            "imgs": json.dumps(all_source_urls),
            "tags": json.dumps(tags),
            "phone": prop.agent_phone or "",
        }
        headers = {"LOGIN-SESSION": self.login_session}

        for attempt in range(3):
            response = await self.client.post(
                f"{self.base_url}/api/applet/rental/release",
                data=data,
                headers=headers,
            )
            result = response.json()
            errcode = result.get("errcode", -1)
            if errcode == 0 or errcode in (101, 201):
                return result
            if errcode == 500 and attempt < 2:
                logger.warning(f"Property publish got 500 for {prop.source_id}, retrying ({attempt + 1}/3)...")
                await asyncio.sleep(2 ** attempt)
                continue
            return result

        return result

    async def publish_article(self, prop: Property, city_id: int) -> dict:
        """发布商业房产资讯到海外新生活平台"""

        # 1. 收集所有图片 URL（直接使用 realtor.ca 原始链接，不上传）
        all_source_urls = []
        if prop.image_url:
            all_source_urls.append(prop.image_url)
        for url in prop.image_urls:
            if url and url not in all_source_urls:
                all_source_urls.append(url)

        if not all_source_urls:
            logger.error(f"No images available for article {prop.source_id}, aborting publish")
            return {"errcode": -1, "errmsg": "没有可用图片"}

        # 2. 构建 tags：后端通过 tags 字段中的 id 来关联文章分类，只有 name 的会被忽略
        type_tag = PROPERTY_TYPE_TAGS.get(prop.property_type or "", prop.property_type or "")
        tags = []
        if type_tag:
            tags.append({"id": 2667, "name": type_tag})  # 商业信息分类
        for tag in prop.highlights[:4]:
            tags.append({"name": tag})

        # 3. 构建 article 格式的 content
        content_json = json.dumps([
            {
                "type": 1,
                "text": json.dumps({"txt": prop.content_zh, "path": ""}, ensure_ascii=False),
            }
        ], ensure_ascii=False)

        # 4. 根据国家-城市映射获取对应 country_id
        country_id = self.get_country_id(city_id)
        if not country_id:
            logger.warning(f"No country mapping for city_id={city_id}, falling back to 1")
            country_id = 1

        # 5. 构建 payload
        data = {
            "title": prop.title_zh,
            "content": content_json,
            "img": all_source_urls[0] if all_source_urls else "",
            "imgs": json.dumps(all_source_urls),
            "tags": json.dumps(tags),
            "country_id": country_id,
            "city_id": city_id,
        }
        headers = {"LOGIN-SESSION": self.login_session}

        for attempt in range(3):
            response = await self.client.post(
                f"{self.base_url}/api/applet/article/release",
                data=data,
                headers=headers,
            )
            result = response.json()
            errcode = result.get("errcode", -1)
            if errcode == 0 or errcode in (101, 201):
                return result
            if errcode == 500 and attempt < 2:
                logger.warning(f"Article publish got 500 for {prop.source_id}, retrying ({attempt + 1}/3)...")
                await asyncio.sleep(2 ** attempt)
                continue
            return result

        return result
