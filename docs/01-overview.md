# 加拿大活动自动发布系统 — 总体设计

## 项目背景

海外新生活（woohelps.com）是一个面向加拿大华人的社区平台，覆盖 10 个加拿大城市。当前平台上的活动内容主要由用户手动发布，数量有限。本项目的目标是自动从外部数据源抓取加拿大各城市的活动信息，经 AI 处理（翻译、摘要、去重）后，通过平台现有 API 发布到海外新生活平台，丰富平台活动内容。

## 系统目标

- **数据源**: 从 TodoCanada、DiscoverSaskatoon、FamilyFunCanada（一期）抓取活动，Facebook Events（二期）
- **AI 处理**: 两个阶段——`filter_activities()` 批量预过滤列表页摘要；`process()` 处理详情页提取结构化数据 + 翻译 + 摘要
- **人工审核模式**: 自动抓取 + AI 处理 → 管理界面人工审核 → 选择性发布到平台 → 平台内置审核流程过滤
- **覆盖城市**: 温哥华、多伦多、蒙特利尔、卡尔加里、埃德蒙顿、渥太华、温尼伯、萨斯卡通、里贾纳、蒙克顿

## 系统架构

```
┌─ 阶段1: Discover ─────────────────────────────────────────────┐
│                                                                │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │  列表页抓取   │ ──→ │  AI 预过滤    │ ──→ │  候选存储    │   │
│  │  discover()  │     │  filter()    │     │  candidates  │   │
│  └──────────────┘     └──────────────┘     └──────────────┘   │
│       │                    │                    │               │
│  只抓列表页摘要       Kimi API 批量判断     存入 candidate_activities
│  (title/date/addr)    worth_fetching?       (等待人工筛选)      │
│                                                                │
└────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  管理界面人工筛选   │
                    │  Web UI 勾选       │
                    └─────────┬─────────┘
                              │
┌─ 阶段2: Fetch Details ────────────────────────────────────────┐
│                                                                │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │  详情页抓取   │ ──→ │  AI 处理引擎  │ ──→ │  去重 & 存储  │   │
│  │  单页抓取     │     │  process()   │     │  activities  │   │
│  └──────────────┘     └──────────────┘     └──────────────┘   │
│       │                    │                    │               │
│  只抓选中的详情页     提取+翻译+摘要+评估   正式活动表           │
│                                                                │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌──────────────┐     ┌──────────────┐
                    │  管理界面     │ ──→ │  API 发布    │
                    │  审核+发布   │     │  (Publisher) │
                    └──────────────┘     └──────────────┘
```

核心设计：**两阶段抓取流程**——先低成本抓列表页摘要，经 AI 预过滤和人工筛选后，只对选中的活动抓取详情页。爬虫负责页面导航和抓取，LLM 负责提取结构化数据 + 翻译 + 摘要。管理界面提供人工审核能力，用户可在发布前预览、筛选和选择要发布的活动。

## 数据流

### 阶段1: Discover（列表页发现）

1. **列表页抓取**: 各爬虫的 `discover_pages()` 方法只抓取列表页，提取活动摘要（url/title/date/address/price/description）
2. **AI 预过滤**: `AIEngine.filter_activities()` 批量判断摘要是否值得抓取详情（文化活动/户外/节日 = YES，促销/招聘/日常课程 = NO）
3. **存入候选表**: 过滤后的摘要存入 `candidate_activities` 表，记录 AI 判断结果（ai_worth_fetching + ai_reason）
4. **人工筛选**: 管理界面展示候选活动，用户勾选想要的活动（human_status: pending → selected/rejected）

### 阶段2: Fetch Details（详情抓取 + 处理）

1. **抓取详情页**: `fetch_selected_details()` 读取人工选中的候选（human_status='selected'），逐个抓取详情页原始 HTML
2. **AI 处理**: `AIEngine.process()` 处理详情页 HTML：提取结构化数据 + 翻译为中文 + 生成摘要 + 分类 + 质量评估
3. **去重**: 页面缓存（processed_pages + html_hash）+ 事件级 source_id 精确去重 + 标题/时间/地址 hash 内容去重
4. **存储**: 处理后的活动存入 `activities` 表，同时更新 candidate_activities 的 fetched_detail 和 activity_id
5. **人工审核**: 管理界面按城市展示待发布活动，用户预览详情后选择性发布
6. **发布**: 调用海外新生活 `activity/release/` API 发布用户选择的活动
7. **审核**: 平台内置审核系统（`perform_sync_moderation`）自动审核

## 技术栈

| 组件 | 技术选择 | 说明 |
|------|---------|------|
| 语言 | Python 3.12+ | AI/爬虫生态最成熟 |
| 爬虫 | Playwright | 抓取原始 HTML，统一逻辑 |
| AI 引擎 | Kimi kimi-k2.6 (月之暗面) | 提取+翻译+摘要+评估，统一 Prompt |
| HTTP 客户端 | httpx + Playwright | 异步 HTTP + 浏览器渲染 |
| 数据库 | SQLite → PostgreSQL | 轻量起步，后续可迁移 |
| 管理界面 | FastAPI + Jinja2 + HTMX | 人工审核、选择性发布 |
| 定时调度 | APScheduler | Python 原生调度器 |
| 配置管理 | pydantic-settings | 类型安全的配置 |
| 日志 | loguru | 比 logging 更好用 |

## 实施分期

| 阶段 | 内容 | 文档 |
|------|------|------|
| 一期 | TodoCanada + FamilyFunCanada + DiscoverSaskatoon 抓取 + AI 翻译 + 管理界面 + 选择性发布 | [02-scraper-design.md](./02-scraper-design.md) |
| 二期 | 两阶段抓取（列表页 AI 预过滤 + 人工筛选后再抓详情）+ 管理界面完善 | [06-implementation-plan.md](./06-implementation-plan.md) |
| 三期 | Facebook Events + 其他数据源扩展 + AI 相似度去重 | 后续文档 |

## 项目目录结构

```
activities-publish/
├── docs/                    # 设计文档
├── reference/               # 参考代码（海外新生活 API 源码）
├── src/
│   ├── config/              # 配置管理
│   │   └── settings.py      # pydantic-settings 配置
│   ├── scrapers/            # 数据源爬虫（只负责页面导航+抓取原始HTML）
│   │   ├── base.py          # 爬虫基类 + RawPage
│   │   ├── todocanada.py    # TodoCanada
│   │   ├── familyfun.py     # FamilyFunCanada
│   │   └── saskatoon.py     # DiscoverSaskatoon
│   ├── ai/                  # AI 处理模块（统一 Prompt）
│   │   ├── engine.py        # AI 引擎（提取+翻译+摘要+评估）
│   │   └── sanitizer.py     # HTML 安全清理
│   ├── models/              # 数据模型
│   │   └── activity.py      # 活动数据模型
│   ├── storage/             # 数据库操作
│   │   └── db.py            # SQLite 操作
│   ├── publisher/           # 发布模块
│   │   └── woohelps.py      # 海外新生活 API 客户端
│   ├── dedup/               # 去重模块
│   │   └── deduplicator.py  # 活动去重
│   ├── web/                 # 管理界面
│   │   ├── app.py           # FastAPI 应用 + 路由
│   │   └── templates/       # Jinja2 模板
│   └── main.py              # CLI 主入口（抓取调度）
├── tests/                   # 测试
├── pyproject.toml           # 项目配置
├── .env.example             # 环境变量模板
└── Dockerfile               # Docker 部署
```
