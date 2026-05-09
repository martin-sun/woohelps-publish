# 管理界面设计

## 目标

为一期系统增加 Web 管理界面，将「自动发布」改为「人工审核 + 选择性发布」，用户可以：

1. 手动触发抓取（按城市或全部）
2. 按城市浏览已抓取的活动列表
3. 预览活动详情
4. 选择性发布活动到海外新生活平台

## 架构变更

### 一期流程（已废弃）

```
抓取所有详情页 → AI处理 → 去重 → 存储（status='pending'） → 人工审核发布
```

问题：抓详情页前无法判断值不值得抓，浪费大量时间和 API tokens。

### 二期新流程（两阶段）

```
阶段1（发现）：只抓列表页摘要 → AI 批量过滤 → 存入 candidate_activities
阶段2（筛选）：管理界面浏览候选活动 → 人工勾选 → 只抓选中的详情页
阶段3（处理）：AI 处理详情页 → 存储到 activities（status='pending'）
阶段4（发布）：管理界面浏览 → 人工选择 → 调用发布 API（status → 'published'）
```

核心变更：
- 新增 `discover_city()`：只抓列表页摘要，AI 过滤，存入 `candidate_activities`
- 新增 `fetch_selected_details()`：读取人工选中的候选，抓详情 + AI 处理 + 存储
- Scraper 拆分 `discover_pages()`（只抓列表）和 `fetch_pages()`（完整流程）
- 人工审核节点从"发布前"前移到"抓详情前"
- 保留 `scrape_city()` 和 `publish_one()` 供 CLI/定时任务全量抓取使用

```python
# src/main.py 核心函数

async def discover_city(
    city_slug: str, start_date: datetime, end_date: datetime,
    db: Database, ai_engine: AIEngine,
):
    """只抓列表页摘要，AI 过滤，存入 candidate_activities"""
    summaries = await scraper.discover_pages(city_slug, start_date, end_date)
    filtered = await ai_engine.filter_activities(city_slug, summaries)
    await db.save_candidates(filtered)


async def fetch_selected_details(
    city_slug: str, start_date: datetime, end_date: datetime,
    db: Database, ai_engine: AIEngine,
):
    """从 candidate_activities 读取人工选中的活动，抓详情 + AI 处理 + 存储"""
    candidates = await db.get_candidates_to_fetch(city_slug)
    for cand in candidates:
        raw_page = await _fetch_single_page(cand["source"], cand["source_url"], city_slug)
        activities = await ai_engine.process(raw_page)
        # ... 去重、存储、关联 candidate ...


async def scrape_city(
    city_slug: str, start_date: datetime, end_date: datetime,
    db: Database, ai_engine: AIEngine,
):
    """完整抓取流程（CLI/定时任务用）：发现 + AI过滤 + 抓详情 + 处理 + 存储"""
    raw_pages = await fetch_all_pages(city_slug, start_date, end_date, ai_engine)
    # ... 处理每个详情页 ...


async def publish_one(
    activity_id: int, db: Database, publisher: WoohelpsPublisher,
) -> dict:
    """发布单个活动到海外新生活"""
    # ... 发布逻辑不变 ...
```

## 技术选型

| 组件 | 选择 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | 异步，与现有 async 代码一致，自带 Swagger 文档 |
| 模板引擎 | Jinja2 | 服务端渲染，FastAPI 原生支持 |
| 前端交互 | HTMX (CDN) | 无需 SPA 框架即可实现动态交互（选择、筛选、批量操作、轮询） |
| 样式 | Tailwind CSS (CDN) | 无需构建步骤，CDN 引入即可 |
| 后台任务 | asyncio.create_task | 进程内异步执行，用 SQLite 表跟踪状态 |
| 新增依赖 | fastapi, uvicorn, jinja2, python-multipart | pyproject.toml 新增 |

选择理由：
- **不用 Celery/arq**：项目是单用户管理工具，不需要 Redis 和额外的 worker 进程
- **用 HTMX 不用 React/Vue**：管理界面交互简单（选择、筛选、发布），HTMX + 服务端渲染足够
- **用 Tailwind CDN**：避免引入 Node.js 构建链

## 页面设计

### 1. Dashboard（`GET /`）

```
┌──────────────────────────────────────────────────────────────┐
│  加拿大活动管理系统              [发现新活动] [抓取新活动]      │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  候选活动概览                                                  │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Toronto │  │Vancouver │  │ Montreal │  │ Calgary  │      │
│  │ 23 待处理│  │ 15 待处理 │  │ 8 待处理 │  │ 5 待处理 │      │
│  │ 12 已选中│  │ 8 已选中  │  │ 3 已选中 │  │ 2 已选中 │      │
│  └─────────┘  └──────────┘  └──────────┘  └──────────┘      │
│                                                              │
│  已发布活动统计                                                │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Toronto │  │Vancouver │  │ Montreal │  │ Calgary  │      │
│  │ 12 待发布│  │ 8 待发布  │  │ 5 待发布 │  │ 3 待发布 │      │
│  │ 45 已发布│  │ 30 已发布 │  │ 20 已发布│  │ 15 已发布│      │
│  └─────────┘  └──────────┘  └──────────┘  └──────────┘      │
│                                                              │
│  最近任务                                                      │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Toronto    │ todocanada   │ ✅ 完成  │ 12 new │ 10min前│  │
│  │ Vancouver  │ familyfun    │ 🔄 运行中│ -      │ 进行中 │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

功能：
- **候选活动概览**：各城市待处理/已选中/总数，点击跳转到 `/candidates`
- **已发布活动统计**：各城市待发布/已发布/总数，点击跳转到 `/activities`
- **快捷按钮**：「发现新活动」→ `/discover`，「抓取新活动」→ `/scrape`
- 最近任务状态列表（发现和抓取共用 scrape_tasks 表）

### 2. 候选活动（`GET /candidates`）

```
┌──────────────────────────────────────────────────────────────┐
│  候选活动（列表页抓取结果）                                      │
│  城市: [全部 ▾]  AI判断: [全部 ▾]  人工状态: [待处理 ▾]  [筛选]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  [✓] │ Blithe Spirit          │ Apr 29-May 10 │ $36-$64   │  │
│      │ AI: 文化演出，值得抓      │ 待处理         │ [选中][拒绝]│
│  [ ] │ Weekly Yoga Class      │ 每周三        │ Free      │  │
│      │ AI: 重复课程，不值得      │ 已拒绝         │ [选中][拒绝]│
│  [✓] │ The Pianomen           │ Jul 20        │ $25       │  │
│      │ AI: 文化演出，值得抓      │ 已选中         │ [选中][拒绝]│
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  ☑ 全选   [批量选中] [批量拒绝] [抓取选中详情]                  │
│  共 42 个候选 / 已选 3 个 / 待处理 23 个                        │
└──────────────────────────────────────────────────────────────┘
```

功能：
- **筛选**：城市（下拉）、AI 判断（值得/不值得/全部）、人工状态（pending/selected/rejected/全部）
- **表格列**：复选框 | 标题 | 日期 | 价格 | AI 判断（是/否 + 原因） | 人工状态 | 操作
- **操作按钮**：每行「选中」「拒绝」「查看原始页」
- **批量操作**：底栏「批量选中」「批量拒绝」「抓取选中详情」
- **HTMX 交互**：筛选时局部刷新表格，不重载整个页面

API 端点：
- `GET /candidates` → 渲染列表页
- `GET /candidates/table` → HTMX 局部刷新表格（筛选时）
- `POST /candidates/select` → 批量标记 selected（candidate_ids[]）
- `POST /candidates/reject` → 批量标记 rejected
- `POST /candidates/fetch-details` → 对 human_status='selected' 且 fetched_detail=0 的候选抓详情

### 3. 发现新活动（`GET /discover`）

```
┌──────────────────────────────────────────────────────────────┐
│  发现新活动（只抓列表页）                                       │
│                                                              │
│  选择城市:  ☑ Toronto  ☑ Vancouver  ☑ Montreal  ☐ ...       │
│            [全选] [取消全选]                                    │
│                                                              │
│  日期范围:  [2025-07-10] ~ [2025-08-10]                       │
│                                                              │
│  [开始发现]                                                   │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  发现任务历史                                                  │
│  ┌───────┬──────────┬─────────┬──────────┬──────┐           │
│  │ ID    │ 城市     │ 状态    │ 当前城市 │ 时间 │           │
│  │ 5     │ Toronto  │ 🔄 运行 │ Toronto  │ 30s  │           │
│  │ 4     │ Vancouver│ ✅ 完成 │ -        │ 5min │           │
│  └───────┴──────────┴─────────┴──────────┴──────┘           │
└──────────────────────────────────────────────────────────────┘
```

功能：
- 城市多选（复选框，含全选/取消全选）
- 日期范围选择（默认：今天 ~ 30天后）
- 「开始发现」按钮 → 后台异步执行 `discover_city()`，只抓列表页摘要
- 任务历史列表，HTMX 每 5 秒自动轮询更新运行中的任务状态
- 任务完成后可点击跳转到 `/candidates` 查看候选活动

API 端点：
- `GET /discover` → 渲染发现页
- `POST /discover/start` → 启动发现任务（表单编码：`city_slugs=toronto&city_slugs=vancouver&start_date=...`）
- `GET /scrape/task/{id}/status` → 任务状态片段（HTMX 轮询，发现和抓取共用）

### 4. 活动列表（`GET /activities`）

```
┌──────────────────────────────────────────────────────────────┐
│  活动列表                                                     │
│  城市: [全部 ▾]  状态: [待发布 ▾]  来源: [全部 ▾]  [筛选]      │
├──────────────────────────────────────────────────────────────┤
│  [✓] │ 📷 │ Toronto Summer Festival  │ Toronto │ 发布   │    │
│     │    │ 多伦多夏日音乐节            │ todoCan │ 7/15   │    │
│  [✓] │ 📷 │ Vancouver Art Walk       │Vancouver│ 待发布 │    │
│     │    │ 温哥华艺术漫步              │ family  │ 7/20   │    │
│  [ ] │ 📷 │ ...                       │ ...     │ ...    │    │
├──────────────────────────────────────────────────────────────┤
│  ☑ 全选   [发布选中 2 个活动]           第 1 页 / 共 5 页 >   │
└──────────────────────────────────────────────────────────────┘
```

功能：
- **筛选**：城市（下拉，含全部选项）、状态（pending/published/failed/skipped）、来源平台
- **表格列**：复选框 | 封面图 | 标题(中/英) | 城市 | 来源 | 活动时间 | 状态
- **批量操作**：底栏显示选中数量 + 「发布选中 N 个活动」按钮
- **分页**：每页 50 条
- **HTMX 交互**：选择/筛选/翻页均局部刷新，不重载整个页面

API 端点：
- `GET /activities` → 渲染列表页
- `GET /activities/table` → HTMX 局部刷新表格（筛选/翻页时）
- `POST /activities/publish` → 批量发布选中活动（表单编码：`activity_ids=1&activity_ids=2&activity_ids=3`）

### 5. 活动详情（`GET /activity/{id}`）

```
┌──────────────────────────────────────────────────────────────┐
│  ← 返回列表                                  [发布到海外新生活]│
├──────────────────────────────────────────────────────────────┤
│  ┌──────────┐  Toronto Summer Festival                         │
│  │          │  多伦多夏日音乐节                                 │
│  │  封面图   │                                                │
│  │          │  📍 123 Main St, Toronto, ON                    │
│  └──────────┘  📅 2025-07-15 10:00 ~ 18:00 (EST)             │
│                💰 Free                                        │
│  ┌────────┐┌────────┐┌────────┐   来源: TodoCanada            │
│  │ 图 2   ││ 图 3   ││ 图 4   │   [查看原始页面 ↗]            │
│  └────────┘└────────┘└────────┘                              │
│                                                              │
│  活动简介：                                                   │
│  多伦多夏日音乐节是加拿大最大的户外音乐活动之一...                  │
│                                                              │
│  ──────────────────────────────────────────────────────────  │
│  详细内容（HTML 预览）：                                       │
│  [渲染后的 HTML 内容]                                          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

功能：
- 封面图 + 图片列表缩略图
- 完整活动信息：标题（中/英）、描述、时间（本地+UTC）、地址、价格
- 来源信息 + 原始链接
- HTML 内容预览（iframe 或渲染后的 HTML）
- 操作按钮：「发布到海外新生活」「返回列表」
- 发布后显示平台活动 ID 和发布时间

### 6. 抓取页面（`GET /scrape`）

```
┌──────────────────────────────────────────────────────────────┐
│  触发抓取                                                     │
│                                                              │
│  选择城市:  ☑ Toronto  ☑ Vancouver  ☑ Montreal  ☐ ...       │
│            [全选] [取消全选]                                    │
│                                                              │
│  日期范围:  [2025-07-10] ~ [2025-08-10]                       │
│                                                              │
│  [开始抓取]                                                   │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  抓取任务历史                                                  │
│  ┌───────┬──────────┬─────────┬─────────┬──────────┬──────┐  │
│  │ ID    │ 城市     │ 状态    │ 新增    │ 跳过     │ 时间 │  │
│  │ 5     │ Toronto  │ 🔄 运行 │ -       │ -        │ 30s  │  │
│  │ 4     │ Vancouver│ ✅ 完成 │ 8       │ 15       │ 5min │  │
│  │ 3     │ Toronto  │ ✅ 完成 │ 12      │ 20       │ 8min │  │
│  │ 2     │ Montreal │ ❌ 失败 │ 0       │ 0        │ 2min │  │
│  └───────┴──────────┴─────────┴─────────┴──────────┴──────┘  │
└──────────────────────────────────────────────────────────────┘
```

功能：
- 城市多选（复选框，含全选/取消全选）
- 日期范围选择（默认：今天 ~ 30天后）
- 「开始抓取」按钮 → 后台异步执行
- 抓取任务历史列表，HTMX 每 5 秒自动轮询更新运行中的任务状态
- 任务完成后可点击跳转到活动列表查看结果

API 端点：
- `POST /scrape/start` → 启动抓取任务（表单编码：`city_slugs=toronto&city_slugs=vancouver&start_date=2025-07-10&end_date=2025-08-10`）
- `GET /scrape/tasks` → 任务历史列表
- `GET /scrape/task/{id}/status` → 单个任务状态（HTMX 轮询）

## 后台任务设计

### scrape_tasks 表

```sql
CREATE TABLE IF NOT EXISTS scrape_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_slugs TEXT NOT NULL,          -- JSON array, 如 ["toronto", "vancouver"]
    status TEXT NOT NULL DEFAULT 'running',  -- running/completed/failed
    total_fetched INTEGER DEFAULT 0,
    total_new INTEGER DEFAULT 0,
    total_skipped INTEGER DEFAULT 0,
    current_city TEXT,                 -- 正在处理的城市
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
```

### 执行流程

发现和抓取共用 `scrape_tasks` 表记录任务状态，但使用独立的 guard lock：

```python
# src/web/app.py

import asyncio

# 发现任务锁
_discover_guard = asyncio.Lock()
_discover_running = False

# 详情抓取锁
_fetch_guard = asyncio.Lock()
_fetch_running = False

# 全量抓取锁
_scrape_guard = asyncio.Lock()
_scrape_running = False


@app.post("/discover/start")
async def start_discover(request: Request):
    global _discover_running
    form = await request.form()
    city_slugs = form.getlist("city_slugs")
    ...
    async with _discover_guard:
        if _discover_running:
            return HTMLResponse("已有发现任务正在运行", status_code=409)
        _discover_running = True
    try:
        task_id = await db.create_scrape_task(city_slugs)
    except Exception:
        _discover_running = False
        raise

    async def _run():
        global _discover_running
        try:
            for city in city_slugs:
                await db.update_scrape_task(task_id, current_city=city)
                await discover_city(city, start_date, end_date, db, ai_engine)
            await db.complete_scrape_task(task_id)
        except Exception as e:
            await db.fail_scrape_task(task_id, str(e))
        finally:
            _discover_running = False

    asyncio.create_task(_run())
    return RedirectResponse(url=f"/discover?task={task_id}", status_code=303)


@app.post("/candidates/fetch-details")
async def candidates_fetch_details(request: Request):
    global _fetch_running
    form = await request.form()
    city = form.get("city", "")
    ...
    async with _fetch_guard:
        if _fetch_running:
            return HTMLResponse("已有详情抓取任务正在运行", status_code=409)
        _fetch_running = True

    async def _run():
        global _fetch_running
        try:
            await fetch_selected_details(city, start_date, end_date, db, ai_engine)
        except Exception as e:
            logger.error(f"Fetch details failed: {e}")
        finally:
            _fetch_running = False

    asyncio.create_task(_run())
    return RedirectResponse(url=f"/candidates?city={city}", status_code=303)


@app.post("/scrape/start")
async def start_scrape(request: Request):
    global _scrape_running
    ...
    async with _scrape_guard:
        if _scrape_running:
            return HTMLResponse("已有抓取任务正在运行", status_code=409)
        _scrape_running = True
    ...
    async def _run():
        global _scrape_running
        try:
            for city in city_slugs:
                await scrape_city(city, start_date, end_date, db, ai_engine)
            await db.complete_scrape_task(task_id)
        except Exception as e:
            await db.fail_scrape_task(task_id, str(e))
        finally:
            _scrape_running = False
    ...
```

### 进度更新

`scrape_city()` 内部在处理完每个城市后更新 scrape_tasks 的 `current_city`、`total_fetched`、`total_new` 等字段，供前端轮询显示进度。

### 限制

- 使用 `_scrape_guard` (asyncio.Lock) 保护「检查 `_scrape_running` + 标记为 True」的原子性，防止并发请求竞态
- `_scrape_running` 在 `finally` 块中重置，确保异常时也能恢复
- 提交请求同步标记 `_scrape_running=True`，后台 task 异步执行抓取
- 进程重启后运行中的任务状态丢失，启动时扫描 scrape_tasks 表将 `status='running'` 的记录标记为 `failed`，并重置 `_scrape_running = False`
- 抓取本身是幂等的（有去重机制），重新执行是安全的

## 需要新增的 DB 查询方法

在 `src/storage/db.py` 中新增：

```python
# --- Candidate Activities ---

async def save_candidates(candidates: list[dict]) -> int:
    """批量保存候选活动（INSERT OR UPDATE），返回操作数量"""

async def list_candidates(
    city_slug: str | None = None,
    ai_worth: bool | None = None,
    human_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """分页查询候选活动，支持按城市/AI判断/人工状态筛选"""

async def count_candidates(
    city_slug: str | None = None,
    ai_worth: bool | None = None,
    human_status: str | None = None,
) -> int:
    """统计候选活动数量"""

async def update_candidate_status(ids: list[int], status: str) -> None:
    """批量更新候选活动的人工状态（pending/selected/rejected）"""

async def mark_candidate_fetched(candidate_id: int, activity_id: int) -> None:
    """标记候选活动已抓详情，并关联 activities.id"""

async def get_candidates_to_fetch(city_slug: str | None = None) -> list[dict]:
    """获取 human_status='selected' AND fetched_detail=0 的候选"""

async def get_candidate(candidate_id: int) -> dict | None:
    """获取单个候选活动详情"""

async def count_candidates_by_city() -> dict[str, dict]:
    """按城市和人工状态统计候选数量
    返回: {"toronto": {"total": 50, "pending": 30, "selected": 20}, ...}
    """

# --- Activity 查询（管理界面用）---

async def list_activities(
    city_slug: str | None = None,
    status: str | None = None,      # pending/published/failed/skipped
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """分页查询活动列表，支持按城市/状态/来源筛选"""

async def get_activity(activity_id: int) -> dict | None:
    """获取单个活动详情"""

async def count_by_city_and_status() -> dict[str, dict]:
    """按城市和 status 统计数量
    返回: {"toronto": {"total": 50, "published": 30, "pending": 20}, ...}
    """

async def get_activities_by_ids(ids: list[int]) -> list[dict]:
    """按 ID 列表批量获取活动（批量发布用）"""

# --- Scrape Tasks ---

async def create_scrape_task(city_slugs: list[str]) -> int:
    """创建抓取/发现任务记录，返回 task_id"""

async def get_scrape_task(task_id: int) -> dict | None:
    """获取任务状态"""

async def update_scrape_task(task_id: int, **kwargs) -> None:
    """更新任务进度"""

async def complete_scrape_task(task_id: int) -> None:
    """标记任务完成"""

async def fail_scrape_task(task_id: int, error: str) -> None:
    """标记任务失败"""

async def list_recent_scrape_tasks(limit: int = 20) -> list[dict]:
    """获取最近的任务列表"""
```

已有的方法（无需修改）：
- `mark_published(source, source_id, platform_id)` → 标记发布成功
- `mark_publish_failed(source, source_id, error)` → 标记发布失败

## API 端点汇总

| 方法 | 路径 | 功能 | 返回 |
|------|------|------|------|
| GET | `/` | Dashboard | HTML |
| GET | `/candidates` | 候选活动列表 | HTML |
| GET | `/candidates/table` | 候选表格片段 | HTML (HTMX) |
| POST | `/candidates/select` | 批量选中候选 | Redirect |
| POST | `/candidates/reject` | 批量拒绝候选 | Redirect |
| POST | `/candidates/fetch-details` | 抓取选中详情 | Redirect |
| GET | `/discover` | 发现新活动页面 | HTML |
| POST | `/discover/start` | 启动发现任务 | Redirect |
| GET | `/activities` | 活动列表 | HTML |
| GET | `/activities/table` | 活动表格片段 | HTML (HTMX) |
| POST | `/activities/publish` | 批量发布 | Redirect |
| GET | `/activity/{id}` | 活动详情 | HTML |
| POST | `/activity/{id}/publish` | 发布单个活动 | Redirect |
| GET | `/scrape` | 抓取页面 | HTML |
| POST | `/scrape/start` | 启动抓取 | Redirect |
| GET | `/scrape/task/{id}/status` | 任务状态片段 | HTML (HTMX) |

## 访问控制

管理界面涉及抓取触发和发布操作，属于后台管理功能，需要访问限制。

### 方案：绑定 localhost + 可选 Basic Auth

```python
# src/web/app.py

import os
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# 绑定 localhost，不暴露到公网
# uvicorn 启动时指定 host="127.0.0.1"

# 可选 Basic Auth（通过环境变量 ADMIN_PASSWORD 控制）
security = HTTPBasic(auto_error=False)

async def verify_auth(credentials: HTTPBasicCredentials | None = Security(security)):
    """如果设置了 ADMIN_PASSWORD 环境变量，则要求 Basic Auth"""
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_password:
        return  # 无密码配置，跳过鉴权
    if not credentials or credentials.password != admin_password:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})

# 所有路由统一应用鉴权，避免遗漏 POST 端点
app = FastAPI(dependencies=[Depends(verify_auth)])
```

配置：
- `ADMIN_PASSWORD` 环境变量：设置后启用 Basic Auth，不设置则不鉴权（仅限本地使用）
- `FastAPI(dependencies=[Depends(verify_auth)])`：全局依赖，所有路由（包括 POST）自动鉴权
- uvicorn 启动 `host="127.0.0.1"`：默认只监听本地回环，外部无法访问
- 生产环境建议通过反向代理（Nginx）添加 HTTPS 和访问控制

## 新增数据表

### candidate_activities

存储列表页抓到的活动摘要，供人工筛选。

```sql
CREATE TABLE IF NOT EXISTS candidate_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_slug TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    event_date TEXT,
    address TEXT DEFAULT '',
    price TEXT DEFAULT '',
    description TEXT DEFAULT '',
    ai_worth_fetching INTEGER,         -- 1=值得, 0=不值得, NULL=未判断
    ai_reason TEXT,
    human_status TEXT NOT NULL DEFAULT 'pending',
    fetched_detail INTEGER NOT NULL DEFAULT 0,
    activity_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_url)
);
```

## 文件结构

```
src/
├── web/                          # Web 管理界面
│   ├── __init__.py
│   ├── app.py                    # FastAPI 应用 + 所有路由
│   └── templates/
│       ├── base.html             # 布局（Tailwind + HTMX CDN）
│       ├── dashboard.html        # 仪表盘（含候选活动概览）
│       ├── candidates.html       # 候选活动列表（人工筛选）
│       ├── discover.html         # 发现新活动（只抓列表页）
│       ├── activities.html       # 活动列表（已抓详情的活动）
│       ├── activity_detail.html  # 活动详情
│       ├── scrape.html           # 抓取触发（全量抓取，供 CLI 用）
│       └── partials/             # HTMX 局部片段
│           ├── activity_table.html   # 活动表格
│           ├── candidate_table.html  # 候选活动表格
│           └── task_row.html         # 任务行
├── config/                       # 配置
├── models/                       # 数据模型
├── storage/                      # 存储（新增 candidate_activities 表）
├── scrapers/                     # 爬虫（拆分 discover_pages + fetch_pages）
├── ai/                           # AI 处理（新增 filter_activities）
├── publisher/                    # 发布模块
├── dedup/                        # 去重模块
└── main.py                       # 主流程（discover_city + fetch_selected_details + scrape_city + publish_one）
```

入口方式：

```bash
# 启动管理界面
python -m src.web.app
# → http://localhost:8000

# Web 流程：
# 1. 访问 /discover → 选择城市 → 开始发现（只抓列表页摘要）
# 2. 访问 /candidates → 筛选/勾选活动 → 选中/拒绝 → 抓取选中详情
# 3. 访问 /activities → 审核已抓详情活动 → 发布到平台

# CLI 全量抓取（定时任务用，不走人工筛选）
python -m src.main --schedule
python -m src.main --city toronto
```

## 新增依赖

```toml
# pyproject.toml
[project]
dependencies = [
    # ... 现有依赖 ...
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.6",   # 表单数据解析（HTMX 表单提交）
]
```

新增环境变量：
```
ADMIN_PASSWORD=           # 可选，设置后管理界面启用 Basic Auth
```

## base.html 模板结构

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}加拿大活动管理{% endblock %}</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- HTMX CDN -->
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
</head>
<body class="bg-gray-50 min-h-screen">
    <!-- 导航栏 -->
    <nav class="bg-white border-b">
        <div class="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
            <a href="/" class="font-bold text-lg">加拿大活动管理</a>
            <div class="flex gap-4">
                <a href="/">Dashboard</a>
                <a href="/activities">活动列表</a>
                <a href="/scrape">抓取</a>
            </div>
        </div>
    </nav>

    <!-- 主内容 -->
    <main class="max-w-7xl mx-auto px-4 py-6">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```
