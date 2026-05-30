#!/usr/bin/env python3
import asyncio, json
from loguru import logger
from playwright.async_api import async_playwright
from src.config.settings import get_settings
from src.scrapers.browser import launch_browser, new_stealth_context

API_URL = "https://api2.realtor.ca/Listing.svc/AsyncPropertySearch_Post"

async def fetch():
    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings, city_slug="saskatoon")
        
        # 不访问任何页面，直接调用 API
        logger.info("直接调用 API（无前置页面访问）...")
        
        form_data = {
            "CurrentPage": "1",
            "RecordsPerPage": "11",
            "Sort": "6-D",
            "IndividualId": "2061436",
            "IncludePins": "1",
            "Currency": "CAD",
            "IncludeHiddenListings": "false",
            "ApplicationId": "1",
            "CultureId": "1",
            "Version": "7.0",
        }
        
        resp = await context.request.post(API_URL, form=form_data, headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.realtor.ca/map",
        })
        
        logger.info(f"API 响应状态: {resp.status}")
        text = await resp.text()
        if resp.status == 200:
            try:
                data = json.loads(text)
                paging = data['Paging']
                logger.info(f"分页: 第{paging['CurrentPage']}页, 共{paging['TotalPages']}页, 总计{paging['TotalRecords']}条, 本页{len(data['Results'])}条")
                for r in data['Results'][:3]:
                    addr = r.get("Property", {}).get("Address", {}).get("AddressText", "N/A")
                    mls = r.get("MlsNumber", "N/A")
                    logger.info(f"  - MLS:{mls} {addr}")
                return True
            except json.JSONDecodeError:
                logger.error(f"JSON 解析失败: {text[:500]}")
                return False
        else:
            logger.error(f"API 调用失败: {resp.status}")
            logger.info(f"响应: {text[:500]}")
            return False

if __name__ == "__main__":
    ok = asyncio.run(fetch())
    exit(0 if ok else 1)
