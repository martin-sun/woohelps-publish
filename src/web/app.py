import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.ai.engine import AIEngine
from src.config.settings import CITIES, get_settings
from src.services import (
    discover_city,
    fetch_one_candidate,
    publish_one,
)
from src.publisher.woohelps import WoohelpsPublisher
from src.storage.db import Database

DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(DIR, "templates"))

# --- Access Control ---

security = HTTPBasic(auto_error=False)


async def verify_auth(credentials: HTTPBasicCredentials | None = Security(security)):
    settings = get_settings()
    if not settings.ADMIN_PASSWORD:
        return
    if not credentials or credentials.password != settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})


# --- Global state ---

_db: Database | None = None
_ai_engine: AIEngine | None = None
_publisher: WoohelpsPublisher | None = None


def _get_db() -> Database:
    if _db is None:
        raise HTTPException(500, "Database not initialized")
    return _db


def _get_publisher() -> WoohelpsPublisher:
    if _publisher is None:
        raise HTTPException(500, "Publisher not initialized")
    return _publisher


# --- Template helpers ---


def _local_time(utc_str: str | None, tz_str: str | None) -> str:
    if not utc_str or not tz_str:
        return ""
    try:
        from zoneinfo import ZoneInfo
        utc_dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
        return utc_dt.astimezone(ZoneInfo(tz_str)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return utc_str


templates.env.globals["local_time"] = _local_time


# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db, _ai_engine, _publisher

    settings = get_settings()
    _db = Database(settings.DATABASE_URL)
    await _db.init()
    _ai_engine = AIEngine.from_settings(settings)

    _publisher = WoohelpsPublisher(
        base_url=settings.WOOHELPS_API_URL,
        login_session=settings.WOOHELPS_LOGIN_SESSION,
    )
    try:
        await _publisher.fetch_city_mapping()
    except Exception as e:
        logger.warning(f"Failed to fetch city mapping (publish will fail until fixed): {e}")

    # Reset stale running tasks
    tasks = await _db.list_recent_scrape_tasks()
    for t in tasks:
        if t["status"] == "running":
            await _db.fail_scrape_task(t["id"], "Server restarted")

    logger.info("Web app started")
    yield

    if _publisher:
        await _publisher.close()
    if _db:
        await _db.close()
    _db = None
    _publisher = None
    logger.info("Web app stopped")


app = FastAPI(dependencies=[Depends(verify_auth)], lifespan=lifespan)


# --- Routes ---


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/activities", status_code=307)


@app.get("/activities")
async def activities_page(request: Request, city: str = "", status: str = "", source: str = "", page: int = 1):
    db = _get_db()
    city_slug = city or None
    status_filter = status or None
    source_filter = source or None
    limit = 50
    offset = (page - 1) * limit
    activities = await db.list_activities(city_slug, status_filter, source_filter, limit, offset)
    total = await db.count_activities(city_slug, status_filter, source_filter)
    logger.info(f"[activities] city={city_slug}, status={status_filter}, source={source_filter}, total={total}, returned={len(activities)}")
    if activities:
        logger.info(f"[activities] first record: id={activities[0].get('id')}, source={activities[0].get('source')}, title={activities[0].get('title_zh', '')[:30]}")
    total_pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse(request, "activities.html", {
        "activities": activities,
        "total": total,
        "total_pages": total_pages,
        "current_page": page,
        "current_city": city,
        "current_status": status,
        "current_source": source,
        "cities": CITIES,
    })


@app.post("/activities/publish")
async def activities_publish(request: Request):
    db = _get_db()
    publisher = _get_publisher()
    form = await request.form()
    activity_ids = [int(x) for x in form.getlist("activity_ids")]
    if not activity_ids:
        return RedirectResponse(url="/activities", status_code=303)

    activities = await db.get_activities_by_ids(activity_ids)
    for act in activities:
        if act["status"] == "published":
            continue
        task_id = await db.create_task(
            "publish",
            detail=act["title_zh"][:60] if act.get("title_zh") else str(act["id"]),
        )

        async def _run(act=act, task_id=task_id):
            try:
                result = await publish_one(act["id"], db, publisher)
                if result.get("success"):
                    await db.complete_scrape_task(task_id)
                else:
                    await db.fail_scrape_task(task_id, result.get("error", "unknown"))
            except Exception as e:
                await db.fail_scrape_task(task_id, str(e))

        asyncio.create_task(_run())

    return RedirectResponse(url="/tasks", status_code=303)


@app.post("/activities/delete")
async def activities_delete(request: Request):
    db = _get_db()
    form = await request.form()
    activity_ids = [int(x) for x in form.getlist("activity_ids")]
    if activity_ids:
        await db.delete_activities(activity_ids)
    return RedirectResponse(url="/activities", status_code=303)


@app.get("/activity/{activity_id}")
async def activity_detail(request: Request, activity_id: int):
    db = _get_db()
    activity = await db.get_activity(activity_id)
    if not activity:
        raise HTTPException(404, "活动不存在")

    if isinstance(activity["image_urls"], str):
        activity["image_urls"] = json.loads(activity["image_urls"])
    if isinstance(activity["highlights"], str):
        activity["highlights"] = json.loads(activity["highlights"])

    return templates.TemplateResponse(request, "activity_detail.html", {
        "activity": activity,
    })


@app.post("/activity/{activity_id}/publish")
async def activity_publish(request: Request, activity_id: int):
    db = _get_db()
    publisher = _get_publisher()
    activity = await db.get_activity(activity_id)
    task_id = await db.create_task(
        "publish",
        detail=activity["title_zh"][:60] if activity and activity.get("title_zh") else str(activity_id),
    )

    async def _run():
        try:
            result = await publish_one(activity_id, db, publisher)
            if result.get("success"):
                await db.complete_scrape_task(task_id)
            else:
                await db.fail_scrape_task(task_id, result.get("error", "unknown"))
        except Exception as e:
            await db.fail_scrape_task(task_id, str(e))

    asyncio.create_task(_run())
    return RedirectResponse(url="/tasks", status_code=303)


@app.get("/tasks")
async def tasks_page(request: Request):
    db = _get_db()
    tasks = await db.list_recent_scrape_tasks(50)
    return templates.TemplateResponse(request, "tasks.html", {
        "tasks": tasks,
    })


@app.get("/tasks/{task_id}/status")
async def task_status(request: Request, task_id: int):
    db = _get_db()
    task = await db.get_scrape_task(task_id)
    if not task:
        return HTMLResponse("Task not found", status_code=404)
    return templates.TemplateResponse(request, "partials/task_row.html", {
        "task": task,
    })


@app.post("/tasks/delete/{task_id}")
async def delete_task(task_id: int, request: Request):
    db = _get_db()
    await db.delete_scrape_task(task_id)
    referer = request.headers.get("referer", "/activities/candidates")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/tasks/clear")
async def clear_tasks(request: Request):
    db = _get_db()
    await db.clear_scrape_tasks()
    referer = request.headers.get("referer", "/activities/candidates")
    return RedirectResponse(url=referer, status_code=303)


# --- Discover route (form embedded in candidates page) ---

_discover_guard = asyncio.Lock()
_discover_running = False


@app.post("/activities/discover/start")
async def start_discover(request: Request):
    global _discover_running

    db = _get_db()
    form = await request.form()
    city_slugs = form.getlist("city_slugs")
    if not city_slugs:
        return RedirectResponse(url="/activities/candidates", status_code=303)

    async with _discover_guard:
        if _discover_running:
            return HTMLResponse("已有发现任务正在运行，请等待完成", status_code=409)
        _discover_running = True

    try:
        task_id = await db.create_scrape_task(city_slugs)
    except Exception:
        _discover_running = False
        raise

    ai_engine = _ai_engine

    async def _run():
        global _discover_running
        try:
            for city in city_slugs:
                await db.update_scrape_task(task_id, current_city=city)
                await discover_city(city, db, ai_engine)
            await db.complete_scrape_task(task_id)
        except Exception as e:
            logger.error(f"Discover task {task_id} failed: {e}")
            await db.fail_scrape_task(task_id, str(e))
        finally:
            _discover_running = False

    asyncio.create_task(_run())
    return RedirectResponse(url="/tasks", status_code=303)


# --- Candidate routes ---

@app.get("/activities/candidates")
async def candidates_page(request: Request, city: str = "", ai_worth: str = "1", status: str = "", page: int = 1):
    db = _get_db()
    city_slug = city or None
    ai_failed = True if ai_worth == "failed" else None
    ai_filter = None if ai_worth in ("", "failed") else (ai_worth == "1")
    status_filter = status or None
    limit = 50
    offset = (page - 1) * limit
    candidates = await db.list_candidates(city_slug, ai_filter, ai_failed, status_filter, limit=limit, offset=offset)
    total = await db.count_candidates(city_slug, ai_filter, ai_failed, status_filter)
    total_pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse(request, "candidates.html", {
        "candidates": candidates,
        "total": total,
        "total_pages": total_pages,
        "current_page": page,
        "current_city": city,
        "current_ai_worth": ai_worth,
        "current_status": status,
        "cities": CITIES,
    })


@app.post("/activities/candidates/select")
async def candidates_select(request: Request):
    db = _get_db()
    form = await request.form()
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if not candidate_ids:
        return RedirectResponse(url="/activities/candidates", status_code=303)

    await db.update_candidate_status(candidate_ids, "selected")
    ai_engine = _ai_engine

    for cid in candidate_ids:
        cand = await db.get_candidate(cid)
        if not cand:
            continue
        task_id = await db.create_task(
            "fetch_details",
            detail=cand["source_url"][:80],
        )

        async def _run(cand=cand, task_id=task_id):
            try:
                new_count = await fetch_one_candidate(cand, db, ai_engine)
                await db.update_scrape_task(task_id, total_fetched=1, total_new=new_count)
                await db.complete_scrape_task(task_id)
            except Exception as e:
                logger.error(f"Fetch detail failed for {cand['source_url']}: {e}")
                await db.fail_scrape_task(task_id, str(e))

        asyncio.create_task(_run())

    return RedirectResponse(url="/tasks", status_code=303)


@app.post("/activities/candidates/reject")
async def candidates_reject(request: Request):
    db = _get_db()
    form = await request.form()
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.update_candidate_status(candidate_ids, "rejected")
    referer = request.headers.get("referer", "/activities/candidates")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/activities/candidates/delete")
async def candidates_delete(request: Request):
    db = _get_db()
    form = await request.form()
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.delete_candidates(candidate_ids)
    referer = request.headers.get("referer", "/activities/candidates")
    return RedirectResponse(url=referer, status_code=303)


_refilter_guard = asyncio.Lock()
_refilter_running = False


@app.post("/activities/candidates/refilter")
async def candidates_refilter(request: Request):
    global _refilter_running

    db = _get_db()
    form = await request.form()
    city = form.get("city", "") or None

    candidates = await db.get_candidates_to_refilter(city)
    if not candidates:
        logger.info(f"[refilter] No failed candidates for city={city}")
        return RedirectResponse(url="/activities/candidates", status_code=303)

    async with _refilter_guard:
        if _refilter_running:
            return HTMLResponse("已有重新过滤任务正在运行，请等待完成", status_code=409)
        _refilter_running = True

    task_id = await db.create_scrape_task([city] if city else [], task_type="refilter")
    ai_engine = _ai_engine

    async def _run():
        global _refilter_running
        try:
            await db.update_scrape_task(task_id, total_fetched=len(candidates))
            logger.info(f"[refilter] Re-filtering {len(candidates)} failed candidates for city={city}")

            summaries = [
                {
                    "title": c["title"],
                    "date": c["event_date"] or "",
                    "address": c["address"] or "",
                    "price": c["price"] or "",
                    "description": c["description"] or "",
                }
                for c in candidates
            ]

            city_slug = city or candidates[0]["city_slug"]
            results = await ai_engine.filter_activities(city_slug, summaries)

            for c, r in zip(candidates, results):
                worth = bool(r.get("worth_fetching", False))
                reason = r.get("reason", "")
                title_zh = r.get("title_zh", c.get("title_zh", ""))
                description_zh = r.get("description_zh", c.get("description_zh", ""))
                await db.update_candidate_ai_result(c["id"], worth, reason, title_zh, description_zh)

            worth_count = sum(1 for r in results if r.get("worth_fetching"))
            await db.update_scrape_task(task_id, total_new=worth_count)
            await db.complete_scrape_task(task_id)
            logger.info(f"[refilter] Done: {worth_count}/{len(candidates)} now worth fetching")
        except Exception as e:
            logger.error(f"[refilter] Failed: {e}")
            await db.fail_scrape_task(task_id, str(e))
        finally:
            _refilter_running = False

    asyncio.create_task(_run())
    referer = request.headers.get("referer", "/activities/candidates")
    return RedirectResponse(url=referer, status_code=303)


# --- Property placeholder routes ---

@app.get("/properties")
async def properties_page(request: Request):
    return templates.TemplateResponse(request, "placeholder.html", {
        "title": "房源列表",
        "message": "房源列表功能开发中",
    })


@app.get("/properties/candidates")
async def property_candidates_page(request: Request):
    return templates.TemplateResponse(request, "placeholder.html", {
        "title": "房源候选",
        "message": "房源候选功能开发中",
    })


# --- News placeholder routes ---

@app.get("/news")
async def news_page(request: Request):
    return templates.TemplateResponse(request, "placeholder.html", {
        "title": "新闻列表",
        "message": "新闻列表功能开发中",
    })


@app.get("/news/candidates")
async def news_candidates_page(request: Request):
    return templates.TemplateResponse(request, "placeholder.html", {
        "title": "新闻候选",
        "message": "新闻候选功能开发中",
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
