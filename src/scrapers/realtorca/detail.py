import asyncio
import re

from loguru import logger
from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeoutError


async def fetch_detail_description(
    source_url: str,
    context: BrowserContext,
    max_retries: int = 3,
    page=None,
) -> tuple[str, list[str]]:
    """访问详情页获取房源描述（PublicRemarks）和全部图片 URL

    返回: (description, photo_urls)
    - description: 房源英文描述文本
    - photo_urls: 从详情页 HTML 中提取的 cdn.realtor.ca/listings 图片 URL 列表（去重）

    错误处理策略：
    - Playwright 超时/断连：退避重试（2s, 4s, 6s）
    - 某条房源详情页失败：记录错误，返回空字符串+空列表，不阻塞其他房源
    - 连续失败 3 次：返回空字符串+空列表，由调度器重新拾起
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

            return description, photo_urls

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
    return "", []


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
