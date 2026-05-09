# 一期实施计划

## 目标

完成 TodoCanada/FamilyFunCanada/DiscoverSaskatoon 活动抓取 → AI 处理（提取+翻译+摘要）→ 管理界面人工审核 → 通过 API 选择性发布到海外新生活平台。

## 核心架构

所有数据源使用统一逻辑：**Playwright 抓取原始 HTML → LLM 一步完成提取+翻译+摘要+质量评估**。爬虫只负责页面导航和收集详情页链接，不写 CSS 选择器提取数据。管理界面提供人工审核和选择性发布能力。

## 实施步骤

### Step 1: 项目初始化
- [x] 初始化 Python 项目（pyproject.toml）
- [x] 配置依赖管理（uv 或 pip）
- [x] 创建项目目录结构
- [x] 编写 `.env.example` 配置模板
- [x] 配置 pydantic-settings

**依赖包**:
```
httpx>=0.27
anthropic>=0.40
pydantic>=2.0
pydantic-settings>=2.0
apscheduler>=3.10
loguru>=0.7
aiosqlite>=0.20
playwright>=1.40
bleach>=6.0
```

### Step 2: 配置模块 (`src/config/settings.py`)
- [x] pydantic-settings 配置类
- [x] 环境变量加载
- [x] 城市坐标映射配置

**配置项**:
```
KIMI_API_KEY=
KIMI_BASE_URL=https://api.kimi.com/coding/
KIMI_MODEL=kimi-k2.6
WOOHELPS_API_URL=https://www.woohelps.com
WOOHELPS_LOGIN_SESSION=
DB_PATH=./data/activities.db
LOG_LEVEL=INFO
```

### Step 3: 数据模型 (`src/models/`)
- [x] RawPage 数据类（原始页面：source_url + raw_html + city）
- [x] ProcessedActivity 数据类（AI 处理后）
- [x] 城市配置模型

### Step 4: 存储模块 (`src/storage/`)
- [x] SQLite 数据库初始化（建表）
- [x] 活动 CRUD 操作
- [x] 去重查询
- [x] 抓取日志记录

### Step 5: 爬虫 (`src/scrapers/`)
- [x] Spike: 验证各数据源列表页 URL pattern、详情页链接规则、分页行为
- [x] 爬虫基类 + RawPage 数据结构（只负责导航和抓取原始 HTML）
- [x] TodoCanada 爬虫（列表页 → 收集详情页链接 → 抓取 HTML）
- [x] FamilyFunCanada 爬虫（城市主页 → 收集文章链接 → 抓取 HTML）
- [x] DiscoverSaskatoon 爬虫（列表页 + Load More → 收集详情页链接 → 抓取 HTML）
- [x] 爬虫注册与调度逻辑
- [x] 错误处理和重试

### Step 6: AI 处理引擎 (`src/ai/`)
- [x] Kimi API 客户端封装（Anthropic 兼容协议）
- [x] 统一 Process Prompt（提取+翻译+摘要+分类+质量评估一步完成）
- [x] HTML 安全清理（bleach）
- [x] 时区处理（LLM 输出的本地时间 → UTC）
- [x] 批量处理逻辑

### Step 7: 发布模块 (`src/publisher/woohelps.py`)
- [x] 海外新生活 API 客户端
- [x] 活动发布接口
- [x] 错误处理和重试

### Step 8: 去重模块 (`src/dedup/`)
- [x] 页面缓存表（processed_pages + html_hash）
- [x] 事件级 source_id 精确去重（hash-based）
- [x] content_hash 内容去重

### Step 9: 主流程 (`src/main.py`)
- [x] 编排整个流程（抓取 → LLM 处理 → HTML 清理 → 去重 → 存储 → **自动发布**）
- [x] APScheduler 定时任务（每天 06:00 UTC）
- [x] 命令行参数（手动触发/定时运行）
- [x] 日志配置

> **注意**：当前 Step 9 的 `process_city()` 包含自动发布逻辑（main.py:90-102）。Step 10.1 将拆分此函数并移除自动发布。

### Step 10: 管理界面一期 (`src/web/`)

> 详细设计见 [07-admin-ui-design.md](./07-admin-ui-design.md)

- [x] 10.1 拆分 `process_city()` 为 `scrape_city()` + `publish_one()`，移除自动发布逻辑
- [x] 10.2 新增 DB 查询方法：`list_activities`、`get_activity`、`count_by_city_and_status`、`get_activities_by_ids`
- [x] 10.3 新增 `scrape_tasks` 表 + CRUD
- [x] 10.4 FastAPI 应用 + Jinja2 模板（base/dashboard/activities/detail/scrape）
- [x] 10.5 HTMX 交互：筛选、批量选择、批量发布、任务状态轮询
- [x] 10.6 抓取后台执行（`asyncio.create_task`）+ 进度更新
- [x] 10.7 新增依赖：fastapi、uvicorn、jinja2、python-multipart
- [x] 10.8 访问控制：localhost 绑定 + 可选 Basic Auth（`ADMIN_PASSWORD` 环境变量）

### Step 11: 二期 — 列表页 AI 预过滤 + 人工筛选

> 目标：将人工审核节点从"发布前"前移到"抓详情前"，减少不必要的详情页抓取

#### 11.1 数据库：新增 `candidate_activities` 表
- [x] 建表 SQL（含索引）
- [x] `save_candidates()` — 批量保存/更新
- [x] `list_candidates()` / `count_candidates()` — 分页查询+筛选
- [x] `update_candidate_status()` — 批量更新人工状态
- [x] `mark_candidate_fetched()` — 标记已抓详情
- [x] `get_candidates_to_fetch()` — 获取待抓详情的选中候选
- [x] `count_candidates_by_city()` — Dashboard 统计用

#### 11.2 爬虫：拆分 `discover_pages()` 和 `fetch_pages()`
- [x] `TodoCanadaScraper.discover_pages()` — 只翻列表页，提取摘要
- [x] `TodoCanadaScraper.fetch_pages()` — 调用 discover + AI过滤 + 抓详情
- [x] `FamilyFunCanadaScraper.discover_pages()` — 只收集文章 URL
- [x] `DiscoverSaskatoonScraper.discover_pages()` — 只收集详情页 URL
- [x] `BaseScraper.fetch_pages()` 签名更新，增加 `ai_engine` 参数

#### 11.3 AI 引擎：新增 `filter_activities()`
- [x] 批量过滤 prompt（根据标题/日期/地址/价格/描述判断值不值得抓）
- [x] JSON 输出解析（`worth_fetching` + `reason`）
- [x] 失败时 fallback 到全抓

#### 11.4 主流程：新增 `discover_city()` 和 `fetch_selected_details()`
- [x] `discover_city()` — 只抓列表页摘要 → AI过滤 → 存入 candidate_activities
- [x] `fetch_selected_details()` — 读取人工选中的候选 → 抓详情 → AI处理 → 存储
- [x] `_fetch_single_page()` — 辅助函数：抓取单个详情页
- [x] 保留 `scrape_city()` 供 CLI/定时任务全量抓取

#### 11.5 Web 界面：新增 `/discover` 和 `/candidates`
- [x] `/discover` 页面 — 触发发现任务（只抓列表页）
- [x] `/discover/start` POST — 后台执行 `discover_city()`
- [x] `/candidates` 页面 — 展示候选活动，支持筛选/勾选/批量操作
- [x] `/candidates/table` — HTMX 局部刷新
- [x] `/candidates/select` — 批量标记 selected
- [x] `/candidates/reject` — 批量标记 rejected
- [x] `/candidates/fetch-details` — 后台执行 `fetch_selected_details()`
- [x] Dashboard 增加候选活动概览卡片

#### 11.6 新增模板
- [x] `discover.html` — 发现任务触发页
- [x] `candidates.html` — 候选活动列表页
- [x] `partials/candidate_table.html` — HTMX 刷新表格

### Step 12: 测试 & 验证
- [ ] 单元测试（各模块）
- [ ] 集成测试（端到端流程）
- [ ] 手动验证（先测试 Saskatoon 发现流程）

## 文件清单

```
src/
├── config/
│   ├── __init__.py
│   └── settings.py          # Step 2
├── models/
│   ├── __init__.py
│   └── activity.py          # Step 3 - RawPage + ProcessedActivity
├── storage/
│   ├── __init__.py
│   └── db.py                # Step 4 + 11.1 - 新增 candidate_activities 表
├── scrapers/
│   ├── __init__.py
│   ├── base.py              # Step 5 + 11.2 - fetch_pages 签名增加 ai_engine
│   ├── todocanada.py        # Step 5 + 11.2 - 拆分 discover_pages + fetch_pages
│   ├── familyfun.py         # Step 5 + 11.2 - 拆分 discover_pages + fetch_pages
│   └── saskatoon.py         # Step 5 + 11.2 - 拆分 discover_pages + fetch_pages
├── ai/
│   ├── __init__.py
│   ├── engine.py            # Step 6 + 11.3 - 新增 filter_activities 批量过滤
│   └── sanitizer.py         # Step 6 - HTML 安全清理
├── publisher/
│   ├── __init__.py
│   └── woohelps.py          # Step 7
├── dedup/
│   ├── __init__.py
│   └── deduplicator.py      # Step 8
├── web/                     # Step 10 + 11.5 - 管理界面
│   ├── __init__.py
│   ├── app.py               # FastAPI 应用 + 路由（新增 /discover + /candidates）
│   └── templates/           # Jinja2 模板
│       ├── base.html
│       ├── dashboard.html   # 新增候选活动概览
│       ├── discover.html    # 11.6 新增
│       ├── candidates.html  # 11.6 新增
│       ├── activities.html
│       ├── activity_detail.html
│       ├── scrape.html
│       └── partials/
│           ├── activity_table.html
│           ├── candidate_table.html  # 11.6 新增
│           └── task_row.html
└── main.py                  # Step 9 + 11.4 - 新增 discover_city + fetch_selected_details
```

## 验证方法

1. **Spike 验证**: 对每个数据源验证列表页 URL pattern、详情页链接规则、分页行为（详见 [02-scraper-design.md](./02-scraper-design.md)）
2. **抓取验证**: 运行爬虫，确认能获取到详情页的原始 HTML
3. **LLM 处理验证**: 检查从原始 HTML 提取的数据质量（标题、时间、地点、翻译）
4. **HTML 清理验证**: 确认清理后的 HTML 不含 script/iframe，外链安全，来源标注正确
5. **时区验证**: 对比本地时间和发布的 UTC 时间，确认转换正确（尤其注意夏令时边界）
6. **发布验证**: 先在测试环境发布 1-2 个活动到平台，检查：
   - 活动是否出现在平台上
   - 中文内容是否正确
   - 时间、地点是否准确
   - 图片是否正常显示
7. **审核验证**: 确认平台审核流程正常工作

## 时间估算

| 步骤 | 预估时间 | 状态 |
|------|---------|------|
| ~~Step 0: Spike 验证~~ | ✅ | 已完成 |
| ~~Step 1-2: 项目初始化 + 配置~~ | ✅ | 已完成 |
| ~~Step 3-4: 模型 + 存储~~ | ✅ | 已完成 |
| ~~Step 5: 爬虫~~ | ✅ | 已完成 |
| ~~Step 6: AI 处理引擎 + HTML 清理~~ | ✅ | 已完成 |
| ~~Step 7-8: 发布 + 去重~~ | ✅ | 已完成 |
| ~~Step 9: 主流程编排~~ | ✅ | 已完成 |
| ~~Step 10: 管理界面~~ | ✅ | 已完成 |
| Step 11: 二期 — 列表页 AI 预过滤 | ✅ | 已实现，待测试 |
| Step 12: 测试验证 | 0.5 天 | 待执行 |
| **合计（剩余）** | **约 0.5 天** | |

> 二期核心代码已全部实现，待手动验证 Saskatoon 发现流程和候选活动筛选流程。
