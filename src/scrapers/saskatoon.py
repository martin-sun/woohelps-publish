from datetime import datetime

from playwright.async_api import async_playwright

from loguru import logger

from src.models.activity import RawPage
from src.scrapers.base import BaseScraper


class DiscoverSaskatoonScraper(BaseScraper):
    BASE_URL = "https://www.discoversaskatoon.com"
    SUPPORTED_CITIES = {"saskatoon"}

    @property
    def supported_cities(self) -> set[str]:
        return self.SUPPORTED_CITIES

    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        if city_slug not in self.SUPPORTED_CITIES:
            return []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            all_detail_urls: set[str] = set()
            for page_num in range(10):
                url = (
                    f"{self.BASE_URL}/calendar-events?page={page_num}"
                    if page_num > 0
                    else f"{self.BASE_URL}/calendar-events"
                )
                await self._goto(page, url)

                detail_urls = await self._collect_links(
                    page, r"discoversaskatoon\.com/calendar-events/[^/]+$"
                )
                new_urls = set(detail_urls) - all_detail_urls
                if not new_urls:
                    break
                all_detail_urls.update(new_urls)

            logger.info(f"DiscoverSaskatoon: found {len(all_detail_urls)} detail pages")

            pages = []
            for url in all_detail_urls:
                await self._delay()
                detail_page = await browser.new_page()
                await self._goto(detail_page, url)
                html = await detail_page.content()
                og_image = await self._get_og_image(detail_page)
                await detail_page.close()

                pages.append(RawPage(
                    source="discoversaskatoon",
                    source_url=url,
                    raw_html=html,
                    city_slug=city_slug,
                    image_url=og_image,
                ))

            await browser.close()
        return pages
