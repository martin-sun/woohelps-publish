from datetime import datetime

from playwright.async_api import async_playwright

from loguru import logger

from src.config.settings import get_settings
from src.scrapers.base import BaseScraper
from src.scrapers.browser import launch_browser


class DiscoverSaskatoonScraper(BaseScraper):
    BASE_URL = "https://www.discoversaskatoon.com"
    SUPPORTED_CITIES = {"saskatoon"}

    @property
    def supported_cities(self) -> set[str]:
        return self.SUPPORTED_CITIES

    async def discover_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime,
        ai_engine=None,
    ) -> list[dict]:
        """抓列表页原始 HTML，批量收集后用 LLM 一次性提取活动摘要"""
        if city_slug not in self.SUPPORTED_CITIES:
            return []
        if not ai_engine:
            logger.warning("Saskatoon scraper requires ai_engine for LLM extraction")
            return []

        async with async_playwright() as p:
            browser = await launch_browser(p, get_settings())
            page = await browser.new_page()

            page_htmls: list[tuple[str, str]] = []  # (url, html)

            for page_num in range(10):
                url = (
                    f"{self.BASE_URL}/calendar-events?page={page_num}"
                    if page_num > 0
                    else f"{self.BASE_URL}/calendar-events"
                )
                try:
                    await self._goto(page, url)
                except Exception as e:
                    logger.warning(f"Failed to load {url}: {e}")
                    break

                html = await page.content()
                page_htmls.append((url, html))
                await self._delay()

            await browser.close()

        if not page_htmls:
            return []

        # 逐页提取，避免合并后 html_preclean 截断丢失后续页内容
        seen_urls: set[str] = set()
        unique: list[dict] = []
        for url, html in page_htmls:
            summaries = await ai_engine.extract_list_events(
                html, city_slug, "discoversaskatoon", url,
            )
            for s in summaries:
                if s.get("url") and s["url"] not in seen_urls:
                    seen_urls.add(s["url"])
                    unique.append(s)

        logger.info(f"DiscoverSaskatoon: discovered {len(unique)} events from {len(page_htmls)} pages")
        return unique
