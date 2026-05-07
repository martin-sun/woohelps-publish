# 去重 & 存储设计

## 数据库设计 (SQLite)

### 活动表 `activities`

```sql
CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 来源信息
    source TEXT NOT NULL,              -- 数据来源 (todocanada/familyfuncanada/discoversaskatoon)
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
    start_time DATETIME NOT NULL,      -- UTC（用于去重、排序、发布）
    end_time DATETIME NOT NULL,        -- UTC
    start_time_local DATETIME,         -- 城市本地时间（用于调试）
    end_time_local DATETIME,           -- 城市本地时间
    timezone TEXT,                      -- IANA 时区（如 America/Toronto）
    address TEXT,
    latitude REAL,
    longitude REAL,

    -- 图片
    image_url TEXT,                    -- 原始图片 URL
    local_image_url TEXT,              -- 上传到 COS 后的 URL

    -- 活动属性
    is_free BOOLEAN DEFAULT 1,
    price TEXT,                        -- 爬虫原始价格字符串（如 "$10-$20", "Free"）
    fee_amount REAL DEFAULT 0,         -- 解析后的数值价格，供发布使用
    fee_parsed_free BOOLEAN DEFAULT 1, -- parse_fee_amount 派生，覆盖爬虫 is_free
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

def compute_content_hash(activity: RawActivity) -> str:
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
