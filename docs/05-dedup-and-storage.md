# 去重 & 存储设计

## 数据库设计 (SQLite)

### 活动表 `activities`

```sql
CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 来源信息
    source TEXT NOT NULL,              -- 数据来源 (eventbrite/meetup/etc)
    source_id TEXT NOT NULL,           -- 来源平台活动 ID
    source_url TEXT,                   -- 原始链接

    -- 英文原始数据
    title_en TEXT NOT NULL,
    description_en TEXT,
    html_en TEXT,

    -- 中文处理后的数据
    title_zh TEXT,
    description_zh TEXT,
    html_zh TEXT,

    -- 时间和地点
    start_time DATETIME NOT NULL,      -- UTC
    end_time DATETIME NOT NULL,        -- UTC
    address TEXT,
    latitude REAL,
    longitude REAL,

    -- 图片
    image_url TEXT,                    -- 原始图片 URL
    local_image_url TEXT,              -- 上传到 COS 后的 URL

    -- 活动属性
    is_free BOOLEAN DEFAULT 1,
    price TEXT,
    venue_name TEXT,
    city_slug TEXT NOT NULL,           -- 城市标识
    activity_type INTEGER DEFAULT 1,   -- 活动类型 (1-6)

    -- AI 处理结果
    ai_highlights TEXT,                -- JSON array
    quality_score REAL,                -- 质量评分 0-1
    is_suitable BOOLEAN DEFAULT 1,     -- 是否适合发布

    -- 发布状态
    status TEXT DEFAULT 'pending',     -- pending/processing/published/failed/skipped
    woohelps_activity_id INTEGER,      -- 平台活动 ID
    publish_time DATETIME,             -- 发布时间
    publish_error TEXT,                -- 发布失败原因

    -- 元数据
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- 去重用的 hash
    content_hash TEXT,                 -- 标题+时间的 hash，用于快速去重

    UNIQUE(source, source_id)          -- 同一来源同一活动不重复
);

CREATE INDEX idx_activities_city ON activities(city_slug);
CREATE INDEX idx_activities_status ON activities(status);
CREATE INDEX idx_activities_start_time ON activities(start_time);
CREATE INDEX idx_activities_content_hash ON activities(content_hash);
```

### 抓取记录表 `scrape_logs`

```sql
CREATE TABLE scrape_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    city_slug TEXT NOT NULL,
    start_date DATETIME NOT NULL,
    end_date DATETIME NOT NULL,
    total_fetched INTEGER DEFAULT 0,
    total_new INTEGER DEFAULT 0,
    total_skipped INTEGER DEFAULT 0,
    status TEXT DEFAULT 'success',     -- success/partial/failed
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 去重策略

### 第一层：精确去重（source + source_id）

同一数据源的同一活动 ID 不重复抓取。通过数据库 UNIQUE 约束保证。

### 第二层：内容去重（content_hash）

计算活动标题 + 开始时间 + 地址的内容哈希：

```python
import hashlib

def compute_content_hash(activity: RawActivity) -> str:
    content = f"{activity.title_en}|{activity.start_time.isoformat()}|{activity.address}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]
```

如果同一城市内已存在相同 content_hash 的活动，则跳过。

### 第三层：相似度去重（AI 辅助）

对于精确和 hash 去重都无法捕获的情况（如不同平台发布的同一活动），使用 AI 进行相似度判断：

```python
SIMILARITY_PROMPT = """判断以下两个活动是否是同一个活动：

活动A: {title_a}，时间: {time_a}，地址: {address_a}
活动B: {title_b}，时间: {time_b}，地址: {address_b}

只返回 true 或 false。
"""
```

> 一期只实现第一层和第二层去重，第三层在二期实现。

## 发布状态流转

```
pending ──→ processing ──→ published
   │              │
   │              └──→ failed ──→ pending (重试)
   │
   └──→ skipped (质量评估不通过)
```

| 状态 | 说明 |
|------|------|
| `pending` | 已抓取，等待 AI 处理 |
| `processing` | AI 处理完成，等待发布 |
| `published` | 已成功发布到平台 |
| `failed` | 发布失败，可重试 |
| `skipped` | 质量评估不通过或重复，跳过 |
