import argparse
import asyncio
import signal
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src.ai.engine import AIEngine
from src.ai.sanitizer import sanitize_html
from src.config.settings import CITIES, get_settings
from src.dedup.deduplicator import compute_content_hash, compute_html_hash
from src.publisher.woohelps import parse_fee_amount
from src.scrapers.familyfun import FamilyFunCanadaScraper
from src.scrapers.saskatoon import DiscoverSaskatoonScraper
from src.scrapers.todocanada import TodoCanadaScraper
from src.storage.db import Database

SCRAPERS = {
    "todocanada": TodoCanadaScraper,
    "familyfuncanada": FamilyFunCanadaScraper,
    "discoversaskatoon": DiscoverSaskatoonScraper,
}


async def fetch_all_pages(
    city_slug: str, start_date: datetime, end_date: datetime
) -> list:
    """从所有数据源抓取原始页面"""
    all_pages = []
    for name, scraper_cls in SCRAPERS.items():
        scraper = scraper_cls()
        if city_slug not in scraper.supported_cities:
            continue
        try:
            pages = await scraper.fetch_pages(city_slug, start_date, end_date)
            all_pages.extend(pages)
        except Exception as e:
            logger.error(f"Scraper {name} failed for {city_slug}: {e}")
    return all_pages


async def process_city(
    city_slug: str, start_date: datetime, end_date: datetime,
    db: Database, ai_engine: AIEngine,
):
    """处理单个城市：抓取 → AI 处理 → 去重 → 存储"""
    logger.info(f"Processing city: {city_slug}")

    raw_pages = await fetch_all_pages(city_slug, start_date, end_date)
    logger.info(f"{city_slug}: fetched {len(raw_pages)} raw pages")

    for page in raw_pages:
        html_hash = compute_html_hash(page.raw_html)
        cached = await db.get_processed_page(page.source, page.source_url)
        if cached and cached["html_hash"] == html_hash and cached["status"] in ("success", "empty"):
            continue

        try:
            activities = await ai_engine.process(page)
        except Exception as e:
            await db.save_processed_page(page.source, page.source_url, html_hash, "failed")
            logger.error(f"LLM processing failed for {page.source_url}: {e}")
            continue

        new_count = 0
        for activity in activities:
            if not activity.start_time_utc:
                continue
            if activity.start_time_utc > end_date:
                continue

            if await db.exists(activity.source, activity.source_id):
                continue

            activity.content_hash = compute_content_hash(activity)
            if await db.exists_content_hash(activity.city_slug, activity.content_hash):
                continue

            fee_amount, fee_parsed_free = parse_fee_amount(activity.price)
            activity.fee_amount = fee_amount
            activity.fee_parsed_free = fee_parsed_free

            activity.html_zh = sanitize_html(activity.html_zh, page.source_url)

            await db.save(activity)
            new_count += 1

        await db.save_processed_page(
            page.source, page.source_url, html_hash,
            "success" if activities else "empty",
            activity_count=len(activities),
        )
        logger.info(f"{city_slug}/{page.source}: {len(activities)} activities, {new_count} new")


async def run_once(city: str | None = None):
    """单次运行全流程"""
    settings = get_settings()

    async with Database(settings.DB_PATH) as db:
        ai_engine = AIEngine.from_settings(settings)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        end_date = now + timedelta(days=30)

        cities = [city] if city else list(CITIES.keys())
        for city_slug in cities:
            await process_city(city_slug, now, end_date, db, ai_engine)

    logger.info("Run complete")


async def run_scheduled():
    """定时调度 — 每天 06:00 UTC（加拿大东部凌晨 1-2 点）"""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run_once, "cron", hour=6, minute=0, id="daily_scrape")
    scheduler.start()
    logger.info("Scheduler started: daily at 06:00 UTC (Canadian early morning)")

    stop_event = asyncio.Event()

    def _signal_handler(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()
    scheduler.shutdown()


def main():
    parser = argparse.ArgumentParser(description="加拿大活动自动发布系统")
    parser.add_argument("--city", help="只处理指定城市 (如 toronto)")
    parser.add_argument("--schedule", action="store_true", help="启动定时调度模式")
    args = parser.parse_args()

    if args.schedule:
        asyncio.run(run_scheduled())
    else:
        asyncio.run(run_once(city=args.city))


if __name__ == "__main__":
    main()
