import asyncio
import random
from abc import ABC, abstractmethod
from datetime import datetime

from playwright.async_api import async_playwright, Page

from src.config.settings import CITIES
from src.models.activity import RawPage

# Playwright 配置
NAVIGATION_TIMEOUT = 30_000  # 30 seconds
REQUEST_DELAY_RANGE = (2, 5)  # 随机延迟范围（秒）


class BaseScraper(ABC):
    """爬虫基类 — 只负责页面导航和抓取原始 HTML"""

    @abstractmethod
    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        ...

    @property
    def supported_cities(self) -> set[str]:
        return set(CITIES.keys())

    @staticmethod
    async def _collect_links(page: Page, url_pattern: str) -> list[str]:
        """从列表页收集所有匹配 URL pattern 的链接"""
        links = await page.eval_on_selector_all(
            "a",
            f"els => els.map(a => a.href).filter(h => h.match({url_pattern!r}))"
        )
        return list(set(links))

    @staticmethod
    async def _get_og_image(page: Page) -> str | None:
        """提取页面 og:image"""
        el = await page.query_selector('meta[property="og:image"]')
        if el:
            return await el.get_attribute("content")
        return None

    @staticmethod
    async def _delay():
        """页面间的随机延迟，避免触发反爬"""
        await asyncio.sleep(random.uniform(*REQUEST_DELAY_RANGE))

    @staticmethod
    async def _goto(page: Page, url: str):
        """带超时的页面导航"""
        await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT)
