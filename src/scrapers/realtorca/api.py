import asyncio
import json
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


def city_url(city_slug: str) -> str:
    """根据城市 slug 拼接 realtor.ca 城市页面 URL

    需要 CITIES 已通过 load_cities() 加载（含 province + eng_name）。
    示例: "toronto" -> "https://www.realtor.ca/on/toronto/real-estate"
    """
    city = CITIES.get(city_slug)
    if not city:
        raise ValueError(f"Unknown city slug: {city_slug}")
    prov = (city.get("province") or "").lower()
    if not prov:
        raise ValueError(f"City {city_slug} missing province")
    slug_in_url = city["eng_name"].lower().replace(" ", "-").replace(".", "").replace("'", "")
    return f"{REALTOR_BASE_URL}/{prov}/{slug_in_url}/real-estate"


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


async def fetch_city_geo_id(context, url: str) -> str:
    """访问 realtor.ca 城市页面，提取 GeographicId

    realtor.ca 城市页面是 ASP.NET SSR，GeographicId 注入在内联脚本的
    SEOLandingPageCriteria 中（GeoIds=g30_xxx）。

    只提取 geo_id，不取房源数据（避免与 API 分页的 RecordsPerPage 不一致导致漏数据）。
    """
    page = await context.new_page()
    try:
        logger.info(f"Fetching city geo_id: {url}")
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if resp and resp.status != 200:
            raise RuntimeError(f"City page HTTP {resp.status}: {url}")

        # 提取 GeographicId（从内联脚本的 GeoIds= 参数）
        geo_id = await page.evaluate("""
            () => {
                const scripts = Array.from(document.querySelectorAll('script'));
                for (const s of scripts) {
                    const m = (s.textContent || '').match(/GeoIds=([A-Za-z0-9_]+)/);
                    if (m) return m[1];
                }
                return null;
            }
        """)
        if not geo_id:
            raise RuntimeError(f"No GeoIds found in {url}")

        logger.info(f"City geo_id: {geo_id}")
        return geo_id
    finally:
        await page.close()


async def call_realtor_city_api(
    geo_id: str,
    page: int = 1,
    max_retries: int = 3,
    context=None,
) -> dict:
    """调用 realtor.ca AsyncPropertySearch_Post API 按 GeographicId 搜索房源

    使用从城市页面提取的 GeographicId 精确搜索，边界与 realtor.ca 官方一致。
    """
    payload = {
        **DEFAULT_PAYLOAD,
        "CurrentPage": str(page),
        "RecordsPerPage": "50",       # 城市范围数据量大，提高每页条数减少翻页
        "GeoIds": geo_id,
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

            logger.info(f"Realtor City API geo_id={geo_id} page {page}: {len(data.get('Results', []))} results, "
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


async def fetch_city_listings(
    city_slug: str,
    days: int = 1,
    delay: float = 2.5,
    max_results: int = 0,
) -> list[dict]:
    """按城市搜索最近 `days` 天内上架的房源，返回 Results 数组。

    流程：
    1. 从 CITIES 获取 province + eng_name，拼接 realtor.ca 城市 URL
    2. 访问城市页面，提取 GeographicId（不取 SSR 数据，避免分页不一致漏数据）
    3. 用 GeographicId 调 API 从 page=1 开始抓（50条/页），按 InsertedDateUTC 过滤
    4. 遇到全旧房源页即停止翻页（Sort=6-D 保证按上架时间降序）

    max_results: >0 时达到该数量即停止翻页（避免大城市爬太多页）
    """
    settings = get_settings()
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ticks = datetime_to_dotnet_ticks(cutoff_dt)

    url = city_url(city_slug)
    all_results: list[dict] = []

    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings, city_slug=city_slug)

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
            # 提取城市 GeographicId
            geo_id = await fetch_city_geo_id(context, url)

            # 统一用 API 从 page=1 开始抓（RecordsPerPage=50）
            page = 1
            while True:
                data = await call_realtor_city_api(geo_id, page=page, context=context)
                results = data.get("Results", [])
                if not results:
                    break

                filtered = [
                    r for r in results
                    if int(r.get("InsertedDateUTC", "0")) >= cutoff_ticks
                ]
                all_results.extend(filtered)

                if not filtered:
                    logger.info(f"City {city_slug} page {page}: all {len(results)} results older than {days} days, stopping")
                    break

                if max_results > 0 and len(all_results) >= max_results:
                    logger.info(f"City {city_slug}: reached max_results={max_results} at page {page}, stopping")
                    all_results = all_results[:max_results]
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
