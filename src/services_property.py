import asyncio
import json
from datetime import datetime

from loguru import logger
from playwright.async_api import async_playwright

from src.ai.engine import AIEngine, process_property
from src.config.settings import CITIES, get_settings
from src.models.property import PropertyCandidate, Property
from src.publisher.woohelps import WoohelpsPublisher
from src.scrapers.browser import launch_browser, new_stealth_context
from src.scrapers.realtorca import fetch_all_listings, fetch_detail_description, parse_city_from_address
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
            land = item.get("Land", {}) or {}
            property_data = item.get("Property", {}) or {}

            # 解析停车信息
            parking_items = property_data.get("Parking", []) or []
            parking_str = ""
            if parking_items:
                parking_parts = []
                for p in parking_items:
                    name = p.get("Name", "")
                    spaces = p.get("Spaces", "")
                    if name:
                        part = name
                        if spaces:
                            part += f" ({spaces})"
                        parking_parts.append(part)
                parking_str = ", ".join(parking_parts)

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
                living_area=building.get("SizeInterior", ""),
                lot_size=land.get("SizeTotal", ""),
                year_built=str(building.get("YearBuilt", "")),
                stories=str(building.get("StoriesTotal", "")),
                features=land.get("LandscapeFeatures", ""),
                parking=parking_str,
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


async def fetch_property_details(agent_id: int | None, db: Database, ai_engine: AIEngine):
    """抓取房源详情页描述并立即进行 AI 处理，一步完成。

    1. 查询 human_status='selected' 且 fetched_detail=FALSE 的候选
    2. 用 Playwright 抓取 description_en
    3. 立即调用 LLM 翻译/润色生成 Property
    4. 保存到 properties 表并关联 candidate
    """
    candidates = await db.get_property_candidates_to_fetch(agent_id, limit=20)
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
                # 1. 抓取详情页英文描述 + 图片
                description, photo_urls = await fetch_detail_description(
                    cand["source_url"], context, max_retries=3, page=shared_page
                )
                await db.update_candidate_description(cand["id"], description)
                await db.update_candidate_photos(cand["id"], photo_urls)
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
                    living_area=cand["living_area"],
                    lot_size=cand["lot_size"],
                    year_built=cand.get("year_built"),
                    stories=cand.get("stories"),
                    features=cand.get("features"),
                    parking=cand.get("parking"),
                    address=cand["address"],
                    postal_code=cand["postal_code"],
                    latitude=float(cand["latitude"]) if cand["latitude"] is not None else None,
                    longitude=float(cand["longitude"]) if cand["longitude"] is not None else None,
                    photo_urls=photo_urls,
                    open_house=json.loads(cand["open_house"]) if cand["open_house"] else [],
                    description_en=description,
                )

                description_en = description or ""

                # 3. 查询经纪信息
                agent_row = await db.get_agent(cand["agent_id"]) if cand.get("agent_id") else None
                agent_info = None
                if agent_row:
                    agent_info = {
                        "name": agent_row.get("name", ""),
                        "brokerage": agent_row.get("brokerage", ""),
                        "phone": agent_row.get("phone", ""),
                    }

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
                living_area=cand["living_area"],
                lot_size=cand["lot_size"],
                year_built=cand.get("year_built"),
                stories=cand.get("stories"),
                features=cand.get("features"),
                parking=cand.get("parking"),
                address=cand["address"],
                postal_code=cand["postal_code"],
                latitude=float(cand["latitude"]) if cand["latitude"] is not None else None,
                longitude=float(cand["longitude"]) if cand["longitude"] is not None else None,
                photo_urls=json.loads(cand["photo_urls"]) if cand["photo_urls"] else [],
                open_house=json.loads(cand["open_house"]) if cand["open_house"] else [],
                description_en=cand.get("description_en"),
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

    city_slug = prop_row["city_slug"]
    city_eng_name = CITIES.get(city_slug, {}).get("eng_name", city_slug)
    city_id = publisher.get_city_id(city_eng_name)
    if not city_id:
        return {"error": f"平台未找到城市映射: {city_eng_name}"}

    # 加载图片 URL
    image_urls = json.loads(prop_row["image_urls"]) if prop_row["image_urls"] else []

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
        living_area=prop_row["living_area"],
        lot_size=prop_row["lot_size"],
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
    )

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
            # 1. 抓取详情页英文描述 + 图片
            description, detail_photo_urls = await fetch_detail_description(
                cand["source_url"], context, max_retries=3, page=page
            )
            await db.update_candidate_description(cand["id"], description)
            await db.update_candidate_photos(cand["id"], detail_photo_urls)
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
                living_area=cand["living_area"],
                lot_size=cand["lot_size"],
                year_built=cand.get("year_built"),
                stories=cand.get("stories"),
                features=cand.get("features"),
                parking=cand.get("parking"),
                address=cand["address"],
                postal_code=cand["postal_code"],
                latitude=float(cand["latitude"]) if cand["latitude"] is not None else None,
                longitude=float(cand["longitude"]) if cand["longitude"] is not None else None,
                photo_urls=detail_photo_urls,
                open_house=json.loads(cand["open_house"]) if cand["open_house"] else [],
                description_en=description,
            )

            description_en = description or ""

            # 3. 查询经纪信息
            agent_row = await db.get_agent(cand["agent_id"]) if cand.get("agent_id") else None
            agent_info = None
            if agent_row:
                agent_info = {
                    "name": agent_row.get("name", ""),
                    "brokerage": agent_row.get("brokerage", ""),
                    "phone": agent_row.get("phone", ""),
                }

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
