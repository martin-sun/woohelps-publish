from datetime import datetime

from playwright.async_api import async_playwright

from loguru import logger

from src.models.activity import RawPage
from src.scrapers.base import BaseScraper

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

    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        slug = FAMILYFUN_CITY_SLUGS.get(city_slug)
        if not slug:
            return []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            city_url = f"{self.BASE_URL}/{slug}/"
            await self._goto(page, city_url)
            article_urls = await self._collect_links(
                page, rf"familyfuncanada\.com/{slug}/[^/]+/.*/$"
            )

            skip_patterns = ["/category/", "/tag/", "/calendar/", "/page/"]
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
                    page, rf"familyfuncanada\.com/{slug}/[^/]+/.*/$"
                )
                more = [u for u in more if not any(p in u for p in skip_patterns)]
                article_urls.extend(more)

            article_urls = list(set(article_urls))
            logger.info(f"FamilyFunCanada {city_slug}: found {len(article_urls)} articles")

            pages = []
            for url in article_urls:
                await self._delay()
                detail_page = await browser.new_page()
                await self._goto(detail_page, url)
                html = await detail_page.content()
                og_image = await self._get_og_image(detail_page)
                await detail_page.close()

                pages.append(RawPage(
                    source="familyfuncanada",
                    source_url=url,
                    raw_html=html,
                    city_slug=city_slug,
                    image_url=og_image,
                ))

            await browser.close()
        return pages
