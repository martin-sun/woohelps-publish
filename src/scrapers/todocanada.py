from datetime import datetime, timezone

from playwright.async_api import async_playwright

from loguru import logger

from src.config.settings import get_settings
from src.scrapers.base import BaseScraper
from src.scrapers.browser import launch_browser, new_stealth_context

TODOCANADA_CITY_SLUGS = {
    "toronto": "toronto",
    "vancouver": "vancouver",
    "calgary": "calgary",
    "edmonton": "edmonton",
    "ottawa": "ottawa",
    "winnipeg": "winnipeg",
    "montreal": "montreal",
    "saskatoon": "saskatoon",
    "regina": "regina",
    "moncton": "moncton",
}

# 翻页保护上限：避免网站分页异常导致无限翻页
MAX_PAGES_LIMIT = 20

# 只抓未来活动并按开始日期降序排列，避免默认页返回大量已结束活动
TODOCANADA_QUERY_PARAMS = "etype=nextmonth&event_sortby=stdate_high_low"


class TodoCanadaScraper(BaseScraper):
    BASE_URL = "https://www.todocanada.ca"

    @property
    def supported_cities(self) -> set[str]:
        return set(TODOCANADA_CITY_SLUGS.keys())

    async def discover_pages(
        self, city_slug: str, ai_engine=None,
    ) -> list[dict]:
        """抓列表页 HTML，用 LLM 提取活动摘要。

        按开始日期降序排列，边抓边提取边判断：当某页无未来活动时停止翻页，
        避免抓取大量已结束活动。最多翻 MAX_PAGES_LIMIT 页作为保护上限。
        """
        slug = TODOCANADA_CITY_SLUGS.get(city_slug)
        if not slug:
            return []
        if not ai_engine:
            logger.warning("TodoCanada scraper requires ai_engine for LLM extraction")
            return []

        settings = get_settings()
        today = datetime.now(timezone.utc).date()
        seen_urls: set[str] = set()
        unique: list[dict] = []
        pages_scraped = 0

        async with async_playwright() as p:
            browser = await launch_browser(p, settings)
            context = await new_stealth_context(browser, settings, city_slug=city_slug)
            page = await context.new_page()

            for page_num in range(MAX_PAGES_LIMIT):
                base_url = (
                    f"{self.BASE_URL}/city/{slug}/events/page/{page_num}/"
                    if page_num > 0
                    else f"{self.BASE_URL}/city/{slug}/events/"
                )
                url = f"{base_url}?{TODOCANADA_QUERY_PARAMS}"
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    if not resp or resp.status in (404, 403):
                        break
                except Exception as e:
                    logger.warning(f"Failed to load {url}: {e}")
                    break

                html = await page.content()
                pages_scraped += 1

                summaries = await ai_engine.extract_list_events(
                    html, city_slug, "todocanada", url,
                )

                future_count = 0
                past_count = 0
                for s in summaries:
                    # 统计本页所有活动的日期分布（含重复），用于决定是否继续翻页
                    raw = s.get("start_date")
                    if raw and raw != "null":
                        try:
                            event_date = datetime.strptime(raw, "%Y-%m-%d").date()
                            if event_date >= today:
                                future_count += 1
                            else:
                                past_count += 1
                        except ValueError:
                            pass
                    # 去重并累积新活动
                    if not (s.get("url") and s["url"] not in seen_urls):
                        continue
                    seen_urls.add(s["url"])
                    unique.append(s)

                logger.info(
                    f"TodoCanada {city_slug} page {page_num}: "
                    f"{len(summaries)} events, {future_count} future, {past_count} past"
                )

                # 按开始日期降序排列下，若本页无未来活动，说明已翻到过去区域，停止
                if future_count == 0 and page_num > 0:
                    logger.info(
                        f"TodoCanada {city_slug}: no future events on page {page_num}, "
                        f"stop pagination after {pages_scraped} pages"
                    )
                    break

                await self._delay()

            await context.close()
            await browser.close()

        logger.info(
            f"TodoCanada {city_slug}: discovered {len(unique)} events from {pages_scraped} pages"
        )
        return unique

