# 多源活动爬虫设计

## 数据源概览

| 数据源 | 网址 | 覆盖城市 | 爬取方式 | 优先级 |
|--------|------|---------|---------|--------|
| TodoCanada | todocanada.ca | 10+ 城市 | Playwright 抓取 | 一期 |
| FamilyFunCanada | familyfuncanada.com | 主要城市 | WordPress REST API | 一期 |
| DiscoverSaskatoon | discoversaskatoon.com | Saskatoon | Playwright 抓取 | 一期 |
| Facebook Events | facebook.com/events | 全部 | Playwright（需登录） | 二期 |

## 技术栈

| 工具 | 用途 |
|------|------|
| **Playwright** | 核心浏览器引擎，处理 JS 渲染页面、动态加载 |
| **httpx** | 异步 HTTP 客户端，调用 REST API、下载图片 |
| **Crawl4AI** | AI 友好提取层（可选，二期） |
| **Jina Reader** | 轻量备用方案（简单页面） |

```bash
# 依赖
pip install playwright httpx beautifulsoup4 lxml
playwright install chromium
```

## 城市坐标配置

```python
CITIES = {
    "toronto":    {"eng_name": "Toronto",     "lat": 43.6532,  "lng": -79.3832,  "radius": "50km"},
    "vancouver":  {"eng_name": "Vancouver",   "lat": 49.2827,  "lng": -123.1207, "radius": "50km"},
    "montreal":   {"eng_name": "Montreal",    "lat": 45.5017,  "lng": -73.5673,  "radius": "50km"},
    "calgary":    {"eng_name": "Calgary",     "lat": 51.0447,  "lng": -114.0719, "radius": "50km"},
    "edmonton":   {"eng_name": "Edmonton",    "lat": 53.5461,  "lng": -113.4938, "radius": "50km"},
    "ottawa":     {"eng_name": "Ottawa",      "lat": 45.4215,  "lng": -75.6972,  "radius": "50km"},
    "winnipeg":   {"eng_name": "Winnipeg",    "lat": 49.8954,  "lng": -97.1385,  "radius": "50km"},
    "saskatoon":  {"eng_name": "Saskatoon",   "lat": 52.1332,  "lng": -106.6700, "radius": "50km"},
    "regina":     {"eng_name": "Regina",      "lat": 50.4452,  "lng": -104.6189, "radius": "50km"},
    "moncton":    {"eng_name": "Moncton",     "lat": 46.0878,  "lng": -64.7782,  "radius": "50km"},
}
```

---

## 数据源 1: TodoCanada

**网址**: `https://www.todocanada.ca/`
**特点**: 服务端渲染，按城市列出活动，每月视图
**爬取方式**: Playwright + CSS 选择器

### 页面结构

活动列表页 URL 模式: `https://www.todocanada.ca/{city}/events/`

每个活动条目包含:
- 日期徽章（月/日）
- 活动标题 + 链接
- 场地地址
- 日期文本描述
- 电话
- 价格（如 "Price: Free", "Price: $5"）
- 截断的活动描述

### 城市路径映射

```python
TODOCANADA_CITY_SLUGS = {
    "toronto": "toronto",
    "vancouver": "vancouver",
    "calgary": "calgary",
    "edmonton": "edmonton",
    "ottawa": "ottawa",
    "winnipeg": "winnipeg",
    "montreal": "montreal",
    "saskatoon": "saskatoon",
    "regina": "regina",
    "moncton": "moncton",
}
```

### 爬虫实现

> **⚠️ 选择器未验证**: 以下代码中的 CSS 选择器为推测性选择器，实现前必须按 Spike 验证清单确认实际页面结构。

```python
class TodoCanadaScraper(BaseScraper):
    """TodoCanada 活动爬虫 — Playwright"""

    BASE_URL = "https://www.todocanada.ca"

    async def fetch_activities(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawActivity]:
        slug = TODOCANADA_CITY_SLUGS[city_slug]
        url = f"{self.BASE_URL}/{slug}/events/"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle")

            # 获取活动列表
            items = await page.query_selector_all("article, .event-item, .listing-item")

            activities = []
            for item in items:
                title_el = await item.query_selector("h2 a, h3 a, .event-title a")
                if not title_el:
                    continue

                title = await title_el.inner_text()
                detail_url = await title_el.get_attribute("href")

                # 提取基本字段
                address = await self._extract_text(item, ".address, .location, .venue")
                price_text = await self._extract_text(item, ".price")
                desc = await self._extract_text(item, ".description, .excerpt, p")

                # 进入详情页获取完整信息
                detail = await self._scrape_detail(page, detail_url)

                activity = RawActivity(
                    source="todocanada",
                    source_id=self._extract_source_id(detail_url),
                    source_url=detail_url,
                    title_en=title.strip(),
                    description_en=desc,
                    html_en=detail.get("html", ""),
                    start_time_local=detail.get("start_time", start_date),
                    end_time_local=detail.get("end_time", end_date),
                    address=address,
                    latitude=detail.get("latitude"),
                    longitude=detail.get("longitude"),
                    image_url=detail.get("image_url"),
                    is_free=self._parse_is_free(price_text),
                    price=price_text,
                    venue_name=detail.get("venue_name"),
                    city_slug=city_slug,
                    url=detail_url,
                    category=detail.get("category"),
                )
                activities.append(activity)

            await browser.close()
        return activities
```

### 注意事项

- 列表页可能显示多个区域的活动（如 London 地区出现在 Toronto 页面），需按地址过滤
- 部分活动没有详细时间信息，需进入详情页获取
- 价格格式不统一（"Free", "$5", "$10-$20"），需灵活解析

---

## 数据源 2: FamilyFunCanada

**网址**: `https://www.familyfuncanada.com/`
**特点**: WordPress + "The Events Calendar" 插件
**爬取方式**: REST API（JSON），无需浏览器

### REST API 端点

```
GET /{city}/wp-json/tribe/events/v1/events
```

**关键参数**:
| 参数 | 说明 | 示例 |
|------|------|------|
| `start_date` | 开始日期 | `2026-05-07` |
| `end_date` | 结束日期 | `2026-06-07` |
| `per_page` | 每页数量 | 50 |
| `page` | 页码 | 1 |

**响应格式** (JSON):
```json
{
    "events": [
        {
            "id": 12345,
            "title": "Summer Music Festival",
            "description": "<p>Full HTML description...</p>",
            "url": "https://www.familyfuncanada.com/event/...",
            "start_date": "2026-05-15",
            "start_date_details": {"year": "2026", "month": "05", "day": "15", "hour": "10", "minutes": "00"},
            "end_date": "2026-05-15",
            "end_date_details": {"year": "2026", "month": "05", "day": "15", "hour": "18", "minutes": "00"},
            "venue": {
                "venue": "Central Park",
                "address": "123 Main St",
                "city": "Toronto",
                "state": "ON",
                "latitude": "43.6532",
                "longitude": "-79.3832"
            },
            "image": {
                "url": "https://...",
                "full": "https://..."
            },
            "categories": [{"name": "Music"}],
            "cost": "Free"
        }
    ],
    "total": 100,
    "total_pages": 2
}
```

### 城市路径映射

```python
FAMILYFUN_CITY_SLUGS = {
    "toronto": "toronto",
    "vancouver": "vancouver",
    "calgary": "calgary",
    "edmonton": "edmonton",
    "ottawa": "ottawa",
    "winnipeg": "winnipeg",
    "montreal": "montreal",
}
```

### 爬虫实现

```python
class FamilyFunCanadaScraper(BaseScraper):
    """FamilyFunCanada 活动爬虫 — WordPress REST API"""

    BASE_URL = "https://www.familyfuncanada.com"

    async def fetch_activities(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawActivity]:
        slug = FAMILYFUN_CITY_SLUGS.get(city_slug)
        if not slug:
            return []  # 该城市无覆盖

        activities = []
        page = 1

        while True:
            url = f"{self.BASE_URL}/{slug}/wp-json/tribe/events/v1/events"
            params = {
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "per_page": 50,
                "page": page,
            }

            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params)
                data = resp.json()

            for event in data.get("events", []):
                activity = self._map_event(event, city_slug)
                activities.append(activity)

            # 分页
            if page >= data.get("total_pages", 1):
                break
            page += 1

        return activities

    def _map_event(self, event: dict, city_slug: str) -> RawActivity:
        venue = event.get("venue", {}) or {}
        image = event.get("image", {}) or {}

        return RawActivity(
            source="familyfuncanada",
            source_id=str(event["id"]),
            source_url=event.get("url", ""),
            title_en=event.get("title", ""),
            description_en=self._strip_html(event.get("description", "")),
            html_en=event.get("description", ""),
            start_time_local=self._parse_datetime(event.get("start_date_details", {})),
            end_time_local=self._parse_datetime(event.get("end_date_details", {})),
            address=self._format_address(venue),
            latitude=float(venue.get("latitude", 0)) or None,
            longitude=float(venue.get("longitude", 0)) or None,
            image_url=image.get("url") or image.get("full"),
            is_free=(event.get("cost", "") or "").lower() in ("free", ""),
            price=event.get("cost"),
            venue_name=venue.get("venue"),
            city_slug=city_slug,
            url=event.get("url", ""),
            category=self._extract_category(event),
        )
```

### 注意事项

- 并非所有城市都有覆盖（只有主要城市）
- API 可能返回旧数据，需检查日期过滤
- 部分活动可能没有 venue 或 image 信息

---

## 数据源 3: DiscoverSaskatoon

**网址**: `https://www.discoversaskatoon.com/calendar-events`
**特点**: Drupal 10，卡片式布局，"Load More" 动态分页
**爬取方式**: Playwright + CSS 选择器
**覆盖**: 仅 Saskatoon

### 页面结构

**列表页**: `/calendar-events`
- 卡片式活动列表
- 每个卡片: 图片、标题、日期范围文本、"View Event" 链接
- 支持按活动类型和地点过滤
- "Load More" 按钮加载更多（动态分页）

**详情页**: `/calendar-events/{event-slug}`
- 活动标题
- 完整描述（HTML）
- 多个具体日期时间
- 价格（如 "Starting at $65/person"）
- 电话、网站 URL
- 地址、地点名称
- 图片

### 爬虫实现

> **⚠️ 选择器未验证**: 以下代码中的 CSS 选择器为推测性选择器，实现前必须按 Spike 验证清单确认实际页面结构。

```python
class DiscoverSaskatoonScraper(BaseScraper):
    """DiscoverSaskatoon 活动爬虫 — Playwright"""

    BASE_URL = "https://www.discoversaskatoon.com"
    SUPPORTED_CITIES = {"saskatoon"}

    async def fetch_activities(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawActivity]:
        if city_slug not in self.SUPPORTED_CITIES:
            return []

        url = f"{self.BASE_URL}/calendar-events"
        activities = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle")

            # 点击 "Load More" 直到全部加载
            while True:
                load_more = await page.query_selector(
                    'button:has-text("Load More"), a:has-text("Load More")'
                )
                if not load_more:
                    break
                await load_more.click()
                await page.wait_for_timeout(1000)

            # 收集所有活动链接
            cards = await page.query_selector_all(".event-card, .views-row, .card")
            for card in cards:
                link = await card.query_selector('a[href*="calendar-events"]')
                if not link:
                    continue
                detail_url = await link.get_attribute("href")
                if detail_url and detail_url.startswith("/"):
                    detail_url = f"{self.BASE_URL}{detail_url}"

                detail = await self._scrape_detail_page(browser, detail_url)
                if detail:
                    activities.append(detail)

            await browser.close()
        return activities
```

### 注意事项

- 仅覆盖 Saskatoon 一个城市
- "Load More" 分页需要特殊处理
- 日期格式可能是文本描述（如 "May 15 - May 17, 2026"），需解析
- 详情页可能有多个日期场次

---

## 统一爬虫基类

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawActivity:
    """统一的原始活动数据结构"""
    source_id: str
    source: str
    title_en: str
    source_url: str = ""
    description_en: str = ""
    html_en: str = ""
    start_time_local: datetime = None   # 城市本地时间（naive datetime）
    end_time_local: datetime = None     # 城市本地时间（naive datetime）
    timezone: str = ""                  # IANA 时区（如 America/Toronto）
    start_time_utc: datetime = None     # UTC 时间（由转换函数填充）
    end_time_utc: datetime = None       # UTC 时间（由转换函数填充）
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    image_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    is_free: bool = True
    price: str | None = None
    venue_name: str | None = None
    city_slug: str = ""
    url: str = ""
    category: str | None = None


class BaseScraper(ABC):
    """爬虫基类"""

    @abstractmethod
    async def fetch_activities(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawActivity]:
        ...

    @property
    def supported_cities(self) -> set[str]:
        """返回该爬虫支持的城市集合"""
        return set(CITIES.keys())

    @staticmethod
    def _strip_html(html: str) -> str:
        """移除 HTML 标签"""
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml").get_text(separator=" ", strip=True)
```

## 爬虫注册 & 调度

```python
SCRAPERS: dict[str, type[BaseScraper]] = {
    "todocanada": TodoCanadaScraper,
    "familyfuncanada": FamilyFunCanadaScraper,
    "discoversaskatoon": DiscoverSaskatoonScraper,
}


async def fetch_all_activities(
    city_slug: str, start_date: datetime, end_date: datetime
) -> list[RawActivity]:
    """从所有数据源抓取活动"""
    all_activities = []

    for name, scraper_cls in SCRAPERS.items():
        scraper = scraper_cls()
        if city_slug not in scraper.supported_cities:
            continue
        try:
            activities = await scraper.fetch_activities(city_slug, start_date, end_date)
            all_activities.extend(activities)
        except Exception as e:
            logger.error(f"Scraper {name} failed for {city_slug}: {e}")

    return all_activities
```

## 抓取后处理：时间 enrich

爬虫只填充 `start_time_local` / `end_time_local` / `timezone`。必须在抓取完成后、写入数据库前，统一调用 `local_to_utc` 填充 UTC 字段，否则后续去重（依赖 `start_time_utc`）会因字段为空而报错。

```python
from zoneinfo import ZoneInfo
from datetime import datetime, timezone as tz

CITY_TIMEZONES = {
    "toronto":    "America/Toronto",
    "vancouver":  "America/Vancouver",
    "montreal":   "America/Toronto",
    "calgary":    "America/Edmonton",
    "edmonton":   "America/Edmonton",
    "ottawa":     "America/Toronto",
    "winnipeg":   "America/Winnipeg",
    "saskatoon":  "America/Regina",
    "regina":     "America/Regina",
    "moncton":    "America/Moncton",
}

def enrich_time_fields(activity: RawActivity) -> None:
    """抓取后立即调用：填充 timezone 和 start_time_utc / end_time_utc。"""
    tz_name = CITY_TIMEZONES.get(activity.city_slug)
    if not tz_name:
        logger.warning(f"Unknown timezone for city: {activity.city_slug}")
        return
    activity.timezone = tz_name
    zone = ZoneInfo(tz_name)
    for local_attr, utc_attr in [
        ("start_time_local", "start_time_utc"),
        ("end_time_local", "end_time_utc"),
    ]:
        local_dt = getattr(activity, local_attr)
        if local_dt:
            aware = local_dt.replace(tzinfo=zone)
            utc_dt = aware.astimezone(tz.utc).replace(tzinfo=None)
            setattr(activity, utc_attr, utc_dt)

async def fetch_all_activities(
    city_slug: str, start_date: datetime, end_date: datetime
) -> list[RawActivity]:
    all_activities = []
    for name, scraper_cls in SCRAPERS.items():
        # ... 抓取逻辑同上 ...
        for act in activities:
            enrich_time_fields(act)   # ← 立即填充 UTC 字段
            all_activities.append(act)
    return all_activities
```

## 图片处理

一期先用原始 URL，平台 `img` 字段支持外部 URL。后续可上传到腾讯云 COS。

## 调度策略

- **频率**: 每天凌晨 1 次全量抓取
- **范围**: 未来 30 天活动
- **城市**: 遍历所有 10 个城市
- **并行**: 不同城市的抓取可并行执行
- **去重**: 通过数据库 `UNIQUE(source, source_id)` 约束避免重复处理

---

## 实现前 Spike 验证清单

> **警告**: 上方代码中的 CSS 选择器（如 `article, .event-item, .listing-item`、`.event-card, .views-row, .card`）均为推测性选择器，**尚未在真实页面上验证**。在正式实现爬虫前，必须对每个数据源完成以下 spike 验证。

### 每个数据源必须完成的验证

| 检查项 | 说明 | 通过标准 |
|--------|------|---------|
| 页面采样 | 手动访问 3-5 个城市的列表页和详情页，确认页面结构 | 页面可正常访问，结构一致 |
| 选择器验证 | 用浏览器 DevTools 确认实际 CSS 选择器 | 选择器能准确定位所有活动条目 |
| robots.txt | 检查 `/{source}/robots.txt` | 确认爬取行为合规或确认对非登录用户无限制 |
| 反爬机制 | 测试连续请求 5-10 次，检查是否被封 IP 或出现验证码 | 无反爬限制，或可通过合理延迟绕过 |
| 数据完整性 | 确认能获取到：标题、时间、地址、描述、图片 | 至少 80% 的活动能提取完整信息 |
| 时间格式 | 确认列表页/详情页的时间格式和时区 | 能准确解析为本地 naive datetime |

### 各源 Spike 优先级

1. **FamilyFunCanada** — 优先级最高，REST API 返回结构化 JSON，无需猜测选择器，最稳定可靠
2. **DiscoverSaskatoon** — 仅覆盖 Saskatoon，页面结构相对简单，但需验证 "Load More" 和详情页选择器
3. **TodoCanada** — 最复杂，多城市页面结构可能有差异，需逐城市采样

### 反爬策略

```python
# 通用反爬对策（Playwright 爬虫）
PLAYWRIGHT_CONFIG = {
    "headless": True,
    "request_delay": (2, 5),       # 每次请求间隔 2-5 秒（随机）
    "page_timeout": 30000,          # 页面加载超时 30s
    "user_agent_rotation": True,    # 随机 User-Agent
    "max_retries": 3,               # 失败重试次数
    "retry_delay": 10,              # 重试间隔
}
```

如果 spike 发现某数据源反爬严重或页面结构频繁变化，应降低该源优先级或改用其他数据源替代。
