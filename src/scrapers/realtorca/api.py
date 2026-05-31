import asyncio
import json

from loguru import logger
from playwright.async_api import async_playwright

from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

REALTOR_API_URL = "https://api2.realtor.ca/Listing.svc/AsyncPropertySearch_Post"
REALTOR_BASE_URL = "https://www.realtor.ca"

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
