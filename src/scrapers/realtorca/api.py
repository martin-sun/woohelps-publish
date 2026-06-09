import asyncio
import json
import math
from datetime import datetime, timedelta, timezone

from loguru import logger
from playwright.async_api import async_playwright

from src.config.settings import CITIES, get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

REALTOR_API_URL = "https://api2.realtor.ca/Listing.svc/AsyncPropertySearch_Post"
REALTOR_BASE_URL = "https://www.realtor.ca"

# .NET ticks epoch offset: 621355968000000000 ticks = 1970-01-01 00:00:00 UTC
DOTNET_TICKS_EPOCH = 621355968000000000


def dotnet_ticks_to_datetime(ticks_str: str) -> datetime:
    """将 .NET DateTime ticks 字符串转为 Python UTC datetime

    .NET ticks 是从 0001-01-01 开始的每 100 纳秒计数。
    示例: '639165417695400000' -> 2026-06-08T18:56:09.540000+00:00
    """
    ticks = int(ticks_str)
    seconds = (ticks - DOTNET_TICKS_EPOCH) / 10_000_000
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def datetime_to_dotnet_ticks(dt: datetime) -> int:
    """将 Python datetime 转为 .NET DateTime ticks 整数"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = dt.timestamp()
    return int((seconds * 10_000_000) + DOTNET_TICKS_EPOCH)


def city_bounds(city_slug: str) -> dict[str, str]:
    """根据城市中心点和 radius 计算地理边界框，返回 API 参数字典。

    使用粗略估算：1° 纬度 ≈ 111 km，经度按纬度修正。
    """
    city = CITIES.get(city_slug)
    if not city:
        raise ValueError(f"Unknown city slug: {city_slug}")

    lat = city["lat"]
    lng = city["lng"]
    radius_km = float(city.get("radius", "50km").replace("km", ""))

    delta_lat = radius_km / 111.0
    delta_lng = radius_km / (111.0 * math.cos(math.radians(lat)))

    return {
        "LatitudeMin": str(round(lat - delta_lat, 4)),
        "LatitudeMax": str(round(lat + delta_lat, 4)),
        "LongitudeMin": str(round(lng - delta_lng, 4)),
        "LongitudeMax": str(round(lng + delta_lng, 4)),
    }


# 默认请求参数
DEFAULT_PAYLOAD = {
    "RecordsPerPage": "11",
    "Sort": "6-D",
    "IncludePins": "1",
    "Currency": "CAD",
    "IncludeHiddenListings": "false",
    "ApplicationId": "1",
    "CultureId": "1",
    "Version": "7.0",
}

# API 请求头（模拟前端 XMLHttpRequest）
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.realtor.ca/map",
    "Origin": "https://www.realtor.ca",
}


async def call_realtor_api(
    agent_id: str,
    page: int = 1,
    max_retries: int = 3,
    context=None,
) -> dict:
    """调用 realtor.ca AsyncPropertySearch_Post API 获取单个经纪的房源列表

    使用 Playwright 的 stealth context 发送请求，避免 403。
    包含网络/5xx 重试逻辑，每次退避 2^n 秒。
    """
    payload = {
        **DEFAULT_PAYLOAD,
        "CurrentPage": str(page),
        "IndividualId": agent_id,
    }

    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = await context.request.post(
                REALTOR_API_URL,
                form=payload,
                headers=DEFAULT_HEADERS,
            )

            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Realtor API HTTP {resp.status}: {text[:200]}")

            data = await resp.json()

            error = data.get("ErrorCode", {})
            if error.get("Id") != 200:
                raise RuntimeError(f"Realtor API error: {error.get('Description', 'Unknown')}")

            logger.info(f"Realtor API page {page}: {len(data.get('Results', []))} results, "
                        f"total {data.get('Paging', {}).get('TotalRecords', 0)}")
            return data

        except Exception as e:
            last_exception = e
            status_code = getattr(e, "status", None) or getattr(getattr(e, "response", None), "status", None)

            if attempt < max_retries and (status_code is None or status_code >= 500):
                wait = 2 ** attempt
                logger.warning(f"Realtor API attempt {attempt} failed ({status_code or 'network'}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            raise

    raise last_exception or RuntimeError("Realtor API call exhausted all retries")


async def call_realtor_city_api(
    city_slug: str,
    page: int = 1,
    max_retries: int = 3,
    context=None,
) -> dict:
    """调用 realtor.ca AsyncPropertySearch_Post API 按城市地理边界框搜索房源"""
    bounds = city_bounds(city_slug)
    payload = {
        **DEFAULT_PAYLOAD,
        "CurrentPage": str(page),
        "RecordsPerPage": "50",       # 城市范围数据量大，提高每页条数减少翻页
        **bounds,
        "PropertyTypeGroupID": "1",   # Residential
        "TransactionTypeId": "2",     # For Sale
    }

    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = await context.request.post(
                REALTOR_API_URL,
                form=payload,
                headers=DEFAULT_HEADERS,
            )

            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Realtor City API HTTP {resp.status}: {text[:200]}")

            data = await resp.json()

            error = data.get("ErrorCode", {})
            if error.get("Id") != 200:
                raise RuntimeError(f"Realtor City API error: {error.get('Description', 'Unknown')}")

            logger.info(f"Realtor City API {city_slug} page {page}: {len(data.get('Results', []))} results, "
                        f"total {data.get('Paging', {}).get('TotalRecords', 0)}")
            return data

        except Exception as e:
            last_exception = e
            status_code = getattr(e, "status", None) or getattr(getattr(e, "response", None), "status", None)

            if attempt < max_retries and (status_code is None or status_code >= 500):
                wait = 2 ** attempt
                logger.warning(f"Realtor City API attempt {attempt} failed ({status_code or 'network'}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            raise

    raise last_exception or RuntimeError("Realtor City API call exhausted all retries")


async def fetch_all_listings(agent_id: str, delay: float = 2.5) -> list[dict]:
    """翻页获取单个经纪的所有房源，返回 Results 数组。

    内部创建 Playwright stealth context 调用 API，避免 403。
    """
    settings = get_settings()
    all_results = []
    page = 1

    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings)

        # 页面预热：先访问 realtor.ca 主页让 Incapsula JS challenge 完成，获取 WAF cookies
        warmup_page = await context.new_page()
        try:
            await warmup_page.goto("https://www.realtor.ca", wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(3)  # 给 JS challenge 足够时间完成
            # 可选：再访问 map 页面建立 referer 上下文
            try:
                await warmup_page.goto("https://www.realtor.ca/map", wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(1)
            except Exception:
                pass  # map 页面加载慢，不影响后续
        except Exception:
            logger.warning("Warmup page load failed, continuing anyway")
        finally:
            await warmup_page.close()

        try:
            while True:
                data = await call_realtor_api(agent_id, page=page, context=context)
                results = data.get("Results", [])
                all_results.extend(results)

                paging = data.get("Paging", {})
                total_pages = paging.get("TotalPages", 1)

                if page >= total_pages:
                    break

                page += 1
                await asyncio.sleep(delay)
        finally:
            await context.close()
            await browser.close()

    logger.info(f"Fetched {len(all_results)} listings for agent {agent_id}")
    return all_results


async def fetch_city_listings(city_slug: str, days: int = 1, delay: float = 2.5) -> list[dict]:
    """按城市地理边界框搜索最近 `days` 天内上架的房源，返回 Results 数组。

    使用 Sort=6-D（按上架日期降序），当某页所有房源都早于 cutoff 时停止翻页。
    """
    settings = get_settings()
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ticks = datetime_to_dotnet_ticks(cutoff_dt)

    all_results: list[dict] = []
    page = 1

    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings)

        # 页面预热（与 fetch_all_listings 一致）
        warmup_page = await context.new_page()
        try:
            await warmup_page.goto("https://www.realtor.ca", wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(3)
            try:
                await warmup_page.goto("https://www.realtor.ca/map", wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(1)
            except Exception:
                pass
        except Exception:
            logger.warning("Warmup page load failed, continuing anyway")
        finally:
            await warmup_page.close()

        try:
            while True:
                data = await call_realtor_city_api(city_slug, page=page, context=context)
                results = data.get("Results", [])
                if not results:
                    break

                # 过滤：只保留 InsertedDateUTC >= cutoff_ticks 的房源
                filtered = [
                    r for r in results
                    if int(r.get("InsertedDateUTC", "0")) >= cutoff_ticks
                ]
                all_results.extend(filtered)

                # 停止条件：如果这页已经没有符合 cutoff 的房源，后续页更老，直接停
                if not filtered:
                    logger.info(f"City {city_slug} page {page}: all {len(results)} results older than {days} days, stopping")
                    break

                paging = data.get("Paging", {})
                total_pages = paging.get("TotalPages", 1)

                if page >= total_pages:
                    break

                page += 1
                await asyncio.sleep(delay)
        finally:
            await context.close()
            await browser.close()

    logger.info(f"Fetched {len(all_results)} city listings for {city_slug} (last {days} days)")
    return all_results
