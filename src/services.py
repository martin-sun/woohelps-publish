import asyncio
import json
from datetime import datetime, timezone

from loguru import logger

from src.ai.engine import AIEngine, DATE_WINDOW_DAYS, filter_by_date
from src.config.settings import CITIES, get_settings
from src.dedup.deduplicator import compute_content_hash, compute_html_hash
from src.publisher.woohelps import WoohelpsPublisher, parse_fee_amount
from src.scrapers.familyfun import FamilyFunCanadaScraper
from src.scrapers.todocanada import TodoCanadaScraper
from src.scrapers.browser import launch_browser, new_stealth_context
from src.storage.db import Database

SCRAPERS = {
    "todocanada": TodoCanadaScraper,
    "familyfuncanada": FamilyFunCanadaScraper,
}


async def discover_city(
    city_slug: str,
    db: Database, ai_engine: AIEngine,
):
    """只抓列表页摘要，AI 过滤，存入 candidate_activities"""
    logger.info(f"Discovering city: {city_slug}")

    for name, scraper_cls in SCRAPERS.items():
        scraper = scraper_cls()
        if city_slug not in scraper.supported_cities:
            continue
        try:
            summaries = await scraper.discover_pages(city_slug, ai_engine=ai_engine)
        except Exception as e:
            logger.error(f"Discover {name} failed for {city_slug}: {e}")
            continue

        if not summaries:
            continue

        before_date = len(summaries)
        summaries = filter_by_date(summaries)
        if len(summaries) < before_date:
            logger.info(f"{city_slug}/{name}: date filter removed {before_date - len(summaries)} events beyond {DATE_WINDOW_DAYS} days")

        logger.info(f"{city_slug}/{name}: discovered {len(summaries)} summaries")

        if ai_engine:
            try:
                summaries = await ai_engine.filter_activities(city_slug, summaries)
                logger.info(f"{city_slug}/{name}: AI filtered to {len(summaries)} summaries")
            except Exception as e:
                logger.error(f"AI filter failed for {city_slug}: {e}")

        # 转换为 candidate 格式并入库
        candidates = []
        for s in summaries:
            candidates.append({
                "city_slug": city_slug,
                "source": name,
                "source_url": s["url"],
                "title": s.get("title", ""),
                "title_zh": s.get("title_zh", ""),
                "event_date": s.get("date", ""),
                "address": s.get("address", ""),
                "price": s.get("price", ""),
                "description": s.get("description", ""),
                "description_zh": s.get("description_zh", ""),
                "ai_worth_fetching": s.get("worth_fetching", True),
                "ai_reason": s.get("reason", ""),
            })

        count = await db.save_candidates(candidates)
        logger.info(f"{city_slug}/{name}: saved {count} candidates")


async def fetch_selected_details(
    city_slug: str,
    db: Database, ai_engine: AIEngine,
):
    """从 candidate_activities 读取人工选中的活动，抓详情 + AI 处理 + 存储"""
    candidates = await db.get_candidates_to_fetch(city_slug)
    if not candidates:
        logger.info(f"No selected candidates to fetch for {city_slug}")
        return

    logger.info(f"{city_slug}: fetching {len(candidates)} selected detail pages")

    from playwright.async_api import async_playwright

    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings, city_slug=city_slug)

        for cand in candidates:
            try:
                raw_page = await _fetch_single_page(
                    context, cand["source"], cand["source_url"], cand["city_slug"],
                )
                if not raw_page:
                    continue

                html_hash = compute_html_hash(raw_page.raw_html)
                cached = await db.get_processed_page(raw_page.source, raw_page.source_url)
                if cached and cached["html_hash"] == html_hash and cached["status"] in ("success", "empty"):
                    await db.mark_candidate_fetched(cand["id"])
                    continue

                activities = await ai_engine.process(raw_page)

                new_count = 0
                saved_activity_id = None
                for activity in activities:
                    if await db.exists(activity.source, activity.source_id):
                        logger.info(f"[dedup] skipped by exists: source={activity.source}, source_id={activity.source_id}")
                        continue
                    activity.content_hash = compute_content_hash(activity)
                    if await db.exists_content_hash(activity.city_slug, activity.content_hash):
                        logger.info(f"[dedup] skipped by content_hash: city={activity.city_slug}")
                        continue

                    fee_amount, fee_parsed_free = parse_fee_amount(activity.price)
                    activity.fee_amount = fee_amount
                    activity.fee_parsed_free = fee_parsed_free

                    saved_activity_id = await db.save(activity)
                    new_count += 1

                    await db.mark_candidate_fetched(cand["id"], saved_activity_id)

                # 即使没有新 activity 插入，也标记已处理，避免重复抓取
                if new_count == 0:
                    await db.mark_candidate_fetched(cand["id"])

                await db.save_processed_page(
                    raw_page.source, raw_page.source_url, html_hash,
                    "success" if activities else "empty",
                    activity_count=len(activities),
                )
                logger.info(f"{city_slug}/{cand['source_url']}: {len(activities)} activities, {new_count} new")

            except Exception as e:
                logger.error(f"Fetch detail failed for {cand['source_url']}: {e}")

        await context.close()
        await browser.close()


async def fetch_one_candidate(cand: dict, db: Database, ai_engine: AIEngine) -> int:
    """抓取单个 candidate 的详情页 + AI 处理，返回新 activity 数量"""
    from playwright.async_api import async_playwright

    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings, city_slug=cand["city_slug"])
        try:
            raw_page = await _fetch_single_page(
                context, cand["source"], cand["source_url"], cand["city_slug"],
            )
            if not raw_page:
                await db.mark_candidate_fetched(cand["id"])
                return 0

            html_hash = compute_html_hash(raw_page.raw_html)
            cached = await db.get_processed_page(raw_page.source, raw_page.source_url)
            if cached and cached["html_hash"] == html_hash and cached["status"] in ("success", "empty"):
                await db.mark_candidate_fetched(cand["id"])
                return 0

            activities = await ai_engine.process(raw_page)

            new_count = 0
            saved_activity_id = None
            for activity in activities:
                if await db.exists(activity.source, activity.source_id):
                    continue
                activity.content_hash = compute_content_hash(activity)
                if await db.exists_content_hash(activity.city_slug, activity.content_hash):
                    continue

                fee_amount, fee_parsed_free = parse_fee_amount(activity.price)
                activity.fee_amount = fee_amount
                activity.fee_parsed_free = fee_parsed_free

                saved_activity_id = await db.save(activity)
                new_count += 1
                await db.mark_candidate_fetched(cand["id"], saved_activity_id)

            if new_count == 0:
                await db.mark_candidate_fetched(cand["id"])

            await db.save_processed_page(
                raw_page.source, raw_page.source_url, html_hash,
                "success" if activities else "empty",
                activity_count=len(activities),
            )
            return new_count
        finally:
            await context.close()
            await browser.close()


async def _fetch_single_page(context, source: str, url: str, city_slug: str) -> "RawPage | None":
    """用共享的 browser context 抓取单个详情页"""
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        html = await page.content()
        og_image = await page.query_selector('meta[property="og:image"]')
        image_url = await og_image.get_attribute("content") if og_image else None
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None
    finally:
        await page.close()

    from src.models.activity import RawPage
    return RawPage(
        source=source,
        source_url=url,
        raw_html=html,
        city_slug=city_slug,
        image_url=image_url,
    )


async def publish_one(
    activity_id: int, db: Database, publisher: WoohelpsPublisher,
) -> dict:
    """发布单个活动到海外新生活"""
    activity = await db.get_activity(activity_id)
    if not activity:
        return {"error": "活动不存在"}
    if activity["status"] == "published":
        return {"error": "活动已发布"}

    city_info = CITIES.get(activity["city_slug"])
    if not city_info:
        return {"error": f"未知城市: {activity['city_slug']}"}

    city_id = publisher.get_city_id(city_info["eng_name"])
    if not city_id:
        return {"error": f"平台未找到城市: {city_info['eng_name']}"}

    from src.models.activity import ProcessedActivity
    pa = ProcessedActivity(
        source=activity["source"],
        source_id=activity["source_id"],
        source_url=activity["source_url"],
        city_slug=activity["city_slug"],
        title_en=activity["title_en"],
        title_zh=activity["title_zh"],
        description_zh=activity["description_zh"],
        content_zh=activity["content_zh"],
        address=activity["address"],
        venue_name=activity["venue_name"],
        price=activity["price"],
        is_free=bool(activity["is_free"]),
        fee_amount=activity["fee_amount"],
        fee_parsed_free=bool(activity["fee_parsed_free"]),
        start_time_utc=datetime.fromisoformat(activity["start_time"]) if activity["start_time"] else None,
        end_time_utc=datetime.fromisoformat(activity["end_time"]) if activity["end_time"] else None,
        timezone=activity["timezone"],
        image_url=activity["image_url"],
        image_urls=json.loads(activity["image_urls"]) if isinstance(activity["image_urls"], str) else activity["image_urls"],
        highlights=json.loads(activity["highlights"]) if isinstance(activity["highlights"], str) else activity["highlights"],
        activity_type=activity["activity_type"],
        content_hash=activity["content_hash"],
        status=activity["status"],
    )

    result = await publisher.publish_activity(pa, city_id)
    errcode = result.get("errcode", -1)
    if errcode == 0 or errcode in (101, 201):
        platform_id = result.get("data", {}).get("id") if isinstance(result.get("data"), dict) else None
        await db.mark_published(activity["source"], activity["source_id"], platform_id or 0)
        return {"success": True, "result": result}
    else:
        error_msg = result.get("errmsg", str(result))
        await db.mark_publish_failed(activity["source"], activity["source_id"], error_msg)
        return {"success": False, "error": error_msg, "result": result}


async def run_once(city: str | None = None):
    """单次运行发现流程（列表页 + AI 过滤 → 存入候选，不自动抓详情）"""
    settings = get_settings()

    async with Database(settings.DATABASE_URL) as db:
        ai_engine = AIEngine.from_settings(settings)

        cities = [city] if city else list(CITIES.keys())
        for city_slug in cities:
            await discover_city(city_slug, db, ai_engine)

    logger.info("Discover complete")
