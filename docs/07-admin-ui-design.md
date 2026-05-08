# 管理界面设计

## 目标

为一期系统增加 Web 管理界面，将「自动发布」改为「人工审核 + 选择性发布」，用户可以：

1. 手动触发抓取（按城市或全部）
2. 按城市浏览已抓取的活动列表
3. 预览活动详情
4. 选择性发布活动到海外新生活平台

## 架构变更

### 当前流程（main.py `process_city`）

```
抓取 → AI处理 → 去重 → 存储 → 自动发布
```

抓取和发布耦合在一起，`process_city()` 完成后活动直接发布到平台。

### 新流程

```
阶段1（抓取）：抓取 → AI处理 → 去重 → 存储（status='pending'）
阶段2（审核）：管理界面浏览 → 人工选择 → 调用发布 API（status → 'published'）
```

核心变更：
- `process_city()` 拆分为 `scrape_city()`（抓取+处理+存储，status='pending'）和 `publish_one()`（发布单个活动）
- 移除 `process_city()` 中的自动发布逻辑（当前 main.py:90-102）
- `run_once()` 和 `run_scheduled()` 只执行抓取，不再自动发布

```python
# src/main.py 变更示意

async def scrape_city(
    city_slug: str, start_date: datetime, end_date: datetime,
    db: Database, ai_engine: AIEngine,
):
    """抓取 + AI处理 + 去重 + 存储（不发布，status='pending'）"""
    raw_pages = await fetch_all_pages(city_slug, start_date, end_date)
    for page in raw_pages:
        # ... 与当前 process_city 相同的去重和存储逻辑 ...
        # 移除自动发布部分（当前 main.py:90-102）
        pass


async def publish_one(
    activity_id: int, db: Database, publisher: WoohelpsPublisher,
) -> dict:
    """发布单个活动到海外新生活"""
    activity = await db.get_activity(activity_id)
    if not activity or activity["status"] == "published":
        return {"error": "活动不存在或已发布"}
    city_id = publisher.get_city_id(CITIES[activity["city_slug"]]["eng_name"])
    result = await publisher.publish_activity(activity, city_id)
    # ... 标记发布结果 ...
    return result
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
│  加拿大活动管理系统                              [抓取新活动]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Toronto │  │Vancouver │  │ Montreal │  │ Calgary  │      │
│  │ 12 待发布│  │ 8 待发布  │  │ 5 待发布 │  │ 3 待发布 │      │
│  │ 45 已发布│  │ 30 已发布 │  │ 20 已发布│  │ 15 已发布│      │
│  └─────────┘  └──────────┘  └──────────┘  └──────────┘      │
│                                                              │
│  最近抓取任务                                                  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Toronto    │ todocanada   │ ✅ 完成  │ 12 new │ 10min前│  │
│  │ Vancouver  │ familyfun    │ 🔄 运行中│ -      │ 进行中 │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

功能：
- 各城市活动统计卡片（待发布数 / 已发布数 / 总数）
- 最近抓取任务状态列表
- 快捷按钮：「抓取全部城市」→ 跳转到 `/scrape`

### 2. 活动列表（`GET /activities`）

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

### 3. 活动详情（`GET /activity/{id}`）

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

### 4. 抓取页面（`GET /scrape`）

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

```python
# src/web/app.py 中

import asyncio

# 全局锁：同时只允许一个抓取任务运行
# 用 guard lock 保证「检查 + 标记」的原子性
_scrape_guard = asyncio.Lock()
_scrape_running = False

@app.post("/scrape/start")
async def start_scrape(request: Request):
    global _scrape_running

    # 先解析表单，在 guard 外完成可能失败的操作
    form = await request.form()
    city_slugs = form.getlist("city_slugs")
    start_date = ...  # 从表单解析

    # guard lock 保护检查+标记，防止并发请求竞态
    async with _scrape_guard:
        if _scrape_running:
            return HTMLResponse("已有抓取任务正在运行，请等待完成", status_code=409)
        _scrape_running = True

    # 创建 DB 任务记录（在标记后执行，失败时需要重置标记）
    try:
        task_id = await db.create_scrape_task(city_slugs)
    except Exception:
        _scrape_running = False
        raise

    async def _run():
        global _scrape_running
        try:
            for city in city_slugs:
                await db.update_scrape_task(task_id, current_city=city)
                await scrape_city(city, start_date, end_date, db, ai_engine)
            await db.complete_scrape_task(task_id)
        except Exception as e:
            await db.fail_scrape_task(task_id, str(e))
        finally:
            _scrape_running = False

    asyncio.create_task(_run())
    return RedirectResponse(url=f"/scrape?task={task_id}", status_code=303)


@app.get("/scrape/task/{task_id}/status")
async def task_status(task_id: int):
    """HTMX 轮询端点，返回任务状态片段"""
    task = await db.get_scrape_task(task_id)
    return HTMLResponse(render_template("partials/task_row.html", task=task))
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
    """创建抓取任务记录，返回 task_id"""

async def get_scrape_task(task_id: int) -> dict | None:
    """获取抓取任务状态"""

async def update_scrape_task(task_id: int, **kwargs) -> None:
    """更新抓取任务进度"""

async def complete_scrape_task(task_id: int) -> None:
    """标记抓取任务完成"""

async def fail_scrape_task(task_id: int, error: str) -> None:
    """标记抓取任务失败"""

async def list_recent_scrape_tasks(limit: int = 20) -> list[dict]:
    """获取最近的抓取任务列表"""
```

已有的方法（无需修改）：
- `mark_published(source, source_id, platform_id)` → 标记发布成功
- `mark_publish_failed(source, source_id, error)` → 标记发布失败

## API 端点汇总

| 方法 | 路径 | 功能 | 返回 |
|------|------|------|------|
| GET | `/` | Dashboard | HTML |
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

## 文件结构

```
src/
├── web/                          # 新增：Web 管理界面
│   ├── __init__.py
│   ├── app.py                    # FastAPI 应用 + 所有路由
│   └── templates/
│       ├── base.html             # 布局（Tailwind + HTMX CDN）
│       ├── dashboard.html        # 仪表盘
│       ├── activities.html       # 活动列表
│       ├── activity_detail.html  # 活动详情
│       ├── scrape.html           # 抓取触发 + 任务列表
│       └── partials/             # HTMX 局部片段
│           ├── activity_table.html   # 活动表格（筛选/翻页刷新）
│           ├── activity_row.html     # 单行活动
│           ├── city_card.html        # 城市统计卡片
│           └── task_row.html         # 抓取任务行（轮询刷新）
├── config/                       # 不变
├── models/                       # 不变
├── storage/                      # 新增查询方法
├── scrapers/                     # 不变
├── ai/                           # 不变
├── publisher/                    # 不变
├── dedup/                        # 不变
└── main.py                       # 修改：拆分 process_city，移除自动发布
```

入口方式：

```bash
# 启动管理界面
python -m src.web.app
# → http://localhost:8000

# 定时抓取（Step 10.1 完成后：仅抓取+存储，不自动发布）
python -m src.main --schedule

# 单次抓取指定城市（Step 10.1 完成后：仅抓取+存储，不自动发布）
python -m src.main --city toronto
```

> **注意**：当前 `python -m src.main` 仍会自动发布（main.py:90-102）。Step 10.1 完成后才改为仅抓取+存储。

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
