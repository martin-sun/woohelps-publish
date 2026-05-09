import re
from datetime import datetime

from playwright.async_api import async_playwright

from loguru import logger

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

    @staticmethod
    async def _get_max_page(page) -> int | None:
        """从分页控件读取最大页码，没有则返回 None"""
        # 优先找 "Last Page" 链接
        last_link = await page.query_selector("a.last.page-numbers")
        if last_link:
            href = await last_link.get_attribute("href") or ""
            m = re.search(r"/page/(\d+)/", href)
            if m:
                return int(m.group(1))

        # 否则从所有页码数字中取最大
        nums = await page.eval_on_selector_all(
            ".page-numbers",
            "els => els.map(el => parseInt(el.textContent)).filter(n => !isNaN(n))",
        )
        return max(nums) if nums else None

    @staticmethod
    async def _extract_list_summaries(page) -> list[dict]:
        """从列表页提取所有活动摘要信息"""
        cards = await page.query_selector_all("article, .event-item, .event-listing-item")
        summaries = []
        seen_urls = set()
        for card in cards:
            # 找卡片内的 event 链接
            link_el = await card.query_selector('a[href*="/event/"]')
            if not link_el:
                continue
            href = await link_el.get_attribute("href")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)

            title_el = await card.query_selector(".event-title h2, .entry-title")
            date_el = await card.query_selector(".event_date, [itemprop='startDate']")
            addr_el = await card.query_selector(".address, [itemprop='address']")
            price_el = await card.query_selector(".ampprice")
            desc_el = await card.query_selector(".entry-summary, .entry-content p")

            title = await title_el.inner_text() if title_el else ""
            date = await date_el.inner_text() if date_el else ""
            address = await addr_el.inner_text() if addr_el else ""
            price = await price_el.inner_text() if price_el else ""
            description = await desc_el.inner_text() if desc_el else ""

            summaries.append({
                "url": href,
                "title": title.strip(),
                "date": date.strip(),
                "address": address.strip(),
                "price": price.strip(),
                "description": description.strip(),
            })
        return summaries

    async def discover_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime,
        ai_engine=None,
    ) -> list[dict]:
        """只抓列表页，返回活动摘要列表（含 url/title/date/address/price/description）"""
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

            all_summaries: list[dict] = []
            page_1_summaries = await self._extract_list_summaries(page)
            all_summaries.extend(page_1_summaries)

            max_page = await self._get_max_page(page)
            if max_page is None:
                max_page = 26
            logger.info(f"TodoCanada {city_slug}: max page = {max_page}")

            for page_num in range(2, max_page + 1):
                next_url = f"{self.BASE_URL}/city/{slug}/events/page/{page_num}/"
                resp = await page.goto(next_url, wait_until="domcontentloaded", timeout=30_000)
                if not resp or resp.status in (404, 403):
                    break
                summaries = await self._extract_list_summaries(page)
                new_urls = {s["url"] for s in summaries} - {s["url"] for s in all_summaries}
                if not new_urls:
                    break
                all_summaries.extend(s for s in summaries if s["url"] in new_urls)

            logger.info(f"TodoCanada {city_slug}: discovered {len(all_summaries)} summaries")

            await context.close()
            await browser.close()
        return all_summaries

