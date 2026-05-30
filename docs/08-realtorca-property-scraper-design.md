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
│  │ 目标经纪管理  │  │ 房源审核      │  │ 发布管理      │  │ 任务调度     │ │
│  │ (CRUD Agent) │  │ (Property)   │  │ (Publish)    │  │ (Schedule)  │ │
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
    city_slug TEXT NOT NULL,                    -- saskatoon/toronto/vancouver/...
    province_code TEXT,                         -- sk/on/bc/...

    -- 管理字段
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE(source, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_agents_city ON agents(city_slug);
CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);
```

**示例数据**：

| id | agent_id | name | name_zh | brokerage | city_slug | province_code | is_active |
|----|----------|------|---------|-----------|-----------|---------------|-----------|
| 1 | 2061436 | Don (Xuanzhi) Tang | 唐轩之 | L&T Realty Ltd. | saskatoon | sk | true |

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
    property_type TEXT,
    bedrooms TEXT,
    bathrooms TEXT,
    living_area TEXT,
    lot_size TEXT,
    address TEXT,
    postal_code TEXT,

    -- 图片
    photo_urls TEXT NOT NULL DEFAULT '[]',      -- JSON 数组，来自 API Property.Photo

    -- 状态
    human_status TEXT NOT NULL DEFAULT 'pending',  -- pending/selected/rejected
    fetched_detail BOOLEAN NOT NULL DEFAULT FALSE, -- 是否已抓取详情页描述
    property_id INTEGER,

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(source, source_id)
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

    -- 中文内容
    description_zh TEXT,                        -- 中文摘要（200字内）
    content_zh TEXT NOT NULL DEFAULT '',        -- 中文正文（500-1000字）
    highlights TEXT NOT NULL DEFAULT '[]',      -- 亮点列表 JSON

    -- 图片
    image_url TEXT,
    image_urls TEXT NOT NULL DEFAULT '[]',

    -- 经纪信息（冗余存储）
    agent_name TEXT,
    agent_brokerage TEXT,
    agent_phone TEXT,

    -- 状态
    status TEXT NOT NULL DEFAULT 'pending',
    platform_property_id INTEGER,
    publish_error TEXT,

    content_hash TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE(source, source_id)
);
```

---

## 爬虫设计

### 1. 两阶段爬取流程

```python
async def scrape_agent_listings(agent: Agent) -> List[PropertyCandidate]:
    """爬取单个经纪的所有房源"""

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
        candidate = PropertyCandidate(
            agent_id=agent.id,
            source_id=item["Id"],
            source_url=f"https://www.realtor.ca{item['RelativeDetailsURL']}",
            mls_number=item["MlsNumber"],
            title=item["Property"]["Address"]["AddressText"].split("|")[0],
            price=item["Property"]["Price"],
            price_numeric=item["Property"].get("PriceUnformattedValue"),
            property_type=item["Property"]["Type"],
            bedrooms=str(item["Building"].get("Bedrooms", "")),
            bathrooms=str(item["Building"].get("BathroomTotal", "")),
            living_area=item["Building"].get("SizeInterior", ""),
            lot_size=item["Land"].get("SizeTotal", ""),
            address=item["Property"]["Address"]["AddressText"],
            postal_code=item.get("PostalCode", ""),
            photo_urls=json.dumps([p.get("HighResPath") or p.get("LowResPath")
                                   for p in item["Property"].get("Photo", [])]),
        )
        candidates.append(candidate)

    return candidates


async def fetch_detail_description(candidate: PropertyCandidate) -> str:
    """访问详情页获取房源描述（PublicRemarks）"""

    async with new_stealth_context(...) as context:
        page = await context.new_page()
        await page.goto(candidate.source_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)

        body_text = await page.locator("body").inner_text(timeout=10_000)

        # 提取描述段落（通常以 Welcome/Beautiful/Introducing 开头，200+ 字符）
        description = extract_public_remarks(body_text)
        return description
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

---

## 发布设计

### 方案选择

| 方案 | 说明 | 推荐度 |
|------|------|--------|
| **A. 复用现有 activity API** | 把房产信息当"活动"发布，复用 `/api/applet/activity/release` | 需要确认平台是否支持 |
| **B. 新增房产专用 API** | 如果 woohelps 平台有房产/分类信息接口，使用专用接口 | 最优，但需要平台配合 |

**建议**：先确认 woohelps 平台是否有"房产/二手交易/分类信息"类的发布接口。如果没有，复用 activity 接口，通过 `activity_type` 字段区分（如新增 type=7 表示房产）。

### 发布字段映射

```python
{
    "name": property.title_zh,
    "description": property.content_zh,
    "city_id": city_id,
    "address": property.address,
    "img": property.image_url,
    "imgs": property.image_urls,
    "price": property.price_numeric,
    "price_str": property.price,
    "property_type": property.property_type,
    "bedrooms": property.bedrooms,
    "bathrooms": property.bathrooms,
    "living_area": property.living_area,
    "mls_number": property.mls_number,
}
```

---

## 管理后台扩展

### 1. Agents 管理 (`/admin/agents`)

| 功能 | 说明 |
|------|------|
| 列表 | 显示所有目标经纪 |
| 添加 | 输入 agent_id + 城市，系统自动抓取经纪基本信息 |
| 编辑 | 修改备注、启用/禁用 |
| 删除 | 软删除 |
| 批量导入 | 从 CSV/Excel 导入经纪列表 |

**添加经纪的自动化**：输入 `agent_id`（如 2061436）后，系统自动：
1. 访问 `https://www.realtor.ca/agent/{agent_id}`
2. 从页面提取：name, brokerage, phone, city, province
3. 存入 agents 表

### 2. Property Candidates (`/admin/property-candidates`)

- 显示 API 返回的房源摘要（价格、地址、房型、图片缩略图）
- 人工选择/拒绝
- 触发详情页描述抓取

### 3. Properties (`/admin/properties`)

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

- [ ] 确认 woohelps 平台发布接口
- [ ] 实现房产发布方法

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
| API 限流或返回 429 | 增加延迟（每页间隔 2-3 秒），单日抓取量控制 |
| realtor.ca 反爬升级 | Stealth 机制已验证有效；API 调用比页面渲染更难检测 |
| 页面结构改版 | 详情页描述提取使用文本关键词匹配（非 CSS 选择器），容错性强 |
| 房源信息不准确 | 保留 MLS 号码和 source_url，用户可追溯原始信息 |
| 法律合规 | 只爬公开房源 + 标注来源 + 保留经纪信息 |
| 图片加载慢/失败 | 异步批量下载，失败跳过，不影响发布 |
| Co-listing 房源归属 | 在 properties 表中存储所有参与经纪信息，明确标注 |

---

## 待确认问题

1. **发布接口**：woohelps 平台是否有专门的"房产"发布接口？还是复用现有 activity 接口？
2. **目标经纪**：初期计划覆盖哪些城市、多少位经纪？是否有现成的经纪列表？
3. **价格筛选**：是否需要过滤特定价格范围（如只发布 50万-200万加元的房源）？
4. **更新频率**：房源是实时变化的，多久抓取一次？（建议每天一次，只抓新上架的）
