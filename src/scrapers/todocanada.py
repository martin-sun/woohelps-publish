from playwright.async_api import async_playwright

from loguru import logger

from src.config.settings import get_settings
from src.scrapers.base import BaseScraper
from src.scrapers.browser import launch_browser

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

MAX_PAGES = 10


class TodoCanadaScraper(BaseScraper):
    BASE_URL = "https://www.todocanada.ca"

    @property
    def supported_cities(self) -> set[str]:
        return set(TODOCANADA_CITY_SLUGS.keys())

    async def discover_pages(
        self, city_slug: str, ai_engine=None,
    ) -> list[dict]:
        """抓列表页 HTML，用 LLM 提取活动摘要"""
        slug = TODOCANADA_CITY_SLUGS.get(city_slug)
        if not slug:
            return []
        if not ai_engine:
            logger.warning("TodoCanada scraper requires ai_engine for LLM extraction")
            return []

        async with async_playwright() as p:
            browser = await launch_browser(p, get_settings())
            page = await browser.new_page()

            page_htmls: list[tuple[str, str]] = []

            for page_num in range(MAX_PAGES):
                url = (
                    f"{self.BASE_URL}/city/{slug}/events/page/{page_num}/"
                    if page_num > 1
                    else f"{self.BASE_URL}/city/{slug}/events/"
                )
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    if not resp or resp.status in (404, 403):
                        break
                except Exception as e:
                    logger.warning(f"Failed to load {url}: {e}")
                    break

                html = await page.content()
                page_htmls.append((url, html))
                await self._delay()

            await browser.close()

        if not page_htmls:
            return []

        seen_urls: set[str] = set()
        unique: list[dict] = []
        for url, html in page_htmls:
            summaries = await ai_engine.extract_list_events(
                html, city_slug, "todocanada", url,
            )
            for s in summaries:
                if s.get("url") and s["url"] not in seen_urls:
                    seen_urls.add(s["url"])
                    unique.append(s)

        logger.info(f"TodoCanada {city_slug}: discovered {len(unique)} events from {len(page_htmls)} pages")
        return unique

