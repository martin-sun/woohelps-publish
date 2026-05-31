#!/usr/bin/env python3
"""验证 realtor.ca API 数据结构完整性 + 详情页图片提取可行性"""
import asyncio, json
from loguru import logger
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context
from src.scrapers.realtorca import fetch_all_listings, fetch_detail_description
from src.scrapers.realtorca.detail import extract_public_remarks
import re

AGENT_ID = "2061436"  # 已知的经纪 ID
PROPERTY_ID = "29290701"  # 120 O Avenue S

async def analyze_api_data():
    """1. 分析 API 返回的完整数据结构"""
    logger.info("=" * 60)
    logger.info("【1】分析 API 返回的完整数据结构")
    logger.info("=" * 60)

    listings = await fetch_all_listings(AGENT_ID, delay=2.5)
    if not listings:
        logger.error("API 没有返回数据")
        return

    # 找到目标房源
    target = None
    for item in listings:
        if str(item.get("Id")) == PROPERTY_ID:
            target = item
            break

    if not target:
        logger.error(f"未找到 PropertyId={PROPERTY_ID}")
        return

    logger.info(f"房源 MLS: {target.get('MlsNumber')}")
    logger.info(f"房源地址: {target.get('Property', {}).get('Address', {}).get('AddressText')}")

    # 检查 API 是否包含 PublicRemarks
    remarks = target.get("PublicRemarks") or target.get("Property", {}).get("PublicRemarks")
    logger.info(f"API 中 PublicRemarks 长度: {len(remarks) if remarks else 0}")
    if remarks:
        logger.info(f"PublicRemarks 前 200 字: {remarks[:200]}")

    # 检查图片
    photos = target.get("Property", {}).get("Photo", [])
    logger.info(f"API 返回图片数量: {len(photos)}")
    for i, p in enumerate(photos[:3]):
        logger.info(f"  图片 {i+1}: {p.get('HighResPath', p.get('LowResPath', 'N/A'))[:80]}...")

    # 检查 Building 信息
    building = target.get("Building", {}) or {}
    land = target.get("Land", {}) or {}
    property_data = target.get("Property", {}) or {}

    logger.info(f"Building 字段: {list(building.keys())}")
    logger.info(f"Land 字段: {list(land.keys())}")
    logger.info(f"Property 顶层字段: {list(property_data.keys())}")

    # 检查是否还有其他媒体
    logger.info(f"是否有 Media: {'Media' in target}")
    logger.info(f"是否有 AlternateURL: {'AlternateURL' in target}")

    # 打印完整的 JSON 结构（去掉大段文本，只看键）
    def summarize_keys(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)) and k not in ["Photo", "PublicRemarks"]:
                    summarize_keys(v, f"{prefix}.{k}")
                elif k not in ["Photo", "PublicRemarks"]:
                    logger.info(f"  字段 {prefix}.{k}: {type(v).__name__}")

    logger.info("完整字段结构:")
    summarize_keys(target)

    return target


async def analyze_detail_page_images(property_url: str):
    """2. 分析详情页 HTML 中的图片"""
    logger.info("")
    logger.info("=" * 60)
    logger.info("【2】分析详情页 HTML 中的图片")
    logger.info("=" * 60)

    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings)
        page = await context.new_page()

        await page.goto(property_url, wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(2)

        # 方法 A：提取所有 img src
        all_imgs = await page.locator("img").evaluate_all("els => els.map(e => e.src).filter(s => s)")
        logger.info(f"页面总 <img> 数量: {len(all_imgs)}")

        # 过滤出 cdn.realtor.ca/listings 的图片
        listing_imgs = [src for src in all_imgs if "cdn.realtor.ca/listings" in src]
        logger.info(f"cdn.realtor.ca/listings 图片数量: {len(listing_imgs)}")

        # 去重
        unique_imgs = list(dict.fromkeys(listing_imgs))
        logger.info(f"去重后图片数量: {len(unique_imgs)}")

        # 打印前 10 张
        for i, url in enumerate(unique_imgs[:10]):
            logger.info(f"  图片 {i+1}: {url[:100]}...")

        # 方法 B：检查页面中是否有 photo gallery / carousel 的数据属性
        logger.info("")
        logger.info("【2b】检查页面中的图片 gallery 数据结构")

        # 检查是否有 JSON-LD 结构化数据包含图片
        json_ld = await page.locator('script[type="application/ld+json"]').evaluate_all(
            "els => els.map(e => e.textContent)"
        )
        for ld in json_ld:
            try:
                data = json.loads(ld)
                if isinstance(data, dict) and "image" in str(data).lower():
                    images = data.get("image", [])
                    if isinstance(images, list):
                        logger.info(f"JSON-LD 中包含 {len(images)} 张图片")
                        for img in images[:3]:
                            logger.info(f"  {img}")
                    break
            except:
                pass

        # 方法 C：检查是否有 data 属性或 JS 变量包含图片列表
        photo_data = await page.evaluate("""
            () => {
                // 检查常见的图片数据源
                const results = {};

                // 检查 window 全局变量
                for (const key of ['photos', 'images', 'listingPhotos', 'propertyPhotos']) {
                    if (window[key]) results[key] = Array.isArray(window[key]) ? window[key].length : 'not array';
                }

                // 检查包含图片数组的 script 标签内容
                const scripts = Array.from(document.querySelectorAll('script'));
                for (const s of scripts) {
                    const text = s.textContent || '';
                    if (text.includes('cdn.realtor.ca/listings') && text.includes('[')) {
                        // 尝试提取图片数组
                        const matches = text.match(/https:\/\/cdn\.realtor\.ca\/listings[^"'\s\]\)]+/g);
                        if (matches && matches.length > 5) {
                            results['script_photo_urls'] = [...new Set(matches)].length;
                        }
                    }
                }

                return results;
            }
        """)
        logger.info(f"页面 JS 中的图片数据: {photo_data}")

        await page.close()
        await context.close()
        await browser.close()

        return unique_imgs


async def compare_descriptions(property_url: str, api_remarks: str | None):
    """3. 比较 API 描述 vs 详情页描述"""
    logger.info("")
    logger.info("=" * 60)
    logger.info("【3】比较 API 描述 vs 详情页描述")
    logger.info("=" * 60)

    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings)
        page = await context.new_page()

        await page.goto(property_url, wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(2)

        body_text = await page.locator("body").inner_text(timeout=10_000)
        html_desc = extract_public_remarks(body_text)

        logger.info(f"API PublicRemarks 长度: {len(api_remarks) if api_remarks else 0}")
        logger.info(f"HTML 提取描述长度: {len(html_desc) if html_desc else 0}")

        if api_remarks and html_desc:
            api_first = api_remarks[:200].strip()
            html_first = html_desc[:200].strip()
            logger.info(f"API 前 200 字: {api_first}")
            logger.info(f"HTML 前 200 字: {html_first}")
            logger.info(f"两者是否相同: {api_first == html_first}")

        await page.close()
        await context.close()
        await browser.close()


async def main():
    target = await analyze_api_data()
    api_remarks = target.get("PublicRemarks") if target else None

    property_url = f"https://www.realtor.ca/real-estate/{PROPERTY_ID}/120-o-avenue-s-saskatoon-pleasant-hill"
    await analyze_detail_page_images(property_url)
    await compare_descriptions(property_url, api_remarks)


if __name__ == "__main__":
    asyncio.run(main())
