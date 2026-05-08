# 加拿大活动自动发布系统 — 总体设计

## 项目背景

海外新生活（woohelps.com）是一个面向加拿大华人的社区平台，覆盖 10 个加拿大城市。当前平台上的活动内容主要由用户手动发布，数量有限。本项目的目标是自动从外部数据源抓取加拿大各城市的活动信息，经 AI 处理（翻译、摘要、去重）后，通过平台现有 API 发布到海外新生活平台，丰富平台活动内容。

## 系统目标

- **数据源**: 从 TodoCanada、DiscoverSaskatoon、FamilyFunCanada（一期）抓取活动，Facebook Events（二期）
- **AI 处理**: LLM 统一处理原始 HTML → 提取结构化数据 + 翻译 + 摘要 + 质量评估，一步到位
- **半自动模式**: 自动抓取 + AI 处理 → 通过 API 发布 → 平台内置审核流程过滤
- **覆盖城市**: 温哥华、多伦多、蒙特利尔、卡尔加里、埃德蒙顿、渥太华、温尼伯、萨斯卡通、里贾纳、蒙克顿

## 系统架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  数据源抓取   │ ──→ │  AI 处理引擎  │ ──→ │  去重 & 存储  │ ──→ │  API 发布    │
│  (Scrapers)  │     │  (AI Engine)  │     │  (Storage)   │     │  (Publisher) │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │                    │
  Playwright           Kimi API             SQLite DB         海外新生活 API
  抓取原始HTML          提取+翻译+摘要                           POST activity/release
  (所有源统一逻辑)      (所有源统一Prompt)
```

核心设计：**爬虫只负责页面导航和抓取原始 HTML，LLM 负责提取结构化数据 + 翻译 + 摘要**。所有数据源使用同一套逻辑，无需为每个站点的页面结构写 CSS 选择器来提取数据。

## 数据流

1. **抓取**: Playwright 从各数据源抓取详情页原始 HTML
2. **AI 处理**: LLM 一次调用完成：提取活动信息 + 翻译（英→中）+ 摘要 + 分类 + 质量评估
3. **去重**: 页面缓存（processed_pages + html_hash，避免重复 LLM 调用）+ 事件级 source_id 精确去重 + 标题/时间/地址 hash 内容去重（一期），AI 相似度去重（二期）
4. **存储**: 保存处理后的活动到本地数据库，记录来源和发布状态
5. **发布**: 调用海外新生活 `activity/release/` API 发布活动
6. **审核**: 平台内置审核系统（`perform_sync_moderation`）自动审核

## 技术栈

| 组件 | 技术选择 | 说明 |
|------|---------|------|
| 语言 | Python 3.12+ | AI/爬虫生态最成熟 |
| 爬虫 | Playwright | 抓取原始 HTML，统一逻辑 |
| AI 引擎 | Kimi kimi-k2.6 (月之暗面) | 提取+翻译+摘要+评估，统一 Prompt |
| HTTP 客户端 | httpx + Playwright | 异步 HTTP + 浏览器渲染 |
| 数据库 | SQLite → PostgreSQL | 轻量起步，后续可迁移 |
| 定时调度 | APScheduler | Python 原生调度器 |
| 配置管理 | pydantic-settings | 类型安全的配置 |
| 日志 | loguru | 比 logging 更好用 |

## 实施分期

| 阶段 | 内容 | 文档 |
|------|------|------|
| 一期 | TodoCanada + FamilyFunCanada + DiscoverSaskatoon 抓取 + AI 翻译 + API 发布 | [02-scraper-design.md](./02-scraper-design.md) |
| 二期 | Facebook Events 和其他数据源扩展 | 后续文档 |
| 三期 | 智能去重 + 质量评分 | 后续文档 |

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
│   └── main.py              # 主入口
├── tests/                   # 测试
├── pyproject.toml           # 项目配置
├── .env.example             # 环境变量模板
└── Dockerfile               # Docker 部署
```
