# 去重 & 存储设计

## 数据库设计 (SQLite)

### 活动表 `activities`

```sql
-- 与 src/storage/db.py _CREATE_TABLES 完全一致
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 来源信息
    source TEXT NOT NULL,              -- todocanada/familyfuncanada/discoversaskatoon
    source_id TEXT NOT NULL,           -- 确定性 ID: source_url#<hash>[:8]
    source_url TEXT NOT NULL,
    city_slug TEXT NOT NULL,           -- toronto/vancouver/...

    -- 标题
    title_en TEXT NOT NULL,
    title_zh TEXT NOT NULL,

    -- 中文处理后的数据
    description_zh TEXT NOT NULL,      -- 中文摘要
    html_zh TEXT NOT NULL,             -- 翻译后的中文 HTML

    -- 时间和地点
    start_time TEXT,                   -- UTC, ISO format
    end_time TEXT,                     -- UTC, ISO format
    timezone TEXT,                     -- IANA 时区（如 America/Toronto）
    address TEXT NOT NULL DEFAULT '',
    venue_name TEXT,

    -- 图片
    image_url TEXT,
    image_urls TEXT NOT NULL DEFAULT '[]',  -- JSON array

    -- 活动属性
    price TEXT,                        -- 原始价格字符串（如 "$10-$20", "Free"）
    is_free INTEGER NOT NULL DEFAULT 1,
    fee_amount REAL NOT NULL DEFAULT 0.0,
    fee_parsed_free INTEGER NOT NULL DEFAULT 1,
    activity_type INTEGER NOT NULL DEFAULT 1,  -- 1-6

    -- AI 处理结果
    highlights TEXT NOT NULL DEFAULT '[]',     -- JSON array

    -- 发布状态
    status TEXT NOT NULL DEFAULT 'pending',    -- pending/published/failed/skipped
    platform_activity_id INTEGER,              -- 平台活动 ID（发布成功后写入）
    publish_error TEXT,                        -- 发布失败原因

    -- 去重
    content_hash TEXT,

    -- 元数据
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_activities_city ON activities(city_slug);
CREATE INDEX IF NOT EXISTS idx_activities_status ON activities(status);
CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_activities_content_hash ON activities(city_slug, content_hash);
```

> **本地时间显示**：数据库只存 UTC 时间 + IANA 时区。管理界面显示本地时间时动态换算：
> ```python
> from zoneinfo import ZoneInfo
> from datetime import datetime, timezone
>
> def utc_to_local(utc_str: str, tz_str: str) -> str:
>     utc_dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
>     return utc_dt.astimezone(ZoneInfo(tz_str)).strftime("%Y-%m-%d %H:%M")
> ```

### 页面处理记录表 `processed_pages`

独立于活动表，用于避免对同一页面重复调用 LLM。通过 html_hash 检测页面内容是否变化，支持持续更新的 guide 页面和失败恢复。

```sql
CREATE TABLE processed_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    UNIQUE(source, source_url),
    html_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    activity_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_processed_pages_hash ON processed_pages(html_hash);
```

```python
import hashlib

def compute_html_hash(html: str) -> str:
    """计算页面 HTML 内容 hash，用于检测页面是否变化"""
    return hashlib.sha256(html.encode()).hexdigest()[:16]
```

**判断逻辑**：
- 查询 processed_pages 中该 source_url 的记录
- 如果存在且 `html_hash` 相同且 `status` 为 `success` 或 `empty` → 跳过（内容未变，已处理）
- 如果 `html_hash` 不同或 `status = 'failed'` → 重新处理（页面已更新或上次失败）
- 查不到记录 → 首次处理

### 抓取任务表 `scrape_tasks`（管理界面用）

跟踪管理界面触发的抓取任务状态，用于 HTMX 轮询显示进度。

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

### 候选活动表 `candidate_activities`

阶段1（Discover）产出的列表页摘要，等待 AI 预过滤和人工筛选。是两阶段抓取流程的核心中间表。

```sql
CREATE TABLE IF NOT EXISTS candidate_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_slug TEXT NOT NULL,           -- 城市 slug
    source TEXT NOT NULL,              -- todocanada/familyfuncanada/discoversaskatoon
    source_url TEXT NOT NULL,          -- 活动详情页 URL
    title TEXT NOT NULL DEFAULT '',    -- 活动标题
    event_date TEXT,                   -- 活动日期（原始文本）
    address TEXT DEFAULT '',           -- 地址
    price TEXT DEFAULT '',             -- 价格（原始文本）
    description TEXT DEFAULT '',       -- 简要描述
    ai_worth_fetching INTEGER,         -- AI 判断：1=值得抓详情, 0=不值得, NULL=未过滤
    ai_reason TEXT,                    -- AI 判断原因
    human_status TEXT NOT NULL DEFAULT 'pending',  -- pending/selected/rejected
    fetched_detail INTEGER NOT NULL DEFAULT 0,     -- 1=已抓详情
    activity_id INTEGER,               -- 关联的 activities 表 ID（抓详情后填入）
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_url)
);

CREATE INDEX idx_candidates_city ON candidate_activities(city_slug);
CREATE INDEX idx_candidates_status ON candidate_activities(human_status);
CREATE INDEX idx_candidates_source ON candidate_activities(source);
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `ai_worth_fetching` | `filter_activities()` 的判断结果。1=值得抓取详情页，0=不值得，NULL=未经过 AI 过滤 |
| `ai_reason` | AI 给出的判断原因（如"社区文化节"、"商业促销"） |
| `human_status` | 人工筛选状态：`pending`(待审核) → `selected`(选中，等待抓详情) / `rejected`(拒绝) |
| `fetched_detail` | 0=未抓详情，1=已抓取详情页并处理 |
| `activity_id` | 关联 `activities.id`，详情页处理完成后填入 |

#### 与 `activities` 表的关系

- `candidate_activities`：列表页摘要（待审核），数据来自 `discover_pages()`
- `activities`：已抓详情的正式活动（可发布），数据来自 `process()`

数据流：

```
列表页摘要  →  AI filter  →  candidate_activities
                                    │
                              人工筛选 (Web UI)
                                    │
                              selected 条目
                                    │
                              抓详情页 HTML
                                    │
                              AI process()
                                    │
                              activities 表
                                    │
                              candidate.activity_id 回填
```

#### 数据库方法

```python
# 写入
await db.save_candidates(candidates)           # 批量保存候选（UPSERT by source+source_url）

# 查询
await db.list_candidates(city_slug, ai_worth, human_status, limit, offset)
await db.count_candidates(city_slug, ai_worth, human_status)
await db.get_candidate(candidate_id)
await db.count_candidates_by_city()             # 按城市+状态统计

# 状态更新
await db.update_candidate_status(ids, status)   # 批量更新 human_status
await db.mark_candidate_fetched(id, activity_id) # 标记已抓详情 + 关联 activity

# 阶段2 读取
await db.get_candidates_to_fetch(city_slug)     # human_status='selected' AND fetched_detail=0
```

## 去重策略

### 第零层：页面级缓存（processed_pages）

避免对同一页面重复调用 LLM。通过 `processed_pages` 表记录已处理页面的 `html_hash`：
- 内容未变 + 上次 success/empty → 跳过（节省 LLM 调用）
- 内容变化或上次失败 → 重新处理（支持 guide 页更新和失败恢复）
- 多活动页面的活动通过事件级 source_id 去重，不受页面缓存影响

### 第一层：精确去重（source + source_id）

同一数据源的同一活动 ID 不重复抓取。通过数据库 UNIQUE 约束保证。

### 第二层：内容去重（content_hash）

计算活动标题 + 开始时间 + 地址的内容哈希。为减少地址写法差异导致的漏匹配，对标题做大小写归一化，对地址做空白和标点归一化：

```python
import hashlib
import re

def _normalize_text(text: str) -> str:
    """归一化文本用于 hash 比较：小写、去除多余空白和标点"""
    text = text.lower().strip()
    text = re.sub(r'[,\.\-#]', ' ', text)   # 移除常见标点
    text = re.sub(r'\s+', ' ', text)         # 合并空白
    return text

def compute_content_hash(activity: ProcessedActivity) -> str:
    title = _normalize_text(activity.title_en)
    start = activity.start_time_utc.isoformat()  # 使用 UTC 确保跨时区一致
    address = _normalize_text(activity.address or "")
    content = f"{title}|{start}|{address}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]
```

如果同一城市内已存在相同 content_hash 的活动，则跳过。

### 第三层：相似度去重（AI 辅助，二期实现）

对于精确和 hash 去重都无法捕获的情况（如不同平台发布的同一活动、标题翻译/大小写差异），使用 AI 进行相似度判断：

```python
SIMILARITY_PROMPT = """判断以下两个活动是否是同一个活动：

活动A: {title_a}，时间: {time_a}，地址: {address_a}
活动B: {title_b}，时间: {time_b}，地址: {address_b}

只返回 true 或 false。
"""
```

### 一期去重限制说明

一期（第一层 + 第二层）存在以下已知限制，二期需解决：

| 场景 | 一期处理方式 | 二期方案 |
|------|-------------|---------|
| 跨来源同一活动（如 TodoCanada 和 FamilyFunCanada 同时收录） | 仅靠 content hash 匹配，标题差异大时会漏判 | AI 相似度去重 |
| 地址写法不同（如 "123 Main St" vs "123 Main Street"） | 归一化后部分覆盖，但缩写/全称仍可能不一致 | 地址标准化 + AI |
| 系列活动多场次 | 各场次有不同 source_id，不会被误去重 | 需要考虑是否合并展示 |
| 标题翻译/大小写差异 | 归一化后覆盖常见情况 | AI 相似度 |

## 候选活动状态流转

`candidate_activities.human_status` 跟踪人工筛选状态：

```
pending ──→ selected ──→ (fetched_detail=1, activity_id 回填)
   │
   └──→ rejected
```

| 状态 | 说明 |
|------|------|
| `pending` | Discover 写入，等待人工审核 |
| `selected` | 人工勾选，等待抓详情 |
| `rejected` | 人工拒绝 |

## 发布状态流转

`activities.status` 跟踪正式活动的发布状态：

```
pending ──→ published
   │
   ├──→ failed ──→ published (重试成功)
   │
   └──→ skipped (内容重复，跳过)
```

| 状态 | 说明 | 对应代码 |
|------|------|---------|
| `pending` | 已入库，等待发布 | `db.save()` 写入 |
| `published` | 已成功发布到平台 | `mark_published()` 设置 `status='published'` + `platform_activity_id` |
| `failed` | 发布失败，可重试 | `mark_publish_failed()` 设置 `status='failed'` + `publish_error` |
| `skipped` | 内容重复，跳过 | `mark_skipped()` 设置 `status='skipped'` |

`fetch_selected_details()` 只做存储（`status='pending'`），活动在管理界面等待人工审核，由用户手动触发发布。
