from datetime import datetime

from playwright.async_api import async_playwright

from loguru import logger

from src.models.activity import RawPage
from src.scrapers.base import BaseScraper

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


class TodoCanadaScraper(BaseScraper):
    BASE_URL = "https://www.todocanada.ca"

    @property
    def supported_cities(self) -> set[str]:
        return set(TODOCANADA_CITY_SLUGS.keys())

    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        slug = TODOCANADA_CITY_SLUGS.get(city_slug)
        if not slug:
            return []

        list_url = f"{self.BASE_URL}/city/{slug}/events/"

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await self._goto(page, list_url)

            detail_urls = await self._collect_links(
                page, rf"todocanada\.ca/city/{slug}/event/[^/]+/.*/?$"
            )

            # 处理分页
            for page_num in range(2, 27):
                next_url = f"{self.BASE_URL}/city/{slug}/events/page/{page_num}/"
                resp = await page.goto(next_url, wait_until="domcontentloaded", timeout=30_000)
                if not resp or resp.status == 404:
                    break
                more = await self._collect_links(
                    page, rf"todocanada\.ca/city/{slug}/event/[^/]+/.*/?$"
                )
                detail_urls.extend(more)

            detail_urls = list(set(detail_urls))
            logger.info(f"TodoCanada {city_slug}: found {len(detail_urls)} detail pages")

            pages = []
            for url in detail_urls:
                await self._delay()
                detail_page = await context.new_page()
                await self._goto(detail_page, url)
                html = await detail_page.content()
                og_image = await self._get_og_image(detail_page)
                await detail_page.close()

                pages.append(RawPage(
                    source="todocanada",
                    source_url=url,
                    raw_html=html,
                    city_slug=city_slug,
                    image_url=og_image,
                ))

            await context.close()
            await browser.close()
        return pages
