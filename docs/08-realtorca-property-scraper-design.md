# 加拿大售房信息爬取与发布系统设计

## 背景与目标

在现有活动发布系统基础上，新增**加拿大各城市售房信息**的自动抓取、AI 翻译处理、人工审核与发布功能。

与活动爬虫的核心差异：
- **数据源单一**：聚焦 `realtor.ca`（加拿大最大的 MLS 房源平台）
- **爬取对象精准化**：不是漫无目的地爬整个城市的所有房源，而是只爬**指定地产经纪**的房源
- **筛选方式**：通过 `api2.realtor.ca` 的 `AsyncPropertySearch_Post` API，传入 `IndividualId` 参数精确筛选
- **数据字段差异大**：房产信息有价格、卧室数、浴室数、面积、MLS号、房型、土地面积、建造年份等专属字段

---

## 核心设计：基于 API + 详情页的两阶段爬取

### 为什么选择 API 直接调用 + 详情页补全？

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **爬整个城市所有房源** | 数据量大 | 法律风险高、内容不可控、可能发布敏感/虚假房源 | ❌ 不推荐 |
| **只爬指定经纪房源** | 可控、可合作授权、聚焦优质经纪（如华人经纪） | 需要维护经纪列表 | ✅ **采用** |
| **渲染页面提取数据（原方案）** | 直观、可见即所得 | 慢、资源消耗大、翻页复杂 | ❌ 已废弃 |
| **API 直接调用 + 详情页补描述** | 快、稳定、翻页简单，仅描述需渲染页面 | 需要两种手段配合 | ✅ **采用** |

### API 调研结论

#### 1. 列表数据：通过 Realtor API 直接获取（无需渲染页面）

**端点**：`POST https://api2.realtor.ca/Listing.svc/AsyncPropertySearch_Post`

**关键发现**：
- ✅ **无需先访问任何页面建立 session**，直接调用即返回 200
- ✅ **Playwright 的 `context.request.post` 可直接调用**（共享 stealth context 的 cookie/UA）
- ✅ **分页参数 `CurrentPage` 完全有效**，自动返回总页数
- ✅ **返回数据非常完整**，包含价格、地址、房型、卧室/浴室、面积、图片、经纪信息、MLS号等
- ❌ **`PublicRemarks`（房源描述）始终为空**，必须在详情页补全
- ⚠️ **Co-listing 房源会返回多个经纪**：按 `IndividualId` 搜索会返回该经纪参与的所有房源（包括合作房源），每条房源的 `Individual` 数组包含所有参与经纪

**请求参数**：
```python
{
    "CurrentPage": "1",           # 分页，从 1 开始
    "RecordsPerPage": "11",       # 每页条数（实测固定 11）
    "Sort": "6-D",                # 按最新排序
    "IndividualId": "2061436",    # 经纪唯一 ID
    "IncludePins": "1",
    "Currency": "CAD",
    "IncludeHiddenListings": "false",
    "ApplicationId": "1",
    "CultureId": "1",
    "Version": "7.0",
}
```

**响应结构**：
```json
{
  "ErrorCode": {"Id": 200, "Description": "Success"},
  "Paging": {
    "RecordsPerPage": 11,
    "CurrentPage": 1,
    "TotalRecords": 20,
    "TotalPages": 2
  },
  "Results": [
    {
      "Id": "29758682",
      "MlsNumber": "SK035857",
      "Property": {
        "Price": "$439,900",
        "PriceUnformattedValue": 439900,
        "Type": "Single Family",
        "Address": {
          "AddressText": "105 615 Stensrud ROAD|Saskatoon, Saskatchewan S7W0A1",
          "Longitude": -106.557484,
          "Latitude": 52.147011
        },
        "Photo": [{"LowResPath": "...", "HighResPath": "..."}],
        "Parking": [...]
      },
      "Building": {
        "Bedrooms": 3,
        "BathroomTotal": 3,
        "SizeInterior": "1113 sqft"
      },
      "Land": {
        "SizeTotal": "1113 sqft",
        "LandscapeFeatures": "Lawn, Underground sprinkler, Garden Area"
      },
      "Individual": [
        {
          "IndividualID": 1952035,
          "Name": "Wayne Lin",
          "Organization": {"Name": "L&T Realty Ltd."},
          "Position": "Broker"
        },
        {
          "IndividualID": 2061436,
          "Name": "Don (Xuanzhi) Tang",
          "Organization": {"Name": "L&T Realty Ltd."},
          "Position": "Associate Broker"
        }
      ],
      "RelativeDetailsURL": "/real-estate/29758682/105-615-stensrud-road-saskatoon-willowgrove",
      "RelativeURLEn": "/real-estate/29758682/105-615-stensrud-road-saskatoon-willowgrove",
      "OpenHouse": [...],
      "Tags": [...]
    }
  ]
}
```

> **经纪手机号数据来源说明**：
> `AsyncPropertySearch_Post` API 返回的 `Individual` 数组中**不包含 `phone` 字段**。LLM 输入示例中的 `phone` 数据来源于 `agents` 表的录入信息（手动维护）。若未来需要自动获取，可考虑额外爬取经纪个人页面 `https://www.realtor.ca/agent/{agent_id}/{name-slug}`，但该页面需要有效 session 且反爬较严，当前方案以**人工录入**为准。

#### 2. 房源描述：通过详情页 Playwright 获取

**详情页 URL**：`https://www.realtor.ca{RelativeDetailsURL}`

**关键发现**：
- ✅ **Stealth context 可正常访问**，HTTP 200，无拦截
- ❌ **详情页无内嵌 JSON 数据**（不同于 agent 个人页面）
- ✅ **描述可通过 `page.locator("body").inner_text()` 提取**
- 描述通常以 "Welcome to..." / "Beautiful..." 等开头，长度 200-1000 字符

**示例描述**：
```
Welcome to one of the most desirable locations in Willowgrove! This beautifully maintained
Semi-detached style townhouse shows like new and offers the perfect combination of comfort,
privacy, and convenience. Situated in a quiet, gated community...
```

#### 3. 关于之前 `IndividualId` URL 参数的误区（已纠正）

**错误假设**（设计文档 v1）：
> `https://www.realtor.ca/on/toronto/real-estate?IndividualId=2061436&Sort=6-D`

**实际情况**：
- `?IndividualId=...` 在 `/city/real-estate` 页面**不生效**，返回的是全部房源（如 Toronto 返回 10,672 套）
- 正确的经纪个人页面是：`https://www.realtor.ca/agent/{agent_id}/{name-slug}`
- 但该页面只显示 **4 条**代表性房源，需要点击 "View All" 跳转到 map 视图
- map 视图 `https://www.realtor.ca/map#Sort=6-D&IndividualId=...` **直接访问会被拦截**（Access Denied Error 15），需要先有 session
- **最终方案**：完全绕过页面渲染，直接调用 API 获取列表数据

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         管理后台 (Admin UI)                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ 目标经纪管理  │  │ 房源审核      │  │ 发布管理      │  │ 任务监控     │ │
│  │ (CRUD Agent) │  │ (Property)   │  │ (Publish)    │  │ (Tasks)     │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         爬取引擎 (Scraper Engine)                        │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ ① 读取目标经纪列表                                                │ │
│  │ ② 调用 AsyncPropertySearch_Post API 获取所有分页房源（JSON）      │ │
│  │ ③ 对每条房源：用 Stealth Playwright 访问详情页，提取描述文本      │ │
│  │ ④ 合并 API 数据 + 详情页描述 → LLM 提取结构化数据 + 中文翻译     │ │
│  │ ⑤ 图片下载 → 转存 DO Spaces                                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         数据层 (Storage)                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ agents       │  │ properties   │  │ property_    │  │ scrape_     │ │
│  │ (目标经纪表)  │  │ (房源主表)   │  │ candidates   │  │ tasks       │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         发布层 (Publisher)                               │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ 调用海外新生活房产发布 API（或复用现有 activity API）                │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 数据模型设计

### 1. agents — 目标地产经纪表

```sql
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,

    -- 来源标识
    source TEXT NOT NULL DEFAULT 'realtorca',
    agent_id TEXT NOT NULL,                     -- realtor.ca IndividualId (如 2061436)

    -- 经纪信息
    name TEXT NOT NULL,                         -- 英文原名
    name_zh TEXT,                               -- 中文名（如有）
    brokerage TEXT,                             -- 所属经纪公司
    phone TEXT,
    email TEXT,
    website TEXT,

    -- 覆盖城市/省份
    city_slugs TEXT NOT NULL DEFAULT '[]',      -- JSON 数组 ["saskatoon", "toronto"]
    province_code TEXT,                         -- sk/on/bc/...

    -- 管理字段
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE(source, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_agents_city ON agents USING GIN(city_slugs);
CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);
```

**示例数据**：

| id | agent_id | name | name_zh | brokerage | city_slugs | province_code | is_active |
|----|----------|------|---------|-----------|------------|---------------|-----------|
| 1 | 2061436 | Don (Xuanzhi) Tang | 唐轩之 | L&T Realty Ltd. | ["saskatoon"] | sk | true |

### 2. property_candidates — 房源候选表

```sql
CREATE TABLE IF NOT EXISTS property_candidates (
    id SERIAL PRIMARY KEY,

    city_slug TEXT NOT NULL,
    agent_id INTEGER REFERENCES agents(id),
    source TEXT NOT NULL DEFAULT 'realtorca',
    source_id TEXT NOT NULL,                    -- realtor.ca 房源 Id (如 29758682)
    source_url TEXT NOT NULL,                   -- 详情页 URL
    mls_number TEXT,                            -- MLS 号码

    -- API 返回的原始摘要（列表阶段即已知）
    title TEXT,                                 -- 地址作为标题
    price TEXT,
    price_numeric NUMERIC,
    previous_price_numeric NUMERIC,             -- 用于价格变动检测
    property_type TEXT,
    bedrooms TEXT,
    bathrooms TEXT,
    living_area TEXT,
    lot_size TEXT,
    address TEXT,
    postal_code TEXT,
    latitude NUMERIC,
    longitude NUMERIC,

    -- 图片
    photo_urls TEXT NOT NULL DEFAULT '[]',      -- JSON 数组，来自 API Property.Photo

    -- 开放日
    open_house TEXT NOT NULL DEFAULT '[]',      -- JSON 数组，来自 API OpenHouse

    -- 房源生命周期（相对于该经纪的视图）
    listing_status TEXT NOT NULL DEFAULT 'active',
                                                -- active / price_changed / sold / delisted
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(), -- 最近一次在 API 中见到该房源
    miss_count INTEGER NOT NULL DEFAULT 0,      -- 连续未在 API 中见到的次数
    history_log TEXT NOT NULL DEFAULT '[]',     -- 变更历史：[{"field":"price","old":400000,"new":439900,"at":"..."}]

    -- 人工审核
    human_status TEXT NOT NULL DEFAULT 'pending',  -- pending / selected / rejected
    fetched_detail BOOLEAN NOT NULL DEFAULT FALSE, -- 是否已抓取详情页描述
    fetch_error TEXT,                           -- 详情页抓取失败原因（如 detail_failed）
    property_id INTEGER,                        -- 关联到 properties.id（当选中时）

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE(source, source_id, agent_id)         -- 同一房源可被不同经纪各自追踪（co-listing）
);
```

### 3. properties — 房源主表

```sql
CREATE TABLE IF NOT EXISTS properties (
    id SERIAL PRIMARY KEY,

    source TEXT NOT NULL DEFAULT 'realtorca',
    source_id TEXT NOT NULL,                    -- 房源 Id
    source_url TEXT NOT NULL,
    city_slug TEXT NOT NULL,
    agent_id INTEGER REFERENCES agents(id),

    -- 标题
    title_en TEXT NOT NULL,                     -- 英文标题（地址）
    title_zh TEXT NOT NULL,                     -- 中文标题

    -- 房产核心字段
    price TEXT NOT NULL,
    price_numeric NUMERIC,
    mls_number TEXT,
    property_type TEXT,

    -- 房间信息
    bedrooms TEXT,
    bathrooms TEXT,

    -- 面积
    living_area TEXT,
    lot_size TEXT,

    -- 建筑信息
    year_built TEXT,
    stories TEXT,
    garage TEXT,

    -- 地址
    address TEXT NOT NULL,
    neighborhood TEXT,
    postal_code TEXT,
    latitude NUMERIC,
    longitude NUMERIC,

    -- 中文内容
    description_zh TEXT,                        -- 中文摘要（200字内）
    content_zh TEXT NOT NULL DEFAULT '',        -- 中文正文（500-1000字）
    highlights TEXT NOT NULL DEFAULT '[]',      -- 亮点列表 JSON

    -- 开放日
    open_house TEXT NOT NULL DEFAULT '[]',      -- JSON 数组

    -- 图片
    image_url TEXT,
    image_urls TEXT NOT NULL DEFAULT '[]',

    -- 经纪信息（冗余存储）
    agent_name TEXT,
    agent_brokerage TEXT,
    agent_phone TEXT,

    -- 房源生命周期与发布状态
    status TEXT NOT NULL DEFAULT 'pending',
                                                -- pending → ai_processing → review → published
                                                -- → price_changed（价格变动需重新审核）
                                                -- → delisted（下架）
    last_scraped_at TIMESTAMP,                  -- 最近一次成功爬取时间
    delisted_at TIMESTAMP,                      -- 标记为下架时间

    platform_property_id INTEGER,
    publish_error TEXT,

    content_hash TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE(source, source_id)                   -- 同一房源在平台只发布一次
);
```

### 4. scrape_tasks — 爬虫任务调度与执行记录表

```sql
CREATE TABLE IF NOT EXISTS scrape_tasks (
    id SERIAL PRIMARY KEY,

    agent_id INTEGER REFERENCES agents(id),
    task_type TEXT NOT NULL DEFAULT 'agent_listings',
                                                -- agent_listings / detail_fetch / image_download
    status TEXT NOT NULL DEFAULT 'pending',     -- pending / running / completed / failed / cancelled
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- 执行参数
    params TEXT NOT NULL DEFAULT '{}',          -- JSON：{"start_page":1,"end_page":2}

    -- 执行结果
    total_fetched INTEGER DEFAULT 0,            -- 本任务抓取总数
    new_candidates INTEGER DEFAULT 0,           -- 新增候选数
    updated_candidates INTEGER DEFAULT 0,       -- 更新候选数
    delisted_candidates INTEGER DEFAULT 0,      -- 下架候选数
    failed_count INTEGER DEFAULT 0,             -- 失败条数
    error_log TEXT,                             -- 失败摘要（截断）

    -- 运行时上下文
    scraper_version TEXT,                       -- 代码版本或 Git commit
    ip_address TEXT,                            -- 实际出口 IP（调试用）

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scrape_tasks_agent ON scrape_tasks(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scrape_tasks_status ON scrape_tasks(status, created_at DESC);
```

---

## 数据流转与房源生命周期

### 1. property_candidates → properties 的流转逻辑

两张表字段有重叠，但职责不同：

| 表 | 职责 | 数据流向 |
|----|------|----------|
| `property_candidates` | 经纪视角的原始数据池 | 只写入、更新，人工选择后流入 properties |
| `properties` | 平台视角的待发布/已发布内容 | 由 candidate `selected` 触发创建，AI 翻译后写入 |

**流转步骤**：

1. **API 入库**：每次爬取将房源写入 `property_candidates`。若 `source_id + agent_id` 已存在，更新字段并记录 `last_seen_at`；否则插入。
2. **人工选择**：Admin 在 Candidates 页面点击"选择"，`human_status = 'selected'`。
3. **详情页抓取**：若 `fetched_detail = FALSE`，触发 Playwright 抓取描述，完成后进入 AI 处理。
4. **AI 处理**：调用 LLM 生成中文内容，结果**复制写入** `properties` 表（新建记录）。
5. **关联回写**：`property_candidates.property_id` 更新为 `properties.id`，建立关联。
6. **发布**：Admin 在 Properties 页面审核并发布。发布成功后 `properties.status = 'published'`。

**Co-listing 重复处理**：
`properties` 保持 `UNIQUE(source, source_id)`。当第二个经纪的 candidate 被选中时，若 `properties` 中已存在该房源：
- **方案 A（推荐）**：提示"该房源已由其他经纪发布"，拒绝创建重复内容，保持平台房源唯一性。
- **方案 B（覆盖）**：更新 `properties.agent_id` 等字段，将房源归属切换为最新选中的经纪（需确认业务需求）。

### 2. 增量更新与房源状态机

每次爬取不仅是"插入新房源"，还要处理**存量房源的生命周期**：

**状态定义（`property_candidates.listing_status`）**：

```
active ──[价格变动]──→ price_changed ──[人工确认/重新发布]──→ active
  │
  └──[API 不再返回]──→ delisted
```

- `active`：正常在售，最近一次爬取仍在 API 返回中。
- `price_changed`：`price_numeric` 与上次不一致，需要人工重新审核。
- `delisted`：连续 **2 次**爬取均未在 API 返回中见到，标记为下架（避免单次 API 波动误杀）。
- `sold`：若 API 明确返回 `Status = "Sold"`，直接标记。

**增量更新伪代码**：

```python
async def incremental_update(agent: Agent, current_ids: Set[str]):
    now = datetime.utcnow()

    # 1. 更新已见房源的 last_seen_at
    await db.execute(
        "UPDATE property_candidates SET last_seen_at = %s "
        "WHERE agent_id = %s AND source_id = ANY(%s)",
        (now, agent.id, list(current_ids))
    )

    # 2. 检测价格变动
    rows = await db.fetchall(
        "SELECT id, source_id, price_numeric, previous_price_numeric "
        "FROM property_candidates WHERE agent_id = %s AND source_id = ANY(%s)",
        (agent.id, list(current_ids))
    )
    for row in rows:
        if row["price_numeric"] != row["previous_price_numeric"]:
            await mark_price_changed(row["id"], row["price_numeric"])

    # 3. 检测下架：连续 2 次未在 API 中见到才标记为 delisted
    #    本次未见到 → miss_count + 1；本次见到了 → miss_count 重置为 0
    await db.execute(
        "UPDATE property_candidates SET miss_count = miss_count + 1 "
        "WHERE agent_id = %s AND listing_status = 'active' AND source_id NOT IN %s",
        (agent.id, current_ids)
    )
    await db.execute(
        "UPDATE property_candidates SET miss_count = 0 "
        "WHERE agent_id = %s AND source_id IN %s",
        (agent.id, current_ids)
    )

    # 正式标记下架：miss_count >= 2
    delisted = await db.fetchall(
        "SELECT id, source_id FROM property_candidates "
        "WHERE agent_id = %s AND listing_status = 'active' AND miss_count >= 2",
        (agent.id,)
    )
    for row in delisted:
        await mark_delisted(row["id"])
```

**字段变更追踪**：
除价格外，`bedrooms`、`bathrooms`、`living_area`、`photo_urls` 等字段的变更写入 `history_log`（JSON 数组），供 Admin 查看历史，但不自动触发状态机变化。

---

## 爬虫设计

### 0. 调度策略（已废弃）

> **⚠️ 2025-05-30 更新**：自动调度器已移除。所有爬虫任务改为**纯人工触发**（通过管理后台 `/agents` 页面点击「抓取」按钮）。以下内容为历史设计参考，不再自动执行。

| 维度 | 策略 | 说明 |
|------|------|------|
| **频率** | 每个活跃经纪每天抓取 **1 次** | 加拿大 MLS 房源更新频率不高，日维度足够 |
| **时段** | 避开加拿大本地高峰（9:00–11:00, 19:00–21:00 CST） | 推荐在 UTC 6:00–10:00（加拿大凌晨）执行，降低对 realtor.ca 的压力 |
| **并发** | 经纪间**串行**，页内**串行** | 避免触发反爬。多经纪之间通过队列依次执行，每个经纪的翻页间隔 **2–3 秒** |
| **失败重调度** | 失败任务 30 分钟后重试，最多 3 次 | 记录在 `scrape_tasks` 表中，由调度器扫描 `status = 'failed'` 记录 |

**调度器伪代码**（已废弃）：

```python
async def scheduler():
    """每日调度入口（已废弃，改为人工触发）"""
    agents = await db.fetchall("SELECT * FROM agents WHERE is_active = TRUE")
    for agent in agents:
        # 检查该经纪今天是否已成功执行过
        today_task = await get_today_task(agent.id)
        if today_task and today_task["status"] == "completed":
            continue

        task = await create_scrape_task(agent.id, task_type="agent_listings")
        try:
            await run_agent_scrape(task, agent)
            await complete_task(task, status="completed")
        except Exception as e:
            await complete_task(task, status="failed", error=str(e))
```

**详情页抓取的异步调度**（已废弃）：
`fetched_detail = FALSE` 的候选记录由独立的 `detail_fetch` 任务批量处理：
- 每次从 `property_candidates` 中选取 `human_status = 'selected' AND fetched_detail = FALSE` 的前 **20 条**。
- 每条详情页抓取独立执行，单条失败不影响同批次其他记录。
- 批次间隔 **5 秒**，避免对 realtor.ca 详情页造成突发流量。
- 抓取成功后更新 `fetched_detail = TRUE`；失败则写入 `fetch_error`，由下次调度重新拾起。

> 人工触发方式：管理后台 `/agents` → 点击经纪右侧「抓取」按钮 → 手动触发 `scrape_agent()`。

---

### 1. 两阶段爬取流程

```python
async def scrape_agent_listings(agent: Agent) -> List[PropertyCandidate]:
    """爬取单个经纪的所有房源

    注意：API 只需要 IndividualId，不需要城市参数。
    城市信息从房源地址中解析。
    """

    # ── Phase 1: API 获取列表数据 ──
    all_listings = []
    page = 1
    while True:
        data = await call_realtor_api(agent.agent_id, page=page)
        all_listings.extend(data["Results"])
        if page >= data["Paging"]["TotalPages"]:
            break
        page += 1

    # 存入 candidates（去重：source_id = 房源 Id）
    candidates = []
    for item in all_listings:
        # 解析城市（从地址中提取）
        address_text = item["Property"]["Address"]["AddressText"]
        city_slug = parse_city_from_address(address_text)  # 如 "Saskatoon" → "saskatoon"

        candidate = PropertyCandidate(
            agent_id=agent.id,
            city_slug=city_slug,
            source_id=item["Id"],
            source_url=f"https://www.realtor.ca{item['RelativeDetailsURL']}",
            mls_number=item["MlsNumber"],
            title=address_text.split("|")[0],
            price=item["Property"]["Price"],
            price_numeric=item["Property"].get("PriceUnformattedValue"),
            property_type=item["Property"]["Type"],
            bedrooms=str(item["Building"].get("Bedrooms", "")),
            bathrooms=str(item["Building"].get("BathroomTotal", "")),
            living_area=item["Building"].get("SizeInterior", ""),
            lot_size=item["Land"].get("SizeTotal", ""),
            address=address_text,
            postal_code=item.get("PostalCode", ""),
            latitude=item["Property"]["Address"].get("Latitude"),
            longitude=item["Property"]["Address"].get("Longitude"),
            photo_urls=json.dumps([p.get("HighResPath") or p.get("LowResPath")
                                   for p in item["Property"].get("Photo", [])]),
            open_house=json.dumps(item.get("OpenHouse", [])),
        )
        candidates.append(candidate)

    return candidates


async def fetch_detail_description(candidate: PropertyCandidate, max_retries: int = 3) -> str:
    """访问详情页获取房源描述（PublicRemarks）

    错误处理策略：
    - Playwright 超时/断连：退避重试（2s, 4s, 6s）
    - 某条房源详情页失败：记录错误，返回空字符串，不阻塞该经纪的其他房源
    - 连续失败 3 次：标记候选 `fetch_error = 'detail_failed'`，由调度器下次重新拾起
    """

    for attempt in range(1, max_retries + 1):
        try:
            async with new_stealth_context(...) as context:
                page = await context.new_page()
                await page.goto(
                    candidate.source_url,
                    wait_until="domcontentloaded",
                    timeout=30_000
                )
                await page.wait_for_load_state("networkidle", timeout=15_000)
                await asyncio.sleep(2)  # 等待懒加载/动态渲染

                body_text = await page.locator("body").inner_text(timeout=10_000)

                description = extract_public_remarks(body_text)
                if description:
                    return description
                # 提取为空，视为失败，进入重试
                logger.warning(f"Empty description for {candidate.source_id}, attempt {attempt}")

        except (TimeoutError, PlaywrightError) as e:
            logger.warning(f"Detail fetch failed for {candidate.source_id}, attempt {attempt}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(attempt * 2)  # 线性退避
            continue

    # 全部重试耗尽，标记失败
    await db.execute(
        "UPDATE property_candidates SET fetch_error = 'detail_failed' WHERE id = %s",
        (candidate.id,)
    )
    return ""
```

### 1.5 图片下载与转存方案

| 项目 | 策略 |
|------|------|
| **下载数量** | 每套房源最多下载 **20 张**（API 通常返回 20–50 张，取前 20 张 `HighResPath`） |
| **并发控制** | 单套房源内图片**串行下载**；多套房源之间通过 asyncio Semaphore 限制并发数为 **3** |
| **超时与重试** | 单张图片超时 **10 秒**，失败重试 **2 次**，仍失败则跳过该张 |
| **格式处理** | 保持原始格式（通常为 JPG），不做压缩/转码（DO Spaces 存储成本可控） |
| **文件名** | `{city_slug}/{source_id}/{index}.jpg`，便于 CDN 路径组织 |
| **失败策略** | 单张图片失败不影响其他图片和房源发布；若全部图片均失败，允许该房源无图进入审核 |

```python
async def download_photos(candidate: PropertyCandidate, max_concurrent: int = 3) -> List[str]:
    """下载房源图片并转存 DO Spaces"""
    urls = json.loads(candidate.photo_urls)[:20]
    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_one(url: str, idx: int) -> str:
        async with semaphore:
            for attempt in range(1, 3):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                key = f"{candidate.city_slug}/{candidate.source_id}/{idx}.jpg"
                                await upload_to_do_spaces(key, data)
                                return f"https://cdn.example.com/{key}"
                except Exception as e:
                    logger.warning(f"Photo download failed {url}, attempt {attempt}: {e}")
                    await asyncio.sleep(1)
            return ""  # 失败返回空

    results = await asyncio.gather(*[download_one(u, i) for i, u in enumerate(urls)])
    return [r for r in results if r]  # 过滤失败项
```

### 1.6 城市解析方案

`parse_city_from_address()` 负责从 `AddressText` 中提取统一的城市 slug。

**加拿大地址常见格式**：
- `"105 615 Stensrud ROAD|Saskatoon, Saskatchewan S7W0A1"` — 竖线分隔
- `"123 Main St, Toronto, Ontario M5V 2T6"` — 逗号分隔
- `"45 Bay St, St. Catharines, Ontario L2R 1A1"` — 城市名含空格或句点

**解析规则**：
1. 若存在 `|`，取后半部分；否则取全串。
2. 按逗号分割，取倒数第二个片段（城市名）。
3. 去除省份名及其缩写（如 Saskatchewan → sk）。
4. 将城市名统一转为小写 slug（空格替换为连字符，去除句点）。

```python
PROVINCES = {
    "alberta", "british columbia", "manitoba", "new brunswick",
    "newfoundland and labrador", "nova scotia", "ontario",
    "prince edward island", "quebec", "saskatchewan",
    "ab", "bc", "mb", "nb", "nl", "ns", "on", "pe", "qc", "sk", "yt", "nt", "nu",
}

CITY_ALIASES = {
    # "van": "vancouver",  # 可按需扩展别名映射
}

def parse_city_from_address(address_text: str) -> str:
    # 1. 分离街道与市省邮编
    parts = address_text.split("|")
    city_part = parts[-1] if len(parts) > 1 else address_text

    # 2. 按逗号分割，取城市片段（通常是倒数第二段）
    segments = [s.strip() for s in city_part.split(",")]
    city = segments[-2] if len(segments) >= 2 else segments[0]

    # 3. 容错：若城市片段被省份污染，尝试取前一段
    if city.lower() in PROVINCES and len(segments) >= 3:
        city = segments[-3]

    # 4. 标准化为 slug
    city_slug = city.lower().replace(" ", "-").replace(".", "").replace("'", "")
    return CITY_ALIASES.get(city_slug, city_slug)
```

### 2. 描述提取函数

```python
def extract_public_remarks(body_text: str) -> str:
    """从详情页 body text 中提取 PublicRemarks 描述

    策略：查找以常见描述词开头、长度 200-2000 字符的段落
    """
    import re

    # 常见开头词
    starters = [
        r"Welcome to", r"Beautiful", r"Introducing", r"Stunning",
        r"Rare", r"Exceptional", r"Charming", r"Gorgeous",
        r"This", r"Located", r"Proudly", r"Discover",
    ]
    starter_pattern = "|".join(starters)

    # 按段落分割
    paragraphs = [p.strip() for p in body_text.split("\n") if len(p.strip()) > 200]

    for p in paragraphs:
        # 匹配以描述词开头的段落
        if re.match(rf"^(?:{starter_pattern})\b", p, re.IGNORECASE):
            # 清理多余的空白
            return re.sub(r"\s+", " ", p)

    # 兜底：返回最长段落
    if paragraphs:
        return max(paragraphs, key=len)

    return ""
```

### 3. 新文件结构

```
src/
├── scrapers/
│   ├── base.py              # 现有基类
│   ├── browser.py           # 现有（stealth + proxy）
│   ├── todocanada.py        # 现有
│   ├── familyfun.py         # 现有
│   └── realtorca.py         # 新增: realtor.ca 售房爬虫
│       ├── __init__.py
│       ├── api.py           # API 调用封装
│       └── detail.py        # 详情页描述提取
├── models/
│   ├── activity.py          # 现有
│   └── property.py          # 新增: Agent / PropertyCandidate / Property 数据类
├── ai/
│   ├── engine.py            # 现有（扩展: 增加房产提取 prompt）
│   └── prompts.py           # 新增: 集中管理所有 LLM prompt
├── publisher/
│   ├── woohelps.py          # 现有（扩展: 增加房产发布方法）
│   └── property_publisher.py # 新增
└── storage/
    └── db.py                # 扩展: 增加 agents/properties 相关方法
```

---

## AI 处理设计

### 1. 输入数据格式（LLM Prompt）

不再让 LLM 从 HTML 中提取，而是直接传入已结构化的 JSON 数据，让 LLM 做**翻译 + 润色 + 格式化**：

```json
{
  "task": "translate_and_enhance_property",
  "city": "Saskatoon",
  "listing": {
    "mls_number": "SK035857",
    "price": "$439,900",
    "price_numeric": 439900,
    "address": "105 615 Stensrud ROAD, Saskatoon, Saskatchewan S7W0A1",
    "property_type": "Single Family",
    "bedrooms": 3,
    "bathrooms": 3,
    "living_area": "1113 sqft",
    "lot_size": "1113 sqft",
    "stories": "",
    "year_built": "",
    "description_en": "Welcome to one of the most desirable locations in Willowgrove! ...",
    "agents": [
      {"name": "Don (Xuanzhi) Tang", "brokerage": "L&T Realty Ltd.", "phone": "306-715-0318"},
      {"name": "Wayne Lin", "brokerage": "L&T Realty Ltd.", "phone": "306-341-4508"}
    ],
    "features": ["Lawn", "Underground sprinkler", "Garden Area"],
    "parking": "Attached Garage, Other, Parking Space(s) (4)",
    "open_house": [{"Date": "2026-05-30", "StartTime": "2:00 PM", "EndTime": "4:00 PM"}],
    "photo_count": 45
  }
}
```

### 2. LLM 输出格式

```json
{
  "title_zh": "萨斯卡通 Willowgrove 精美联排别墅 | 3卧3卫",
  "description_zh": "位于萨斯卡通最受欢迎的 Willowgrove 社区，这套精心维护的联排别墅状态如新...",
  "content_zh": "【房源简介】\n位于萨斯卡通 Willowgrove 核心地段...\n\n【核心亮点】\n• 3 卧室 3 全卫，1113 平方英尺\n• 带门禁的安静社区，背靠绿地\n• 双车库 + 4 个停车位\n...",
  "highlights": ["Willowgrove 核心地段", "3卧3卫", "双车库", "带门禁社区", "背靠绿地"],
  "suitable": true,
  "suitable_reason": "信息完整，描述积极"
}
```

### 3. 为什么改为结构化输入（而非 HTML）？

| 原方案（HTML → LLM） | 新方案（JSON → LLM） |
|---------------------|---------------------|
| LLM 需要从 HTML 中解析字段 | 字段已由 API 精确提取 |
| HTML 结构变化会导致提取失败 | JSON 结构稳定，不受页面改版影响 |
| 消耗更多 token | token 更少，只让 LLM 做翻译和润色 |
| 需要维护两套 prompt（列表页 + 详情页） | 一套 prompt，输入是结构化数据 |

### 4. 内容质量门禁（Quality Gate）

LLM 输出中的 `suitable` 和 `suitable_reason` 不是装饰字段，而是**自动过滤低质量房源**的开关。门禁规则在调用 LLM 的 **Prompt 中明确约定**，同时在代码层做二次校验：

| 检查项 | 规则 | 不满足时的处理 |
|--------|------|----------------|
| **最少图片数** | `photo_count >= 3` | `suitable = false`，原因："图片过少，无法展示房源" |
| **最少描述长度** | `description_en` 去除空格后 >= 100 字符 | `suitable = false`，原因："房源描述过短，信息不足" |
| **排除类型** | `property_type` 不在黑名单内 | 黑名单：`["Vacant Land", "Parking", "Agriculture", "Commercial"]`。命中则 `suitable = false` |
| **价格异常值** | `price_numeric` 在 `[30_000, 5_000_000]` 加元区间 | 超出区间则 `suitable = false`，原因："价格异常，需人工核实" |
| **关键字段完整性** | `address`, `bedrooms`, `bathrooms`, `price` 均非空 | 任一缺失则 `suitable = false` |

**代码层二次校验**（在写入 `properties` 前执行）：

```python
def quality_gate(candidate: PropertyCandidate, llm_output: dict) -> Tuple[bool, str]:
    """内容质量门禁"""
    if candidate.photo_count < 3:
        return False, "图片少于 3 张"
    if len(candidate.description_en or "") < 100:
        return False, "描述过短"
    if candidate.property_type in {"Vacant Land", "Parking", "Agriculture", "Commercial"}:
        return False, f"类型排除: {candidate.property_type}"
    if not (30_000 <= (candidate.price_numeric or 0) <= 5_000_000):
        return False, "价格异常"
    if not all([candidate.address, candidate.bedrooms, candidate.bathrooms, candidate.price]):
        return False, "关键字段缺失"
    # 同时尊重 LLM 的 suitable 标记
    if not llm_output.get("suitable", True):
        return False, f"LLM 判定不适合: {llm_output.get('suitable_reason', '')}"
    return True, "通过"
```

---

## 发布设计

### 方案选择

✅ **已确认**：woohelps 平台已有专门的房产发布接口，**售房与租房共用同一套端点**，通过 `price_type` 区分。

| 类型 | 端点 | 方法 |
|------|------|------|
| **发布售房** | `/api/applet/rental/release` | `POST` |
| **更新售房** | `/api/applet/rental/update/` | `POST` |

### 认证方式

与发布活动完全一致：
- **Header**: `Login-Session: <token>`
- **Content-Type**: `application/x-www-form-urlencoded`
- Token 获取：先调用 `wx.login` 获取 code，再请求 `GET /api/applet/login/session/get?code=<code>&type=newlife`

### 发布字段映射

参考 miniapp 售房发布页 (`miniapp/src/pages-publish/rental/index.tsx`)，后端对应 `Rental` 模型（`price_type = 2 / PRICE_SALE`）：

| 设计文档字段 | 发布接口字段 | 类型 | 必填 | 说明 |
|-------------|-------------|------|------|------|
| `title_zh` | `name` | string | ✅ | 房源标题（中文） |
| `content_zh` | `description` | string | ✅ | 房源正文描述 |
| `price_numeric` | `price` | string | ✅ | 价格。字符串形式，如 `"439900"`。`"0"` 表示面议 |
| — | `price_type` | string | ✅ | 固定传 `"sellhouse"`，后端映射为 `PRICE_SALE` |
| `city_id` | `city_id` | number | ✅ | 城市 ID（需维护 `city_slug → city_id` 映射表） |
| `address` | `address` | string | ❌ | 完整地址 |
| `latitude/longitude` | `locations` | JSON string | ❌ | `JSON.stringify({"latitude": 52.147, "longitude": -106.557})` |
| `image_urls` | `imgs` | JSON string | ✅ | `JSON.stringify(["https://cdn.../1.jpg", ...])`，最多 18 张 |
| — | `tags` | JSON string | ❌ | `JSON.stringify([{"name": "独立屋"}, {"name": "新上市"}])` |
| `agent_phone` | `phone` | string | ❌ | 经纪手机号 |
| — | `email` | string | ❌ | 经纪邮箱 |
| — | `wechatId` | string | ❌ | 经纪微信号 |

**发布伪代码**：

```python
async def publish_property(property: Property) -> dict:
    payload = {
        "name": property.title_zh,
        "description": property.content_zh,
        "price": str(property.price_numeric),
        "price_type": "sellhouse",
        "city_id": resolve_city_id(property.city_slug),  # saskatoon → 对应 city_id
        "address": property.address,
        "locations": json.dumps({
            "latitude": property.latitude,
            "longitude": property.longitude,
        }),
        "imgs": json.dumps(property.image_urls),
        "tags": json.dumps([{"name": tag} for tag in property.highlights[:5]]),
        "phone": property.agent_phone,
    }

    return await post_form(
        url="https://api.woohelps.com/api/applet/rental/release",
        data=payload,
        headers={"Login-Session": await get_login_session()},
    )
```

### 图片上传链路

与 miniapp 保持一致，直接复用现有基础设施：

1. **获取预签名 URL**：`GET /api/applet/aws/generate-presigned-url?userId={user_id}&fileType=image&fileName={name}&contentType=image/jpeg`
2. **直传 DO Spaces**：`POST https://woohelps.sgp1.digitaloceanspaces.com/{path}`
3. **结果 URL**：`https://woohelps.sgp1.digitaloceanspaces.com/{key}`，直接填入 `imgs` JSON 数组

**注意**：DO Spaces 返回的 URL 是 `http://` 开头，发布前需替换为 `https://`（与 miniapp 处理逻辑一致）。

---

## 管理后台扩展

### 1. Agents 管理 (`/agents`)

| 功能 | 说明 |
|------|------|
| 列表 | 显示所有目标经纪（启用/禁用开关） |
| 添加 | 手动填写表单：agent_id、name、name_zh、brokerage、phone、city_slugs |
| 编辑 | 修改基本信息、备注、启用/禁用 |
| 删除 | 硬删除（数据量小，直接删除） |

**添加经纪**：纯手动录入。在 `/agents/add` 页面填写表单，直接存入 agents 表。

> 说明：realtor.ca 上的经纪信息（换公司、改名等）通过手动编辑更新。

### 2. Property Candidates (`/properties/candidates`)

- 显示 API 返回的房源摘要（价格、地址、房型、图片缩略图）
- 人工选择/拒绝
- 触发详情页描述抓取

### 3. Properties (`/properties`)

- 显示已处理的房源列表（含中文标题、描述、图片）
- 查看详情
- 发布到平台
- 删除

---

## 实施计划

### Phase 1: 基础数据层（1-2 天）

- [ ] 创建数据库 migration：agents / property_candidates / properties 表
- [ ] 创建 `src/models/property.py` 数据类
- [ ] 扩展 `src/storage/db.py`：增加 agents 和 properties 的 CRUD 方法

### Phase 2: 爬虫开发（2-3 天）

- [ ] 创建 `src/scrapers/realtorca/api.py`：封装 AsyncPropertySearch_Post API 调用
- [ ] 创建 `src/scrapers/realtorca/detail.py`：详情页描述提取
- [ ] 实现翻页逻辑（根据 `Paging.TotalPages` 循环）
- [ ] 集成到主调度流程

### Phase 3: AI 引擎扩展（1-2 天）

- [ ] 新增房产翻译/润色 prompt（结构化 JSON 输入）
- [ ] 扩展 `src/ai/engine.py`

### Phase 4: 发布模块（1 天）

- [ ] 实现房产发布方法（调用 `/api/applet/rental/release`）
- [ ] 实现图片预签名上传链路（复用现有 `generate-presigned-url`）
- [ ] 维护 `city_slug → city_id` 映射表

### Phase 5: 管理后台（2-3 天）

- [ ] Agents 管理页面
- [ ] Property Candidates 审核页面
- [ ] Properties 列表与发布页面

### Phase 6: 测试与优化（2 天）

- [ ] 端到端测试
- [ ] 处理边界情况（API 限流、无房源、描述提取失败等）
- [ ] 优化抓取频率和延迟

---

## 关键风险与对策

| 风险 | 对策 |
|------|------|
| API 限流或返回 429 | **每页间隔 2–3 秒**；经纪间间隔 **5 秒**；单经纪每天最多约 **200 次** API 调用（按 20 页×11 条估算）。若遇 429，当前任务中止，30 分钟后由调度器重试。目前 realtor.ca 未公开限流阈值，建议上线前用小规模流量实测并记录实际触发点 |
| realtor.ca 反爬升级 | Stealth 机制已验证有效；API 调用比页面渲染更难检测。备用方案：切换代理 IP 池 |
| 页面结构改版 | 详情页描述提取使用文本关键词匹配（非 CSS 选择器），容错性强 |
| 房源信息不准确 | 保留 MLS 号码和 source_url，用户可追溯原始信息 |
| 法律合规 | 只爬公开房源 + 标注来源 + 保留经纪信息 |
| 图片加载慢/失败 | 异步批量下载，失败跳过，不影响发布 |
| Co-listing 房源归属 | 见上文数据流转章节。`property_candidates` 支持多经纪追踪，`properties` 保持唯一发布 |
| IP 地域限制 | 使用加拿大出口 IP（代理或服务器部署在加拿大）。当前 `browser.py` 已集成代理，需确保代理池包含加拿大节点 |

---

## 监控与告警

### 1. 基于 scrape_tasks 的健康检查

直接查询 `scrape_tasks` 表即可感知爬虫健康，无需额外基建：

| 检查项 | SQL/逻辑示例 | 告警阈值 |
|--------|-------------|----------|
| **今日未完成任务** | `SELECT COUNT(*) FROM scrape_tasks WHERE status != 'completed' AND created_at > CURRENT_DATE` | > 0 且持续 2 小时 |
| **失败率过高** | `SELECT agent_id, failed_count / NULLIF(total_fetched,0) FROM scrape_tasks WHERE created_at > NOW() - INTERVAL '24 hours'` | 失败率 > 20% |
| **长时间无新房源** | `SELECT MAX(created_at) FROM property_candidates` | 超过 48 小时无新增 |

### 2. 简单日志告警

在爬虫主循环中对以下事件发送日志/通知（可接入现有告警通道，如企业微信/钉钉 webhook）：

```python
async def alert_if_needed(task: ScrapeTask):
    if task.status == "failed" and task.failed_count > 5:
        await send_alert(f"爬虫任务失败: agent_id={task.agent_id}, error={task.error_log}")
    if task.delisted_candidates > 10:
        await send_alert(f"大量房源下架: agent_id={task.agent_id}, count={task.delisted_candidates}")
```

### 3. 健康检查端点（可选）

若系统已有 Admin 后端，可暴露简单 HTTP 端点供外部探活：

```python
@app.get("/health/scraper")
async def scraper_health():
    last_success = await db.fetchval(
        "SELECT MAX(completed_at) FROM scrape_tasks WHERE status = 'completed'"
    )
    if not last_success or last_success < datetime.utcnow() - timedelta(hours=25):
        return {"status": "unhealthy", "last_success": last_success}
    return {"status": "healthy", "last_success": last_success}
```


