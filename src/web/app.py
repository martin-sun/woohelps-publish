import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.ai.engine import AIEngine
from src.config.settings import CITIES, get_settings
from src.main import (
    discover_city,
    fetch_selected_details,
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

_scrape_guard = asyncio.Lock()
_scrape_running = False


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
    _db = Database(settings.DB_PATH)
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
async def dashboard(request: Request):
    db = _get_db()
    stats = await db.count_by_city_and_status()
    candidate_stats = await db.count_candidates_by_city()
    tasks = await db.list_recent_scrape_tasks(10)
    return templates.TemplateResponse(request, "dashboard.html", {
        "stats": stats, "candidate_stats": candidate_stats, "tasks": tasks, "cities": CITIES,
    })


@app.get("/activities")
async def activities_page(request: Request, city: str = "", status: str = "", source: str = ""):
    db = _get_db()
    city_slug = city or None
    status_filter = status or None
    source_filter = source or None
    limit = 50
    activities = await db.list_activities(city_slug, status_filter, source_filter, limit=limit, offset=0)
    total = await db.count_activities(city_slug, status_filter, source_filter)
    total_pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse(request, "activities.html", {
        "activities": activities,
        "total": total,
        "total_pages": total_pages,
        "current_page": 1,
        "current_city": city,
        "current_status": status,
        "current_source": source,
        "cities": CITIES,
    })


@app.get("/activities/table")
async def activities_table(request: Request, city: str = "", status: str = "", source: str = "", page: int = 1):
    db = _get_db()
    city_slug = city or None
    status_filter = status or None
    source_filter = source or None
    limit = 50
    offset = (page - 1) * limit
    activities = await db.list_activities(city_slug, status_filter, source_filter, limit, offset)
    total = await db.count_activities(city_slug, status_filter, source_filter)
    total_pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse(request, "partials/activity_table.html", {
        "activities": activities,
        "total": total,
        "total_pages": total_pages,
        "current_page": page,
        "current_city": city,
        "current_status": status,
        "current_source": source,
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
        await publish_one(act["id"], db, publisher)

    return RedirectResponse(url="/activities", status_code=303)


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
    result = await publish_one(activity_id, db, publisher)
    if not result.get("success"):
        logger.error(f"Publish failed for {activity_id}: {result.get('error')}")
    return RedirectResponse(url=f"/activity/{activity_id}", status_code=303)


@app.get("/scrape")
async def scrape_page(request: Request):
    db = _get_db()
    tasks = await db.list_recent_scrape_tasks(20)
    return templates.TemplateResponse(request, "scrape.html", {
        "tasks": tasks, "cities": CITIES,
    })


@app.post("/scrape/start")
async def start_scrape(request: Request):
    global _scrape_running

    db = _get_db()
    form = await request.form()
    city_slugs = form.getlist("city_slugs")
    if not city_slugs:
        return RedirectResponse(url="/scrape", status_code=303)

    start_date_str = form.get("start_date", "")
    end_date_str = form.get("end_date", "")
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.now(timezone.utc).replace(tzinfo=None)
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else start_date + timedelta(days=30)
    except ValueError:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None)
        end_date = start_date + timedelta(days=30)

    async with _scrape_guard:
        if _scrape_running:
            return HTMLResponse("已有抓取任务正在运行，请等待完成", status_code=409)
        _scrape_running = True

    try:
        task_id = await db.create_scrape_task(city_slugs)
    except Exception:
        _scrape_running = False
        raise

    ai_engine = _ai_engine

    async def _run():
        global _scrape_running
        try:
            for city in city_slugs:
                await db.update_scrape_task(task_id, current_city=city)
                await discover_city(city, start_date, end_date, db, ai_engine)
            await db.complete_scrape_task(task_id)
        except Exception as e:
            logger.error(f"Scrape task {task_id} failed: {e}")
            await db.fail_scrape_task(task_id, str(e))
        finally:
            _scrape_running = False

    asyncio.create_task(_run())
    return RedirectResponse(url=f"/scrape?task={task_id}", status_code=303)


@app.get("/scrape/task/{task_id}/status")
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
    referer = request.headers.get("referer", "/discover")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/tasks/clear")
async def clear_tasks(request: Request):
    db = _get_db()
    await db.clear_scrape_tasks()
    referer = request.headers.get("referer", "/discover")
    return RedirectResponse(url=referer, status_code=303)


# --- Discover routes ---

_discover_guard = asyncio.Lock()
_discover_running = False


@app.get("/discover")
async def discover_page(request: Request):
    db = _get_db()
    tasks = await db.list_recent_scrape_tasks(20)
    return templates.TemplateResponse(request, "discover.html", {
        "tasks": tasks, "cities": CITIES,
    })


@app.post("/discover/start")
async def start_discover(request: Request):
    global _discover_running

    db = _get_db()
    form = await request.form()
    city_slugs = form.getlist("city_slugs")
    if not city_slugs:
        return RedirectResponse(url="/discover", status_code=303)

    start_date_str = form.get("start_date", "")
    end_date_str = form.get("end_date", "")
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.now(timezone.utc).replace(tzinfo=None)
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else start_date + timedelta(days=30)
    except ValueError:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None)
        end_date = start_date + timedelta(days=30)

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
                await discover_city(city, start_date, end_date, db, ai_engine)
            await db.complete_scrape_task(task_id)
        except Exception as e:
            logger.error(f"Discover task {task_id} failed: {e}")
            await db.fail_scrape_task(task_id, str(e))
        finally:
            _discover_running = False

    asyncio.create_task(_run())
    return RedirectResponse(url=f"/discover?task={task_id}", status_code=303)


# --- Candidate routes ---

@app.get("/candidates")
async def candidates_page(request: Request, city: str = "", ai_worth: str = "", status: str = ""):
    db = _get_db()
    city_slug = city or None
    ai_filter = None if ai_worth == "" else (ai_worth == "1")
    status_filter = status or None
    limit = 50
    candidates = await db.list_candidates(city_slug, ai_filter, status_filter, limit=limit, offset=0)
    total = await db.count_candidates(city_slug, ai_filter, status_filter)
    total_pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse(request, "candidates.html", {
        "candidates": candidates,
        "total": total,
        "total_pages": total_pages,
        "current_page": 1,
        "current_city": city,
        "current_ai_worth": ai_worth,
        "current_status": status,
        "cities": CITIES,
    })


@app.get("/candidates/table")
async def candidates_table(request: Request, city: str = "", ai_worth: str = "", status: str = "", page: int = 1):
    db = _get_db()
    city_slug = city or None
    ai_filter = None if ai_worth == "" else (ai_worth == "1")
    status_filter = status or None
    limit = 50
    offset = (page - 1) * limit
    candidates = await db.list_candidates(city_slug, ai_filter, status_filter, limit, offset)
    total = await db.count_candidates(city_slug, ai_filter, status_filter)
    total_pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse(request, "partials/candidate_table.html", {
        "candidates": candidates,
        "total": total,
        "total_pages": total_pages,
        "current_page": page,
        "current_city": city,
        "current_ai_worth": ai_worth,
        "current_status": status,
    })


@app.post("/candidates/select")
async def candidates_select(request: Request):
    db = _get_db()
    form = await request.form()
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.update_candidate_status(candidate_ids, "selected")
    return RedirectResponse(url="/candidates", status_code=303)


@app.post("/candidates/reject")
async def candidates_reject(request: Request):
    db = _get_db()
    form = await request.form()
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.update_candidate_status(candidate_ids, "rejected")
    return RedirectResponse(url="/candidates", status_code=303)


@app.post("/candidates/delete")
async def candidates_delete(request: Request):
    db = _get_db()
    form = await request.form()
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.delete_candidates(candidate_ids)
    return RedirectResponse(url="/candidates", status_code=303)


_fetch_guard = asyncio.Lock()
_fetch_running = False


@app.post("/candidates/fetch-details")
async def candidates_fetch_details(request: Request):
    global _fetch_running

    db = _get_db()
    form = await request.form()
    city = form.get("city", "")
    if not city:
        return HTMLResponse("请选择城市", status_code=400)

    start_date_str = form.get("start_date", "")
    end_date_str = form.get("end_date", "")
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.now(timezone.utc).replace(tzinfo=None)
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else start_date + timedelta(days=30)
    except ValueError:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None)
        end_date = start_date + timedelta(days=30)

    async with _fetch_guard:
        if _fetch_running:
            return HTMLResponse("已有详情抓取任务正在运行", status_code=409)
        _fetch_running = True

    ai_engine = _ai_engine

    async def _run():
        global _fetch_running
        try:
            await fetch_selected_details(city, start_date, end_date, db, ai_engine)
            logger.info(f"Fetch details complete for {city}")
        except Exception as e:
            logger.error(f"Fetch details failed for {city}: {e}")
        finally:
            _fetch_running = False

    asyncio.create_task(_run())
    return RedirectResponse(url=f"/candidates?city={city}", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
