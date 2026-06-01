import asyncio
import os
import re
from dataclasses import dataclass, field

from loguru import logger
from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeoutError

# 预加载详情页提取器 JS 代码（避免 Python 多行字符串与 Playwright 的冲突）
_DETAIL_JS_PATH = os.path.join(os.path.dirname(__file__), "detail_extractor.js")
_DETAIL_JS = open(_DETAIL_JS_PATH).read().strip()


@dataclass
class PropertyDetailPage:
    """房源详情页提取结果"""
    description: str
    photo_urls: list[str]
    raw_data: dict = field(default_factory=dict)
    extraction_log: dict = field(default_factory=dict)


async def fetch_property_detail_page(
    source_url: str,
    context: BrowserContext,
    max_retries: int = 3,
    page=None,
) -> PropertyDetailPage:
    """访问详情页获取完整数据：描述、图片、结构化 raw_data

    返回 PropertyDetailPage 对象，包含:
    - description: 房源英文描述文本
    - photo_urls: 图片 URL 列表
    - raw_data: 从 HTML 提取的结构化数据 {summary, building, measurements, rooms, land}
    - extraction_log: 各 section 提取状态日志

    错误处理策略：
    - Playwright 超时/断连：退避重试（2s, 4s, 6s）
    - 某条房源详情页失败：记录错误，返回空对象，不阻塞其他房源
    - 连续失败 3 次：返回空对象，由调度器重新拾起
    """
    own_page = page is None

    for attempt in range(1, max_retries + 1):
        try:
            if own_page or page is None:
                page = await context.new_page()

            await page.goto(
                source_url,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await page.wait_for_load_state("networkidle", timeout=15_000)
            await asyncio.sleep(2)  # 等待懒加载/动态渲染

            # 1. 提取描述
            body_text = await page.locator("body").inner_text(timeout=10_000)
            description = extract_public_remarks(body_text)
            if description:
                logger.debug(f"Extracted description ({len(description)} chars) from {source_url}")
            else:
                logger.warning(f"Empty description for {source_url}, attempt {attempt}")

            # 2. 提取图片 URL
            photo_urls = await _extract_detail_photos(page)
            logger.debug(f"Extracted {len(photo_urls)} photos from {source_url}")

            # 3. 提取结构化数据
            raw_data, extraction_log = await _extract_raw_data(page)
            if raw_data:
                logger.debug(f"Extracted raw_data with keys {list(raw_data.keys())} from {source_url}")
            else:
                logger.warning(f"No raw_data extracted from {source_url}")

            # 校验：raw_data 非空且至少包含 summary/building 之一
            if not raw_data or ("summary" not in raw_data and "building" not in raw_data):
                logger.warning(
                    f"Raw data validation failed for {source_url}: "
                    f"keys={list(raw_data.keys()) if raw_data else 'empty'}, log={extraction_log}"
                )
                # 不阻断：返回空 raw_data，模板相应区块不渲染
                raw_data = {}

            return PropertyDetailPage(
                description=description,
                photo_urls=photo_urls,
                raw_data=raw_data,
                extraction_log=extraction_log,
            )

        except (PlaywrightTimeoutError, Exception) as e:
            logger.warning(f"Detail fetch failed for {source_url}, attempt {attempt}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(attempt * 2)
                if not own_page and page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = None
                continue
        finally:
            if own_page and page:
                await page.close()
                page = None

    logger.error(f"Detail fetch exhausted all retries for {source_url}")
    return PropertyDetailPage(description="", photo_urls=[])


async def _extract_detail_photos(page) -> list[str]:
    """从详情页提取所有 cdn.realtor.ca/listings 图片 URL，去重后返回"""
    try:
        all_imgs = await page.locator("img").evaluate_all(
            "els => els.map(e => e.src).filter(s => s && s.includes('cdn.realtor.ca/listings'))"
        )
        # 去重并保持顺序
        seen = set()
        unique = []
        for url in all_imgs:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique
    except Exception as e:
        logger.warning(f"Failed to extract photos from detail page: {e}")
        return []


async def _extract_raw_data(page) -> tuple[dict, dict]:
    """从详情页 HTML 提取结构化数据（Summary/Building/Measurements/Rooms/Land）。

    返回: (raw_data, extraction_log)
    """
    try:
        result = await page.evaluate(_DETAIL_JS)

        raw_data = result.get("data", {})
        extraction_log = result.get("log", {})
        return raw_data, extraction_log

    except Exception as e:
        logger.warning(f"Raw data extraction script failed: {e}")
        return {}, {"error": str(e)}


def extract_public_remarks(body_text: str) -> str:
    """从详情页 body text 中提取 PublicRemarks 描述

    策略：查找以常见描述词开头、长度 200-2000 字符的段落
    """
    starters = [
        r"Welcome to", r"Beautiful", r"Introducing", r"Stunning",
        r"Rare", r"Exceptional", r"Charming", r"Gorgeous",
        r"This", r"Located", r"Proudly", r"Discover",
    ]
    starter_pattern = "|".join(starters)

    # 按段落分割
    paragraphs = [p.strip() for p in body_text.split("\n") if len(p.strip()) > 200]

    for p in paragraphs:
        # 匹配以描述词开头的段落
        if re.match(rf"^(?:{starter_pattern})\b", p, re.IGNORECASE):
            # 清理多余的空白
            return re.sub(r"\s+", " ", p)

    # 兜底：返回最长段落
    if paragraphs:
        return max(paragraphs, key=len)

    return ""
