from datetime import datetime

from playwright.async_api import async_playwright

from loguru import logger

from src.config.settings import get_settings
from src.scrapers.base import BaseScraper
from src.scrapers.browser import launch_browser

FAMILYFUN_CITY_SLUGS = {
    "toronto": "toronto",
    "vancouver": "vancouver",
    "calgary": "calgary",
    "edmonton": "edmonton",
    "ottawa": "ottawa",
    "winnipeg": "winnipeg",
    "montreal": "montreal",
    "saskatoon": "saskatoon",
}


class FamilyFunCanadaScraper(BaseScraper):
    BASE_URL = "https://www.familyfuncanada.com"

    @property
    def supported_cities(self) -> set[str]:
        return set(FAMILYFUN_CITY_SLUGS.keys())

    async def discover_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime,
        ai_engine=None,
    ) -> list[dict]:
        """只抓列表页，返回文章 URL 列表（摘要信息有限，以 URL 为主）"""
        slug = FAMILYFUN_CITY_SLUGS.get(city_slug)
        if not slug:
            return []

        async with async_playwright() as p:
            browser = await launch_browser(p, get_settings())
            page = await browser.new_page()

            city_url = f"{self.BASE_URL}/{slug}/"
            await self._goto(page, city_url)
            article_urls = await self._collect_links(
                page, rf"familyfuncanada\.com/{slug}/[^/]+/$"
            )

            skip_patterns = [
                "/category/", "/tag/", "/calendar/", "/page/",
                "/feed/", "/files/", "/wp-content/", "/wp-json/",
            ]
            article_urls = [
                u for u in article_urls
                if not any(p in u for p in skip_patterns)
            ]

            for page_num in range(2, 4):
                next_url = f"{self.BASE_URL}/{slug}/page/{page_num}/"
                resp = await page.goto(next_url, wait_until="domcontentloaded", timeout=30_000)
                if not resp or resp.status == 404:
                    break
                more = await self._collect_links(
                    page, rf"familyfuncanada\.com/{slug}/[^/]+/$"
                )
                more = [u for u in more if not any(p in u for p in skip_patterns)]
                article_urls.extend(more)

            article_urls = list(set(article_urls))
            logger.info(f"FamilyFunCanada {city_slug}: discovered {len(article_urls)} articles")
            await browser.close()

            # 返回简单摘要（标题从 URL 提取，详情页再补全）
            return [
                {"url": url, "title": url.rstrip("/").split("/")[-1].replace("-", " ").title()}
                for url in article_urls
            ]

