# 多源活动爬虫设计

## 核心设计：统一 LLM 提取

**所有数据源使用同一套逻辑**：Playwright 负责页面导航和抓取原始 HTML，LLM 负责提取结构化数据 + 翻译 + 摘要。爬虫不需要为每个站点的页面结构写 CSS 选择器来提取数据。

```
列表页 → 找到详情页链接 → 抓取详情页原始 HTML → LLM 提取+翻译一步到位
```

**爬虫只做两件事**：
1. **导航**: 列表页翻页、找到详情页链接（只需匹配 URL pattern，不需要精确选择器）
2. **抓取**: 获取详情页的完整 HTML 和基本信息（URL、城市、来源）

**LLM 做所有脏活**：从原始 HTML 中提取标题/时间/地点/费用 → 翻译为中文 → 生成摘要 → 分类

## 数据源概览

| 数据源 | 网址 | 覆盖城市 | 爬取方式 | 优先级 |
|--------|------|---------|---------|--------|
| TodoCanada | todocanada.ca | 10+ 城市 | Playwright 抓取 + LLM 提取 | 一期 |
| FamilyFunCanada | familyfuncanada.com | 主要城市 | Playwright 抓取 + LLM 提取 | 一期 |
| DiscoverSaskatoon | discoversaskatoon.com | Saskatoon | Playwright 抓取 + LLM 提取 | 一期 |
| Facebook Events | facebook.com/events | 全部 | Playwright（需登录）+ LLM | 二期 |

## 技术栈

| 工具 | 用途 |
|------|------|
| **Playwright** | 页面导航、JS 渲染、抓取原始 HTML |
| **Kimi kimi-k2.6** | 从原始 HTML 提取结构化数据 + 翻译 + 摘要 |
| **httpx** | 下载图片 |

```bash
pip install playwright httpx
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

## 统一爬虫基类

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawPage:
    """爬虫的输出 — 一个详情页的原始内容"""
    source: str                    # 数据来源标识
    source_url: str                # 详情页 URL
    raw_html: str                  # 详情页完整 HTML
    city_slug: str                 # 城市
    image_url: str | None = None   # 页面主图（从 og:image 或 meta 提取）


class BaseScraper(ABC):
    """爬虫基类 — 只负责页面导航和抓取原始 HTML"""

    @abstractmethod
    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        ...

    @property
    def supported_cities(self) -> set[str]:
        return set(CITIES.keys())

    @staticmethod
    async def _collect_links(page, url_pattern: str) -> list[str]:
        """从列表页收集所有匹配 URL pattern 的链接"""
        links = await page.eval_on_selector_all(
            "a",
            f"els => els.map(a => a.href).filter(h => h.match({url_pattern!r}))"
        )
        return list(set(links))  # 去重

    @staticmethod
    async def _get_og_image(page) -> str | None:
        """提取页面 og:image"""
        el = await page.query_selector('meta[property="og:image"]')
        if el:
            return await el.get_attribute("content")
        return None
```

---

## 数据源 1: TodoCanada

**网址**: `https://www.todocanada.ca/`
**特点**: 按城市列出活动，有详情页

### Spike 验证结果 (2026-05-07)

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 列表页 URL | ✅ `/city/{city}/events/` | 注意：不是 `/{city}/events/` |
| 详情页 URL | ✅ `/city/{city}/event/{slug}/` | 如 `/city/toronto/event/juliet/` |
| 分页 | ✅ WordPress 标准分页 | `/city/{city}/events/page/{n}/`，Toronto 26 页 |
| JSON-LD | ✅ Event schema | 含 name/startDate/endDate/location |
| 反爬 | ⚠️ Cloudflare 防护 | 需 headless=False + user-agent 伪装 |
| 城市覆盖 | ⚠️ 部分城市数据少 | Montreal=0 events, Moncton=4 events |
| 详情页 HTML | ~120K 字符 | 预清洗后 ~15-25K |

### 页面结构

- 列表页: `https://www.todocanada.ca/city/{city}/events/`
- 分页: `https://www.todocanada.ca/city/{city}/events/page/{n}/`
- 详情页: `https://www.todocanada.ca/city/{city}/event/{slug}/`

### 城市路径映射

```python
TODOCANADA_CITY_SLUGS = {
    "toronto": "toronto",
    "vancouver": "vancouver",
    "calgary": "calgary",
    "edmonton": "edmonton",
    "ottawa": "ottawa",
    "winnipeg": "winnipeg",
    "montreal": "montreal",    # ⚠️ Spike 验证: 0 events
    "saskatoon": "saskatoon",
    "regina": "regina",
    "moncton": "moncton",      # ⚠️ Spike 验证: 仅 4 events
}
```

### 爬虫实现

```python
class TodoCanadaScraper(BaseScraper):
    BASE_URL = "https://www.todocanada.ca"

    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        slug = TODOCANADA_CITY_SLUGS[city_slug]
        list_url = f"{self.BASE_URL}/city/{slug}/events/"

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,  # Cloudflare 防护需要
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = await context.new_page()
            await page.goto(list_url, wait_until="domcontentloaded")

            # 收集详情页链接（Spike 验证: /city/{city}/event/{slug}/）
            detail_urls = await self._collect_links(
                page, rf"todocanada\.ca/city/{slug}/event/[^/]+/.*/?$"
            )

            pages = []
            for url in detail_urls:
                detail_page = await context.new_page()
                await detail_page.goto(url, wait_until="domcontentloaded")
                html = await detail_page.content()
                og_image = await self._get_og_image(detail_page)
                await detail_page.close()

                pages.append(RawPage(
                    source="todocanada",
                    source_url=url,
                    raw_html=html,
                    city_slug=city_slug,
                    image_url=og_image,
                ))

            await browser.close()
        return pages
```

### Spike 验证项

- [x] ~~确认列表页 URL pattern~~ → `/city/{city}/events/`
- [x] ~~确认详情页 URL pattern~~ → `/city/{city}/event/{slug}/`
- [x] ~~确认是否有分页~~ → WordPress 标准分页 `/city/{city}/events/page/{n}/`

---

## 数据源 2: FamilyFunCanada

**网址**: `https://www.familyfuncanada.com/`
**特点**: WordPress 博客，活动信息以城市指南/文章形式发布

### Spike 验证结果 (2026-05-07)

| 页面类型 | URL 示例 | 状态 | 说明 |
|----------|----------|------|------|
| 城市主页 | `/toronto/`、`/vancouver/`、`/saskatoon/` | ✅ 活跃 | 有 2026 年最新活动指南 |
| Events 日历 | `/toronto/events/` | ❌ 废弃 | 停留在 2021 年 1 月 |
| REST API | `/toronto/wp-json/tribe/events/v1/events` | ❌ 404 | 未注册 |
| 文章链接 | `/toronto/{slug}/` | ✅ 正确 | **不是** `/YYYY/MM/` pattern |
| 文章分页 | `/toronto/page/{n}/` | ✅ 可用 | |
| 文章内容 | 148K-165K 字符/页 | ⚠️ 较大 | 指南型文章为主，一页多活动 |

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
    "saskatoon": "saskatoon",
}
```

### 爬虫实现

```python
class FamilyFunCanadaScraper(BaseScraper):
    BASE_URL = "https://www.familyfuncanada.com"

    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        slug = FAMILYFUN_CITY_SLUGS.get(city_slug)
        if not slug:
            return []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # 收集城市主页上的文章链接
            # Spike 验证: 文章 URL 是 /{city}/{slug}/ 不是 /YYYY/MM/
            city_url = f"{self.BASE_URL}/{slug}/"
            await page.goto(city_url, wait_until="domcontentloaded")
            article_urls = await self._collect_links(
                page, rf"familyfuncanada\.com/{slug}/[^/]+/.*/$"
            )
            # 过滤掉非文章链接（分类、标签、日历等）
            skip_patterns = ["/category/", "/tag/", "/calendar/", "/page/"]
            article_urls = [
                u for u in article_urls
                if not any(p in u for p in skip_patterns)
            ]

            # 处理分页
            for page_num in range(2, 4):  # 抓前 3 页
                next_url = f"{self.BASE_URL}/{slug}/page/{page_num}/"
                resp = await page.goto(next_url, wait_until="domcontentloaded")
                if resp.status == 404:
                    break
                more = await self._collect_links(
                    page, rf"familyfuncanada\.com/{slug}/[^/]+/.*/$"
                )
                more = [u for u in more if not any(p in u for p in skip_patterns)]
                article_urls.extend(more)

            pages = []
            for url in set(article_urls):
                detail_page = await browser.new_page()
                await detail_page.goto(url, wait_until="domcontentloaded")
                html = await detail_page.content()
                og_image = await self._get_og_image(detail_page)
                await detail_page.close()

                pages.append(RawPage(
                    source="familyfuncanada",
                    source_url=url,
                    raw_html=html,
                    city_slug=city_slug,
                    image_url=og_image,
                ))

            await browser.close()
        return pages
```

### 注意事项

- 一篇文章可能包含多个活动（如"May Event Guide"），LLM 会处理这种情况
- 部分文章是"指南"型（如"Where Kids Eat Free"），LLM 质量评估会过滤

---

## 数据源 3: DiscoverSaskatoon

**网址**: `https://www.discoversaskatoon.com/calendar-events`
**特点**: Drupal 10，分页加载
**覆盖**: 仅 Saskatoon

### Spike 验证结果 (2026-05-07)

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 列表页 | ✅ `/calendar-events` | |
| 详情页 | ✅ `/calendar-events/{slug}` | 如 `/calendar-events/the-legendary-patsy-cline-show` |
| 分页 | ✅ `?page=N` 参数 | "Load More" 链接到 `?page=1`、`?page=2` ... |
| HTML 渲染 | 需 `domcontentloaded` | `networkidle` 会超时 |

### 爬虫实现

```python
class DiscoverSaskatoonScraper(BaseScraper):
    BASE_URL = "https://www.discoversaskatoon.com"
    SUPPORTED_CITIES = {"saskatoon"}

    async def fetch_pages(
        self, city_slug: str, start_date: datetime, end_date: datetime
    ) -> list[RawPage]:
        if city_slug not in self.SUPPORTED_CITIES:
            return []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(
                f"{self.BASE_URL}/calendar-events",
                wait_until="domcontentloaded",
            )

            # 分页加载（Spike 验证: ?page=N 参数，非按钮点击）
            all_detail_urls = set()
            for page_num in range(10):  # 最多 10 页
                url = f"{self.BASE_URL}/calendar-events?page={page_num}" if page_num > 0 else f"{self.BASE_URL}/calendar-events"
                await page.goto(url, wait_until="domcontentloaded")

                # Spike 验证: /calendar-events/{slug}
                detail_urls = await self._collect_links(
                    page, r"discoversaskatoon\.com/calendar-events/[^/]+$"
                )
                new_urls = set(detail_urls) - all_detail_urls
                if not new_urls:
                    break
                all_detail_urls.update(new_urls)

            pages = []
            for url in all_detail_urls:
                detail_page = await browser.new_page()
                await detail_page.goto(url, wait_until="domcontentloaded")
                html = await detail_page.content()
                og_image = await self._get_og_image(detail_page)
                await detail_page.close()

                pages.append(RawPage(
                    source="discoversaskatoon",
                    source_url=url,
                    raw_html=html,
                    city_slug=city_slug,
                    image_url=og_image,
                ))

            await browser.close()
        return pages
```

### Spike 验证项

- [x] ~~确认详情页 URL pattern~~ → `/calendar-events/{slug}`
- [x] ~~确认 "Load More" 行为~~ → `?page=N` 参数分页

---

## 爬虫注册 & 调度

```python
SCRAPERS: dict[str, type[BaseScraper]] = {
    "todocanada": TodoCanadaScraper,
    "familyfuncanada": FamilyFunCanadaScraper,
    "discoversaskatoon": DiscoverSaskatoonScraper,
}


async def fetch_all_pages(
    city_slug: str, start_date: datetime, end_date: datetime
) -> list[RawPage]:
    """从所有数据源抓取原始页面"""
    all_pages = []

    for name, scraper_cls in SCRAPERS.items():
        scraper = scraper_cls()
        if city_slug not in scraper.supported_cities:
            continue
        try:
            pages = await scraper.fetch_pages(city_slug, start_date, end_date)
            all_pages.extend(pages)
        except Exception as e:
            logger.error(f"Scraper {name} failed for {city_slug}: {e}")

    return all_pages
```

## 主流程

```python
async def process_city(city_slug: str, start_date: datetime, end_date: datetime):
    # 1. 爬虫抓取原始页面
    raw_pages = await fetch_all_pages(city_slug, start_date, end_date)

    for page in raw_pages:
        # 2. 页面缓存：内容未变化且上次成功则跳过
        html_hash = compute_html_hash(page.raw_html)
        cached = await db.get_processed_page(page.source, page.source_url)
        if cached and cached["html_hash"] == html_hash and cached["status"] in ("success", "empty"):
            continue

        # 3. LLM 提取 + 翻译 + 摘要（一页可产出多个活动）
        try:
            activities = await ai_engine.process(page)
        except Exception as e:
            await db.save_processed_page(page.source, page.source_url, html_hash, "failed")
            logger.error(f"LLM processing failed for {page.source_url}: {e}")
            continue

        for activity in activities:
            # 4. 跳过无开始时间的活动
            if not activity.start_time_utc:
                continue

            # 5. 跳过超出目标日期范围的活动
            if activity.start_time_utc > end_date:
                continue

            # 6. 事件级精确去重（source + source_id，UNIQUE 约束兜底）
            if await db.exists(source=activity.source, source_id=activity.source_id):
                continue

            # 7. 内容去重（标题+时间+地址 hash）
            activity.content_hash = compute_content_hash(activity)
            if await db.exists_content_hash(activity.city_slug, activity.content_hash):
                continue

            # 8. HTML 安全清理
            activity.html_zh = sanitize_html(activity.html_zh, page.source_url)

            # 9. 存储 + 发布
            await db.save(activity)
            try:
                await publisher.publish(activity)
            except Exception as e:
                logger.error(f"Publish failed for {activity.source_id}: {e}")
                await db.mark_publish_failed(activity.source, activity.source_id, str(e))
                continue

        # 记录页面处理状态
        await db.save_processed_page(
            page.source, page.source_url, html_hash,
            "success" if activities else "empty",
            activity_count=len(activities),
        )
```

## 图片处理

一期先用原始 URL 或 `og:image`，平台 `img` 字段支持外部 URL。后续可上传到腾讯云 COS。

## 调度策略

- **频率**: 每天凌晨 1 次全量抓取
- **范围**: 未来 30 天活动
- **城市**: 遍历所有 10 个城市
- **并行**: 不同城市的抓取可并行执行
- **去重**: 页面缓存（processed_pages + html_hash）+ 事件级 source_id 精确去重 + content_hash 内容去重

---

## Eventbrite 调查结论 — ❌ 不可用

**Eventbrite API 不能作为数据源**：

- **Public Event Search API 已关闭**: `/v3/events/search/` 端点于 2019 年 12 月关闭
- **仅剩 Organization/User Events API**: 只能查询自己组织的活动
- **验证结果** (2026-05-07):
  - `GET /v3/events/search/?location.address=Toronto` — **404 NOT_FOUND**

---

## Spike 验证清单

全部验证完成 (2026-05-07)。

### 通用验证（每个数据源）

| 检查项 | TodoCanada | DiscoverSaskatoon | FamilyFunCanada |
|--------|-----------|-------------------|-----------------|
| 列表页可访问 | ✅ `/city/{city}/events/` | ✅ `/calendar-events` | ✅ `/{city}/` |
| 详情页链接收集 | ✅ `/city/{city}/event/{slug}/` | ✅ `/calendar-events/{slug}` | ✅ `/{city}/{slug}/` |
| 反爬测试 | ⚠️ Cloudflare，需 headless=False | ✅ 无限制 | ✅ 无限制 |
| 分页/动态加载 | ✅ WordPress 分页 `page/{n}` | ✅ `?page=N` 参数 | ✅ `/{city}/page/{n}/` |

### 各源 Spike 结论

1. **TodoCanada** — Cloudflare 防护需特殊处理，10 城市中 Montreal 无数据、Moncton 仅 4 条
2. **DiscoverSaskatoon** — 分页使用 `?page=N` 参数（非按钮点击），需 `domcontentloaded` 避免超时
3. **FamilyFunCanada** — 文章 URL 是 `/{city}/{slug}/` 而非 `/YYYY/MM/`，内容以指南型文章为主

### 反爬策略

```python
PLAYWRIGHT_CONFIG = {
    "headless": True,
    "request_delay": (2, 5),
    "page_timeout": 30000,
    "max_retries": 3,
}
```
