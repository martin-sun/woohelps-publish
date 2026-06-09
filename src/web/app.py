import asyncio
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from loguru import logger
from starlette.datastructures import FormData
from starlette.middleware.base import BaseHTTPMiddleware


async def get_form(request: Request) -> FormData:
    """获取表单数据，优先使用 middleware 缓存的结果（避免 BaseHTTPMiddleware 消耗 body 的问题）。"""
    if "_form" in request.scope:
        return request.scope["_form"]
    return await request.form()

from src.ai.engine import AIEngine
from src.config.settings import CITIES, get_settings

from src.services import (
    discover_city,
    fetch_one_candidate,
    publish_one,
)
from src.services_property import (
    scrape_agent,
    scrape_city,
    fetch_property_details,
    fetch_single_property,
    process_property_candidates,
    publish_one_property,
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


# --- Fetch Worker (单线程队列，避免并发 Playwright) ---
_fetch_queue = asyncio.Queue()


async def _fetch_worker():
    """后台 worker：从队列中串行执行房源抓取任务"""
    db = _get_db()
    ai_engine = _ai_engine
    while True:
        item = await _fetch_queue.get()
        try:
            task_id = item["task_id"]
            if item.get("candidate_id") is not None:
                await fetch_single_property(item["candidate_id"], db, ai_engine)
                await db.complete_scrape_task(task_id)
            else:
                await fetch_property_details(
                    item.get("agent_id"), db, ai_engine,
                    candidate_ids=item.get("candidate_ids"),
                    city_slug=item.get("city_slug"),
                )
                await db.complete_scrape_task(task_id)
        except Exception as e:
            logger.error(f"Fetch worker task failed: {e}")
            try:
                await db.fail_scrape_task(item["task_id"], str(e))
            except Exception as db_err:
                logger.error(f"Failed to mark task as failed: {db_err}")
        finally:
            _fetch_queue.task_done()


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
        user_id=getattr(settings, "WOOHELPS_USER_ID", "1"),
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

    _fetch_worker_task = asyncio.create_task(_fetch_worker())
    yield

    _fetch_worker_task.cancel()
    try:
        await _fetch_worker_task
    except asyncio.CancelledError:
        pass

    if _publisher:
        await _publisher.close()
    if _db:
        await _db.close()
    _db = None
    _publisher = None
    logger.info("Web app stopped")


app = FastAPI(dependencies=[Depends(verify_auth)], lifespan=lifespan)


# --- CSRF Protection ---

class CSRFMiddleware(BaseHTTPMiddleware):
    """简单的 CSRF 防护中间件：

    1. 为每个响应设置 csrf_token cookie（若不存在）
    2. 对 POST/PUT/DELETE 请求校验 form 中的 csrf_token 与 cookie 是否匹配
    """

    @staticmethod
    def _generate_token() -> str:
        secret = get_settings().ADMIN_PASSWORD or os.urandom(32).hex()
        return hmac.new(
            secret.encode(),
            secrets.token_bytes(16),
            "sha256",
        ).hexdigest()[:32]

    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get("csrf_token")
        if not token:
            token = self._generate_token()

        # 对写操作校验 CSRF
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            form_token = ""
            content_type = request.headers.get("content-type", "")
            # 1. 优先从 header 读取（fetch/htmx 请求）
            form_token = request.headers.get("X-CSRF-Token", "")
            # 2. 从表单读取（普通表单提交），并缓存到 scope 供 handler 复用
            if not form_token and ("application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type):
                try:
                    form = await get_form(request)
                    request.scope["_form"] = form
                    form_token = form.get("csrf_token", "")
                except Exception:
                    pass
            if not hmac.compare_digest(token, form_token):
                logger.warning(f"CSRF validation failed for {request.url.path}")
                raise HTTPException(status_code=403, detail="CSRF token mismatch")

        response = await call_next(request)

        # 确保 cookie 存在
        if not request.cookies.get("csrf_token"):
            response.set_cookie(
                key="csrf_token",
                value=token,
                httponly=False,   # 需要前端 JS 读取并注入表单
                samesite="Lax",
                secure=False,     # 本地开发兼容
                max_age=86400 * 7,
            )
        return response


app.add_middleware(CSRFMiddleware)


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
    form = await get_form(request)
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
    form = await get_form(request)
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
    form = await get_form(request)
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
    form = await get_form(request)
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
    form = await get_form(request)
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.update_candidate_status(candidate_ids, "rejected")
    referer = request.headers.get("referer", "/activities/candidates")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/activities/candidates/delete")
async def candidates_delete(request: Request):
    db = _get_db()
    form = await get_form(request)
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
    form = await get_form(request)
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


# --- City routes ---

@app.get("/cities")
async def cities_page(request: Request):
    db = _get_db()
    candidate_counts = {}
    for slug in CITIES:
        candidate_counts[slug] = await db.count_property_candidates(
            agent_id=None, city_slug=slug
        )
    return templates.TemplateResponse(request, "cities.html", {
        "cities": CITIES,
        "candidate_counts": candidate_counts,
        "running_cities": _city_scrape_running,
    })


# --- Agent routes ---

@app.get("/agents")
async def agents_page(request: Request):
    db = _get_db()
    agents = await db.list_agents()
    return templates.TemplateResponse(request, "agents.html", {
        "agents": agents,
        "cities": CITIES,
    })


@app.get("/agents/add")
async def agents_add_page(request: Request):
    return templates.TemplateResponse(request, "agent_form.html", {
        "agent": None,
        "cities": CITIES,
    })


@app.post("/agents/create")
async def agents_create(request: Request):
    db = _get_db()
    form = await get_form(request)
    from src.models.property import Agent
    agent = Agent(
        agent_id=form.get("agent_id", ""),
        name=form.get("name", ""),
        name_zh=form.get("name_zh") or None,
        brokerage=form.get("brokerage") or None,
        phone=form.get("phone") or None,
        email=form.get("email") or None,
        city_slugs=[s.strip() for s in form.get("city_slugs", "").split(",") if s.strip()],
        province_code=form.get("province_code") or None,
        is_active=form.get("is_active") == "on",
        notes=form.get("notes") or None,
    )
    await db.save_agent(agent)
    return RedirectResponse(url="/agents", status_code=303)


@app.get("/agents/{agent_id}/edit")
async def agents_edit_page(request: Request, agent_id: int):
    db = _get_db()
    agent_row = await db.get_agent(agent_id)
    if not agent_row:
        return RedirectResponse(url="/agents", status_code=303)
    return templates.TemplateResponse(request, "agent_form.html", {
        "agent": agent_row,
        "cities": CITIES,
    })


@app.post("/agents/{agent_id}/update")
async def agents_update(agent_id: int, request: Request):
    db = _get_db()
    form = await get_form(request)
    from src.models.property import Agent
    agent = Agent(
        id=agent_id,
        agent_id=form.get("agent_id", ""),
        name=form.get("name", ""),
        name_zh=form.get("name_zh") or None,
        brokerage=form.get("brokerage") or None,
        phone=form.get("phone") or None,
        email=form.get("email") or None,
        city_slugs=[s.strip() for s in form.get("city_slugs", "").split(",") if s.strip()],
        province_code=form.get("province_code") or None,
        is_active=form.get("is_active") == "on",
        notes=form.get("notes") or None,
    )
    try:
        await db.update_agent(agent)
    except Exception as e:
        logger.error(f"Update agent {agent_id} failed: {e}")
        return HTMLResponse(f"保存失败: {e}", status_code=500)
    return RedirectResponse(url="/agents", status_code=303)


_agent_scrape_guard = asyncio.Lock()
_agent_scrape_running: set[int] = set()


@app.post("/agents/{agent_id}/scrape")
async def agents_scrape(agent_id: int, request: Request):
    db = _get_db()
    agent = await db.get_agent(agent_id)
    if not agent:
        return RedirectResponse(url="/agents", status_code=303)

    async with _agent_scrape_guard:
        if agent_id in _agent_scrape_running:
            return HTMLResponse("该经纪已有抓取任务正在运行，请等待完成", status_code=409)
        _agent_scrape_running.add(agent_id)

    async def _run():
        try:
            from src.services_property import scrape_agent
            await scrape_agent(agent, db)
        except Exception as e:
            logger.error(f"Scrape agent {agent_id} failed: {e}")
        finally:
            _agent_scrape_running.discard(agent_id)

    asyncio.create_task(_run())
    return RedirectResponse(url="/tasks", status_code=303)


# --- City scrape ---
_city_scrape_guard = asyncio.Lock()
_city_scrape_running: set[str] = set()


@app.post("/properties/city-scrape")
async def properties_city_scrape(request: Request):
    db = _get_db()
    form = await get_form(request)
    city_slug = form.get("city_slug", "").strip()
    try:
        days = int(form.get("days", "1") or "1")
    except ValueError:
        return HTMLResponse("天数必须是数字", status_code=400)

    if not city_slug or city_slug not in CITIES:
        return HTMLResponse(f"无效城市: {city_slug}", status_code=400)
    if days < 1 or days > 30:
        return HTMLResponse("天数必须在 1-30 之间", status_code=400)

    async with _city_scrape_guard:
        if city_slug in _city_scrape_running:
            return HTMLResponse(f"{city_slug} 的城市抓取任务正在运行，请等待完成", status_code=409)
        _city_scrape_running.add(city_slug)

    async def _run():
        try:
            await scrape_city(city_slug, days, db)
        except Exception as e:
            logger.error(f"Scrape city {city_slug} failed: {e}")
        finally:
            _city_scrape_running.discard(city_slug)

    asyncio.create_task(_run())
    return RedirectResponse(url="/tasks", status_code=303)


@app.post("/agents/{agent_id}/delete")
async def agents_delete(agent_id: int, request: Request):
    db = _get_db()
    await db.delete_agent(agent_id)
    return RedirectResponse(url="/agents", status_code=303)


# --- Property routes ---

@app.get("/properties")
async def properties_page(request: Request, city: str = "", status: str = "", agent: str = "", category: str = "", page: int = 1):
    db = _get_db()
    city_slug = city or None
    status_filter = status or None
    agent_id = int(agent) if agent else None
    category_filter = category or None
    limit = 50
    offset = (page - 1) * limit
    properties = await db.list_properties(city_slug, status_filter, agent_id, category_filter, limit, offset)
    total = await db.count_properties(city_slug, status_filter, agent_id, category_filter)
    total_pages = max(1, (total + limit - 1) // limit)
    agents = await db.list_agents(is_active=True)
    return templates.TemplateResponse(request, "properties.html", {
        "properties": properties,
        "total": total,
        "total_pages": total_pages,
        "current_page": page,
        "current_city": city,
        "current_status": status,
        "current_agent": agent,
        "current_category": category,
        "cities": CITIES,
        "agents": agents,
    })


@app.get("/properties/candidates")
async def property_candidates_page(request: Request, agent: str = "", city: str = "", status: str = "", page: int = 1):
    db = _get_db()
    agent_id = int(agent) if agent else None
    city_slug = city or None
    status_filter = status or None
    limit = 50
    offset = (page - 1) * limit
    candidates = await db.list_property_candidates(agent_id, None, status_filter, city_slug, limit, offset)
    total = await db.count_property_candidates(agent_id, None, status_filter, city_slug)
    total_pages = max(1, (total + limit - 1) // limit)
    agents = await db.list_agents(is_active=True)
    return templates.TemplateResponse(request, "property_candidates.html", {
        "candidates": candidates,
        "total": total,
        "total_pages": total_pages,
        "current_page": page,
        "current_agent": agent,
        "current_city": city,
        "current_status": status,
        "agents": agents,
        "cities": CITIES,
    })


@app.post("/properties/candidates/select")
async def property_candidates_select(request: Request):
    db = _get_db()
    form = await get_form(request)
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.update_property_candidate_status(candidate_ids, "selected")
    referer = request.headers.get("referer", "/properties/candidates")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/properties/candidates/reject")
async def property_candidates_reject(request: Request):
    db = _get_db()
    form = await get_form(request)
    candidate_ids = [int(x) for x in form.getlist("candidate_ids")]
    if candidate_ids:
        await db.update_property_candidate_status(candidate_ids, "rejected")
    referer = request.headers.get("referer", "/properties/candidates")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/properties/candidates/fetch")
async def property_candidates_fetch(request: Request):
    db = _get_db()
    form = await get_form(request)
    agent_id = int(form.get("agent_id") or 0) or None
    city_slug = form.get("city", "").strip() or None
    candidate_ids_raw = form.getlist("candidate_ids")
    candidate_ids = [int(x) for x in candidate_ids_raw] if candidate_ids_raw else None

    task_detail = f"agent_id={agent_id}"
    if city_slug:
        task_detail += f", city={city_slug}"
    if candidate_ids:
        task_detail += f", candidate_ids={candidate_ids}"
    task_id = await db.create_task("fetch_and_process_property", detail=task_detail)

    await _fetch_queue.put({
        "task_id": task_id,
        "agent_id": agent_id,
        "city_slug": city_slug,
        "candidate_ids": candidate_ids,
    })
    return RedirectResponse(url="/tasks", status_code=303)


@app.post("/properties/candidates/{candidate_id}/fetch")
async def property_candidate_fetch_single(request: Request, candidate_id: int):
    """单条房源：抓取详情并立即 AI 处理"""
    db = _get_db()

    task_id = await db.create_task("fetch_and_process_property", detail=f"candidate_id={candidate_id}")

    await _fetch_queue.put({
        "task_id": task_id,
        "candidate_id": candidate_id,
    })
    referer = request.headers.get("referer", "/properties/candidates")
    return RedirectResponse(url=referer, status_code=303)


@app.get("/property/{property_id}")
async def property_detail_page(request: Request, property_id: int):
    db = _get_db()
    prop = await db.get_property(property_id)
    if not prop:
        return RedirectResponse(url="/properties", status_code=303)

    if isinstance(prop.get("image_urls"), str):
        prop["image_urls"] = json.loads(prop["image_urls"])
    if isinstance(prop.get("highlights"), str):
        prop["highlights"] = json.loads(prop["highlights"])
    # raw_data 在 db.get_property 中已解析为 dict；兼容旧数据（无 raw_data 字段）
    if isinstance(prop.get("raw_data"), str):
        try:
            prop["raw_data"] = json.loads(prop["raw_data"])
        except Exception:
            prop["raw_data"] = {}
    elif prop.get("raw_data") is None:
        prop["raw_data"] = {}

    return templates.TemplateResponse(request, "property_detail.html", {"property": prop})


@app.post("/properties/publish")
async def properties_publish(request: Request):
    db = _get_db()
    publisher = _get_publisher()
    form = await get_form(request)
    property_ids = [int(x) for x in form.getlist("property_ids")]
    if not property_ids:
        return RedirectResponse(url="/properties", status_code=303)

    properties = await db.get_properties_by_ids(property_ids)
    for prop in properties:
        if prop["status"] == "published":
            continue
        task_id = await db.create_task(
            "publish_property",
            detail=prop["title_zh"][:60] if prop.get("title_zh") else str(prop["id"]),
        )

        async def _run(prop=prop, task_id=task_id):
            try:
                result = await publish_one_property(prop["id"], db, publisher)
                if result.get("success"):
                    await db.complete_scrape_task(task_id)
                else:
                    await db.fail_scrape_task(task_id, result.get("error", "unknown"))
            except Exception as e:
                await db.fail_scrape_task(task_id, str(e))

        asyncio.create_task(_run())

    return RedirectResponse(url="/tasks", status_code=303)


@app.post("/properties/{property_id}/publish")
async def property_publish(request: Request, property_id: int):
    db = _get_db()
    publisher = _get_publisher()
    task_id = await db.create_task("publish_property", detail=f"property_id={property_id}")

    async def _run():
        try:
            result = await publish_one_property(property_id, db, publisher)
            if result.get("success"):
                await db.complete_scrape_task(task_id)
            else:
                await db.fail_scrape_task(task_id, result.get("error", "unknown"))
        except Exception as e:
            await db.fail_scrape_task(task_id, str(e))

    asyncio.create_task(_run())
    return RedirectResponse(url="/tasks", status_code=303)


# --- Health & Alert ---


async def alert_if_needed(db: Database) -> list[str]:
    """基于 scrape_tasks 的健康检查与简单告警。

    返回告警消息列表（空列表表示无告警）。
    """
    alerts: list[str] = []
    now = datetime.now(timezone.utc)

    # 1. 今日未完成任务
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    incomplete = await db.list_recent_scrape_tasks(since=today_start)
    incomplete = [t for t in incomplete if t["status"] != "completed"]
    if incomplete:
        alerts.append(f"今日有 {len(incomplete)} 个未完成的抓取任务")

    # 2. 失败率过高（24h 内失败率 > 20%）
    last_24h = now - timedelta(hours=24)
    tasks_24h = await db.list_recent_scrape_tasks(since=last_24h)
    total = len(tasks_24h)
    failed = len([t for t in tasks_24h if t["status"] == "failed"])
    if total > 5 and failed / total > 0.2:
        alerts.append(f"24h 任务失败率过高: {failed}/{total} ({failed/total:.0%})")

    # 3. 长时间无新房源（48h）
    latest_candidate = await db.fetchval(
        "SELECT MAX(created_at) FROM property_candidates"
    )
    if latest_candidate:
        # asyncpg TIMESTAMPTZ 返回的是 datetime 对象
        if isinstance(latest_candidate, str):
            latest_candidate = datetime.fromisoformat(latest_candidate.replace("Z", "+00:00"))
        if now - latest_candidate > timedelta(hours=48):
            alerts.append(f"超过 48 小时无新增房源候选，最近一条: {latest_candidate}")

    return alerts


@app.get("/health/scraper")
async def scraper_health():
    db = _get_db()
    last_success = await db.fetchval(
        "SELECT MAX(completed_at) FROM scrape_tasks WHERE status = 'completed'"
    )
    now = datetime.now(timezone.utc)
    if isinstance(last_success, str):
        last_success = datetime.fromisoformat(last_success.replace("Z", "+00:00"))

    alerts = await alert_if_needed(db)
    healthy = last_success is not None and (now - last_success) < timedelta(hours=25)

    return {
        "status": "healthy" if healthy else "unhealthy",
        "last_success": last_success.isoformat() if last_success else None,
        "alerts": alerts,
    }


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
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=True)
