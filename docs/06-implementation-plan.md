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

### Step 10: 管理界面 (`src/web/`)

> 详细设计见 [07-admin-ui-design.md](./07-admin-ui-design.md)

- [ ] 10.1 拆分 `process_city()` 为 `scrape_city()` + `publish_one()`，移除自动发布逻辑
- [ ] 10.2 新增 DB 查询方法：`list_activities`、`get_activity`、`count_by_city_and_status`、`get_activities_by_ids`
- [ ] 10.3 新增 `scrape_tasks` 表 + CRUD
- [ ] 10.4 FastAPI 应用 + Jinja2 模板（base/dashboard/activities/detail/scrape）
- [ ] 10.5 HTMX 交互：筛选、批量选择、批量发布、任务状态轮询
- [ ] 10.6 抓取后台执行（`asyncio.create_task`）+ 进度更新
- [ ] 10.7 新增依赖：fastapi、uvicorn、jinja2、python-multipart
- [ ] 10.8 访问控制：localhost 绑定 + 可选 Basic Auth（`ADMIN_PASSWORD` 环境变量）

### Step 11: 测试 & 验证
- [ ] 单元测试（各模块）
- [ ] 集成测试（端到端流程）
- [ ] 手动验证（先发布 1-2 个城市）

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
│   └── db.py                # Step 4
├── scrapers/
│   ├── __init__.py
│   ├── base.py              # Step 5 - 爬虫基类 + RawPage
│   ├── todocanada.py        # Step 5
│   ├── familyfun.py         # Step 5
│   └── saskatoon.py         # Step 5
├── ai/
│   ├── __init__.py
│   ├── engine.py            # Step 6 - 统一 Process Prompt
│   └── sanitizer.py         # Step 6 - HTML 安全清理
├── publisher/
│   ├── __init__.py
│   └── woohelps.py          # Step 7
├── dedup/
│   ├── __init__.py
│   └── deduplicator.py      # Step 8
├── web/                     # Step 10 - 管理界面
│   ├── __init__.py
│   ├── app.py               # FastAPI 应用 + 路由
│   └── templates/           # Jinja2 模板
└── main.py                  # Step 9
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

| 步骤 | 预估时间 |
|------|---------|
| ~~Step 0: Spike 验证~~ | ✅ 已完成 |
| ~~Step 1-2: 项目初始化 + 配置~~ | ✅ 已完成 |
| ~~Step 3-4: 模型 + 存储~~ | ✅ 已完成 |
| ~~Step 5: 爬虫~~ | ✅ 已完成 |
| ~~Step 6: AI 处理引擎 + HTML 清理~~ | ✅ 已完成 |
| ~~Step 7-8: 发布 + 去重~~ | ✅ 已完成 |
| ~~Step 9: 主流程编排~~ | ✅ 已完成 |
| Step 10: 管理界面 | 1.5-2 天 |
| Step 11: 测试验证 | 1 天 |
| **合计（剩余）** | **约 2.5-3 天** |

> 相比原方案（7-8 天），统一 LLM 提取方案省去了编写和验证 CSS 选择器的时间，爬虫代码量也大幅减少。
