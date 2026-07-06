import asyncio
import json
from datetime import datetime

from loguru import logger
from playwright.async_api import async_playwright

from src.ai.engine import AIEngine, COMMERCIAL_PROPERTY_TYPES, process_property
from src.config.settings import CITIES, get_settings
from src.models.property import PropertyCandidate, Property
from src.publisher.woohelps import WoohelpsPublisher
from src.scrapers.browser import launch_browser, new_stealth_context
from src.scrapers.realtorca import fetch_all_listings, fetch_city_listings, fetch_property_detail_page, parse_city_from_address
from src.storage.db import Database


async def scrape_agent(agent_row: dict, db: Database) -> dict:
    """爬取单个经纪的所有房源（Phase 1: API 列表），存入 property_candidates。

    返回 {"inserted": int, "updated": int, "price_changed": int, "delisted": int}
    """
    agent_id = agent_row["id"]
    realtor_agent_id = agent_row.get("agent_id", "")
    if not realtor_agent_id:
        raise ValueError(f"Agent {agent_row.get('name', agent_id)} 的 realtor.ca agent_id 为空，请先录入正确的 IndividualId")
    logger.info(f"Scraping agent {agent_row.get('name', '')} (agent_id={agent_id}, realtor_id={realtor_agent_id})")

    # 1. 创建 scrape_task
    task_id = await db.create_task(
        task_type="agent_listings",
        city_slugs=json.loads(agent_row.get("city_slugs", "[]")),
        detail=f"agent_id={agent_id}, realtor_id={realtor_agent_id}",
    )

    try:
        # 2. 调用 API 获取所有房源
        listings = await fetch_all_listings(realtor_agent_id, delay=2.5)
        current_ids = {item["Id"] for item in listings}

        # 3. 构建 candidates
        candidates = []
        for item in listings:
            address_data = item.get("Property", {}).get("Address", {})
            address_text = address_data.get("AddressText", "")
            city_slug = parse_city_from_address(address_text)

            photos = item.get("Property", {}).get("Photo", [])
            raw_urls = [p.get("HighResPath") or p.get("LowResPath") for p in photos if p.get("HighResPath") or p.get("LowResPath")]
            # 确保图片 URL 为绝对路径（API 可能返回相对路径）
            photo_urls = [
                url if url.startswith("http") else f"https://www.realtor.ca{url}"
                for url in raw_urls
            ]

            building = item.get("Building", {}) or {}
            property_data = item.get("Property", {}) or {}

            candidate = PropertyCandidate(
                city_slug=city_slug,
                agent_id=agent_id,
                source="realtorca",
                source_id=str(item["Id"]),
                source_url=f"https://www.realtor.ca{item.get('RelativeDetailsURL', '')}",
                mls_number=item.get("MlsNumber"),
                title=address_text.split("|")[0] if "|" in address_text else address_text,
                price=property_data.get("Price"),
                price_numeric=property_data.get("PriceUnformattedValue"),
                property_type=property_data.get("Type"),
                bedrooms=str(building.get("Bedrooms", "")),
                bathrooms=str(building.get("BathroomTotal", "")),
                address=address_text,
                postal_code=item.get("PostalCode", ""),
                latitude=address_data.get("Latitude"),
                longitude=address_data.get("Longitude"),
                photo_urls=photo_urls,
                open_house=item.get("OpenHouse", []),
            )
            candidates.append(candidate)

        # 4. 批量 upsert candidates
        inserted, updated, price_changed = await db.upsert_property_candidates(candidates)

        # 5. 增量更新：标记下架
        delisted = await db.incremental_update_candidates(agent_id, list(current_ids))

        # 6. 更新 task
        await db.update_scrape_task(
            task_id,
            status="completed",
            total_fetched=len(listings),
            total_new=inserted,
            new_candidates=inserted,
            updated_candidates=updated,
            delisted_candidates=delisted,
        )

        logger.info(f"Agent {agent_row['name']}: inserted={inserted}, updated={updated}, price_changed={price_changed}, delisted={delisted}")
        return {
            "inserted": inserted,
            "updated": updated,
            "price_changed": price_changed,
            "delisted": delisted,
        }

    except Exception as e:
        logger.error(f"Scrape agent {agent_row['name']} failed: {e}")
        await db.fail_scrape_task(task_id, str(e))
        raise


def _agent_info_from_raw_data(raw_data: dict) -> dict | None:
    """从 API 原始数据 (raw_data['api_individuals']) 中提取经纪信息。

    用于城市爬虫(agent_id=None)的房源，补充经纪名称/公司/电话。
    """
    individuals = raw_data.get("api_individuals", [])
    if not individuals:
        return None
    first = individuals[0]
    name = first.get("Name", "")
    org = first.get("Organization", {})
    brokerage = org.get("Name", "")
    phones = first.get("Phones", [])
    phone = ""
    if phones:
        area = phones[0].get("AreaCode", "")
        num = phones[0].get("PhoneNumber", "")
        phone = f"{area}-{num}" if area and num else num or area
    return {
        "name": name,
        "brokerage": brokerage,
        "phone": phone,
    }


async def scrape_city(city_slug: str, days: int, db: Database) -> dict:
    """按城市爬取最近 `days` 天上架的房源，存入 property_candidates。

    返回 {"inserted": int, "updated": int}
    """
    logger.info(f"Scraping city {city_slug} for last {days} days")

    task_id = await db.create_task(
        task_type="city_listings",
        city_slugs=[city_slug],
        detail=f"city={city_slug}, days={days}",
    )

    try:
        listings = await fetch_city_listings(city_slug, days=days, delay=2.5)

        candidates = []
        for item in listings:
            address_data = item.get("Property", {}).get("Address", {})
            address_text = address_data.get("AddressText", "")
            parsed_city = parse_city_from_address(address_text)

            photos = item.get("Property", {}).get("Photo", [])
            raw_urls = [p.get("HighResPath") or p.get("LowResPath") for p in photos if p.get("HighResPath") or p.get("LowResPath")]
            photo_urls = [
                url if url.startswith("http") else f"https://www.realtor.ca{url}"
                for url in raw_urls
            ]

            building = item.get("Building", {}) or {}
            property_data = item.get("Property", {}) or {}

            # 从 API 保留 Individual 信息到 raw_data，供后续经纪信息提取
            raw_data = {}
            individuals = item.get("Individual", [])
            if individuals:
                raw_data["api_individuals"] = individuals

            candidate = PropertyCandidate(
                city_slug=parsed_city,
                agent_id=None,
                source="realtorca",
                source_id=str(item["Id"]),
                source_url=f"https://www.realtor.ca{item.get('RelativeDetailsURL', '')}",
                mls_number=item.get("MlsNumber"),
                title=address_text.split("|")[0] if "|" in address_text else address_text,
                price=property_data.get("Price"),
                price_numeric=property_data.get("PriceUnformattedValue"),
                property_type=property_data.get("Type"),
                bedrooms=str(building.get("Bedrooms", "")),
                bathrooms=str(building.get("BathroomTotal", "")),
                address=address_text,
                postal_code=item.get("PostalCode", ""),
                latitude=address_data.get("Latitude"),
                longitude=address_data.get("Longitude"),
                photo_urls=photo_urls,
                open_house=item.get("OpenHouse", []),
                raw_data=raw_data,
            )
            candidates.append(candidate)

        inserted, updated = await db.upsert_city_property_candidates(candidates)

        await db.update_scrape_task(
            task_id,
            status="completed",
            total_fetched=len(listings),
            total_new=inserted,
            new_candidates=inserted,
            updated_candidates=updated,
        )

        logger.info(f"City {city_slug}: inserted={inserted}, updated={updated}")
        return {"inserted": inserted, "updated": updated}

    except Exception as e:
        logger.error(f"Scrape city {city_slug} failed: {e}")
        await db.fail_scrape_task(task_id, str(e))
        raise


async def fetch_property_details(agent_id: int | None, db: Database, ai_engine: AIEngine, candidate_ids: list[int] | None = None, city_slug: str | None = None):
    """抓取房源详情页描述并立即进行 AI 处理，一步完成。

    1. 查询 human_status='selected' 且 fetched_detail=FALSE 的候选
    2. 用 Playwright 抓取 description_en
    3. 立即调用 LLM 翻译/润色生成 Property
    4. 保存到 properties 表并关联 candidate
    """
    # 如果指定了 candidate_ids，先自动标记为 selected（与单条行为一致）
    if candidate_ids:
        await db.update_property_candidate_status(candidate_ids, "selected")

    candidates = await db.get_property_candidates_to_fetch(agent_id, candidate_ids=candidate_ids, city_slug=city_slug, limit=20)
    if not candidates:
        logger.info("No selected property candidates to fetch")
        return

    logger.info(f"Fetching & processing {len(candidates)} property detail pages")

    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings)
        shared_page = await context.new_page()

        for cand in candidates:
            try:
                # 1. 抓取详情页（描述 + 图片 + 结构化数据）
                detail_page = await fetch_property_detail_page(
                    cand["source_url"], context, max_retries=3, page=shared_page
                )
                description = detail_page.description
                photo_urls = detail_page.photo_urls
                raw_data = detail_page.raw_data

                # 合并 raw_data：保留 API 阶段已有的 api_individuals 等信息
                existing_raw_data = cand.get("raw_data") or {}
                if isinstance(existing_raw_data, str):
                    try:
                        existing_raw_data = json.loads(existing_raw_data) if existing_raw_data else {}
                    except Exception:
                        existing_raw_data = {}
                if existing_raw_data and isinstance(existing_raw_data, dict):
                    existing_raw_data.update(raw_data)
                    raw_data = existing_raw_data

                await db.update_candidate_description(cand["id"], description)
                await db.update_candidate_photos(cand["id"], photo_urls)
                await db.update_candidate_raw_data(cand["id"], raw_data)
                await db.mark_property_candidate_fetched(cand["id"])

                # 2. 构造 PropertyCandidate 对象
                candidate = PropertyCandidate(
                    id=cand["id"],
                    city_slug=cand["city_slug"],
                    agent_id=cand["agent_id"],
                    source=cand["source"],
                    source_id=cand["source_id"],
                    source_url=cand["source_url"],
                    mls_number=cand["mls_number"],
                    title=cand["title"],
                    price=cand["price"],
                    price_numeric=float(cand["price_numeric"]) if cand["price_numeric"] is not None else None,
                    property_type=cand["property_type"],
                    bedrooms=cand["bedrooms"],
                    bathrooms=cand["bathrooms"],
                    address=cand["address"],
                    postal_code=cand["postal_code"],
                    latitude=float(cand["latitude"]) if cand["latitude"] is not None else None,
                    longitude=float(cand["longitude"]) if cand["longitude"] is not None else None,
                    photo_urls=photo_urls,
                    open_house=json.loads(cand["open_house"]) if cand["open_house"] else [],
                    description_en=description,
                    raw_data=raw_data,
                )

                description_en = description or ""

                # 3. 查询经纪信息（优先 agents 表，城市爬虫 fallback 到 api_individuals）
                agent_row = await db.get_agent(cand["agent_id"]) if cand.get("agent_id") else None
                agent_info = None
                if agent_row:
                    agent_info = {
                        "name": agent_row.get("name", ""),
                        "brokerage": agent_row.get("brokerage", ""),
                        "phone": agent_row.get("phone", ""),
                    }
                elif not cand.get("agent_id"):
                    agent_info = _agent_info_from_raw_data(raw_data)

                # 4. AI 处理
                prop = await process_property(ai_engine, candidate, description_en, agent_info=agent_info)
                if prop:
                    property_id = await db.save_property(prop)
                    if property_id:
                        await db.link_candidate_to_property(cand["id"], property_id)
                        logger.info(f"Processed property {candidate.source_id} -> property_id={property_id}")
                    else:
                        logger.info(f"Property {candidate.source_id} already exists, linked to existing record")
                else:
                    logger.info(f"AI skipped property {candidate.source_id}")

                if description:
                    logger.info(f"Fetched detail for {cand['source_id']}: {len(description)} chars")
                else:
                    logger.warning(f"Empty description for {cand['source_url']}")

            except Exception as e:
                logger.error(f"Fetch & process failed for {cand['source_url']}: {e}")

        try:
            await shared_page.close()
        except Exception:
            pass
        await context.close()
        await browser.close()


async def process_property_candidates(
    agent_id: int | None,
    db: Database,
    ai_engine: AIEngine,
):
    """对 fetched_detail = TRUE 且 property_id IS NULL 的 candidate 进行 AI 处理。"""
    candidates = await db.get_property_candidates_to_process(agent_id, limit=20)

    if not candidates:
        logger.info("No property candidates to process")
        return

    logger.info(f"Processing {len(candidates)} property candidates with AI")

    for cand in candidates:
        try:
            # 构造 PropertyCandidate 对象
            candidate = PropertyCandidate(
                id=cand["id"],
                city_slug=cand["city_slug"],
                agent_id=cand["agent_id"],
                source=cand["source"],
                source_id=cand["source_id"],
                source_url=cand["source_url"],
                mls_number=cand["mls_number"],
                title=cand["title"],
                price=cand["price"],
                price_numeric=float(cand["price_numeric"]) if cand["price_numeric"] is not None else None,
                property_type=cand["property_type"],
                bedrooms=cand["bedrooms"],
                bathrooms=cand["bathrooms"],
                address=cand["address"],
                postal_code=cand["postal_code"],
                latitude=float(cand["latitude"]) if cand["latitude"] is not None else None,
                longitude=float(cand["longitude"]) if cand["longitude"] is not None else None,
                photo_urls=json.loads(cand["photo_urls"]) if cand["photo_urls"] else [],
                open_house=json.loads(cand["open_house"]) if cand["open_house"] else [],
                description_en=cand.get("description_en"),
                raw_data=json.loads(cand["raw_data"]) if cand.get("raw_data") else {},
            )

            description_en = cand.get("description_en") or ""

            # 查询经纪信息，传入 AI 处理
            agent_row = await db.get_agent(cand["agent_id"]) if cand.get("agent_id") else None
            agent_info = None
            if agent_row:
                agent_info = {
                    "name": agent_row.get("name", ""),
                    "brokerage": agent_row.get("brokerage", ""),
                    "phone": agent_row.get("phone", ""),
                }
            elif not cand.get("agent_id"):
                raw_data = cand.get("raw_data") or {}
                if isinstance(raw_data, str):
                    try:
                        raw_data = json.loads(raw_data) if raw_data else {}
                    except Exception:
                        raw_data = {}
                agent_info = _agent_info_from_raw_data(raw_data)

            prop = await process_property(ai_engine, candidate, description_en, agent_info=agent_info)
            if prop:
                property_id = await db.save_property(prop)
                if property_id:
                    await db.link_candidate_to_property(cand["id"], property_id)
                    logger.info(f"Processed property {candidate.source_id} -> property_id={property_id}")
                else:
                    logger.info(f"Property {candidate.source_id} already exists, linked to existing record")
            else:
                logger.info(f"AI skipped property {candidate.source_id}")

        except Exception as e:
            logger.error(f"Process property candidate {cand['id']} failed: {e}")


async def publish_one_property(
    property_id: int,
    db: Database,
    publisher: WoohelpsPublisher,
) -> dict:
    """发布单个房产到海外新生活"""
    prop_row = await db.get_property(property_id)
    if not prop_row:
        return {"error": "房产不存在"}
    if prop_row["status"] == "published":
        return {"error": "房产已发布"}

    # 城市映射：优先使用经纪所在城市，找不到再回退到物业城市
    city_slug = prop_row["city_slug"]
    agent_id = prop_row.get("agent_id")
    city_id = None

    if agent_id:
        agent_row = await db.get_agent(agent_id)
        if agent_row:
            agent_cities = json.loads(agent_row.get("city_slugs") or "[]")
            if agent_cities:
                first_agent_city = agent_cities[0]
                city_name = CITIES.get(first_agent_city, {}).get("name", first_agent_city)
                city_id = publisher.get_city_id(city_name)

    if not city_id:
        city_name = CITIES.get(city_slug, {}).get("name", city_slug)
        city_id = publisher.get_city_id(city_name)

    if not city_id:
        return {"error": f"平台未找到城市映射: {city_name}"}

    # 加载图片 URL
    image_urls = json.loads(prop_row["image_urls"]) if prop_row["image_urls"] else []

    # 加载 raw_data
    raw_data = {}
    if prop_row.get("raw_data"):
        try:
            raw_data = json.loads(prop_row["raw_data"]) if isinstance(prop_row["raw_data"], str) else prop_row["raw_data"]
        except Exception as e:
            logger.warning(f"Failed to parse raw_data for property {property_id}: {e}")
            raw_data = {}

    prop = Property(
        id=prop_row["id"],
        source=prop_row["source"],
        source_id=prop_row["source_id"],
        source_url=prop_row["source_url"],
        city_slug=prop_row["city_slug"],
        agent_id=prop_row["agent_id"],
        title_en=prop_row["title_en"],
        title_zh=prop_row["title_zh"],
        price=prop_row["price"],
        price_numeric=prop_row["price_numeric"],
        mls_number=prop_row["mls_number"],
        property_type=prop_row["property_type"],
        bedrooms=prop_row["bedrooms"],
        bathrooms=prop_row["bathrooms"],
        address=prop_row["address"],
        postal_code=prop_row["postal_code"],
        latitude=prop_row["latitude"],
        longitude=prop_row["longitude"],
        description_zh=prop_row["description_zh"],
        content_zh=prop_row["content_zh"],
        highlights=json.loads(prop_row["highlights"]) if prop_row["highlights"] else [],
        open_house=json.loads(prop_row["open_house"]) if prop_row["open_house"] else [],
        image_urls=image_urls,
        agent_name=prop_row["agent_name"],
        agent_brokerage=prop_row["agent_brokerage"],
        agent_phone=prop_row["agent_phone"],
        status=prop_row["status"],
        raw_data=raw_data,
    )

    # 根据房产类型选择发布渠道：商业发资讯，住宅发售房
    is_commercial = prop.property_type in COMMERCIAL_PROPERTY_TYPES
    if is_commercial:
        result = await publisher.publish_article(prop, city_id)
    else:
        result = await publisher.publish_property(prop, city_id)

    errcode = result.get("errcode", -1)
    if errcode == 0 or errcode in (101, 201):
        platform_id = result.get("data", {}).get("id") if isinstance(result.get("data"), dict) else None
        await db.mark_property_published(prop.source, prop.source_id, platform_id or 0)
        return {"success": True, "result": result}
    else:
        error_msg = result.get("errmsg", str(result))
        await db.mark_property_publish_failed(prop.source, prop.source_id, error_msg)
        return {"success": False, "error": error_msg, "result": result}


async def fetch_single_property(candidate_id: int, db: Database, ai_engine: AIEngine):
    """抓取单条房源详情并立即进行 AI 处理，一步完成。"""
    cand = await db.get_property_candidate(candidate_id)
    if not cand:
        logger.warning(f"Candidate {candidate_id} not found")
        return

    # 自动标记为 selected（如果还没选中）
    if cand["human_status"] != "selected":
        await db.update_property_candidate_status([candidate_id], "selected")
        cand["human_status"] = "selected"

    logger.info(f"Fetching & processing single property: {cand['source_id']}")

    settings = get_settings()
    async with async_playwright() as p:
        browser = await launch_browser(p, settings)
        context = await new_stealth_context(browser, settings)
        page = await context.new_page()

        try:
            # 1. 抓取详情页（描述 + 图片 + 结构化数据）
            detail_page = await fetch_property_detail_page(
                cand["source_url"], context, max_retries=3, page=page
            )
            description = detail_page.description
            detail_photo_urls = detail_page.photo_urls
            raw_data = detail_page.raw_data

            # 合并 raw_data：保留 API 阶段已有的 api_individuals 等信息
            existing_raw_data = json.loads(cand.get("raw_data") or "{}") if cand.get("raw_data") else {}
            if existing_raw_data:
                existing_raw_data.update(raw_data)
                raw_data = existing_raw_data

            await db.update_candidate_description(cand["id"], description)
            await db.update_candidate_photos(cand["id"], detail_photo_urls)
            await db.update_candidate_raw_data(cand["id"], raw_data)
            await db.mark_property_candidate_fetched(cand["id"])

            # 2. 构造 PropertyCandidate 对象
            candidate = PropertyCandidate(
                id=cand["id"],
                city_slug=cand["city_slug"],
                agent_id=cand["agent_id"],
                source=cand["source"],
                source_id=cand["source_id"],
                source_url=cand["source_url"],
                mls_number=cand["mls_number"],
                title=cand["title"],
                price=cand["price"],
                price_numeric=float(cand["price_numeric"]) if cand["price_numeric"] is not None else None,
                property_type=cand["property_type"],
                bedrooms=cand["bedrooms"],
                bathrooms=cand["bathrooms"],
                address=cand["address"],
                postal_code=cand["postal_code"],
                latitude=float(cand["latitude"]) if cand["latitude"] is not None else None,
                longitude=float(cand["longitude"]) if cand["longitude"] is not None else None,
                photo_urls=detail_photo_urls,
                open_house=json.loads(cand["open_house"]) if cand["open_house"] else [],
                description_en=description,
                raw_data=raw_data,
            )

            description_en = description or ""

            # 3. 查询经纪信息（优先 agents 表，城市爬虫 fallback 到 api_individuals）
            agent_row = await db.get_agent(cand["agent_id"]) if cand.get("agent_id") else None
            agent_info = None
            if agent_row:
                agent_info = {
                    "name": agent_row.get("name", ""),
                    "brokerage": agent_row.get("brokerage", ""),
                    "phone": agent_row.get("phone", ""),
                }
            elif not cand.get("agent_id"):
                agent_info = _agent_info_from_raw_data(raw_data)

            # 4. AI 处理
            logger.info(
                f"[DEBUG] candidate {candidate.source_id}: photos={len(candidate.photo_urls)}, "
                f"desc_len={len(description_en)}, price={candidate.price_numeric}, "
                f"type={candidate.property_type}, addr={candidate.address}, "
                f"beds={candidate.bedrooms}, baths={candidate.bathrooms}"
            )
            prop = await process_property(ai_engine, candidate, description_en, agent_info=agent_info)
            if prop:
                logger.info(f"[DEBUG] AI returned property for {candidate.source_id}: title_zh={prop.title_zh[:30]}...")
                property_id = await db.save_property(prop)
                logger.info(f"[DEBUG] save_property returned: {property_id} for {candidate.source_id}")
                if property_id:
                    await db.link_candidate_to_property(cand["id"], property_id)
                    logger.info(f"Processed single property {candidate.source_id} -> property_id={property_id}")
                else:
                    logger.warning(f"[DEBUG] save_property returned None for {candidate.source_id} — this should not happen unless insert failed silently")
            else:
                logger.info(f"[DEBUG] AI skipped/quality_gate rejected property {candidate.source_id}")

        finally:
            try:
                await page.close()
            except Exception:
                pass
            await context.close()
            await browser.close()
