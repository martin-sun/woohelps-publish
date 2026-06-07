import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
from loguru import logger

from src.config.settings import CITIES, CITY_TIMEZONES
from src.models.activity import ProcessedActivity, RawPage
from src.models.property import PropertyCandidate, Property

EXTRACT_LIST_PROMPT = """你是一个专业的网页内容提取助手。请从以下活动列表页的 HTML 中提取所有活动条目的摘要信息。

注意：
1. 这是活动列表页，包含多个活动的摘要/卡片/条目
2. 每个活动通常会有标题、日期、详情链接(URL)、简短描述
3. 请提取所有能找到的活动条目，不要遗漏
4. URL 必须是完整的绝对路径（如 https://example.com/event/xxx）
5. 如果页面中没有活动信息，返回空列表

城市: {city_name}
来源: {source}
页面 URL: {page_url}

页面 HTML:
{raw_html}

请严格按以下 JSON 格式输出，只输出 JSON，不要有其他内容：
{{
    "events": [
        {{
            "url": "活动详情页的完整 URL",
            "title": "活动标题（原文）",
            "date": "活动日期/时间信息（原文）",
            "start_date": "YYYY-MM-DD（活动开始日期，必须从原文中提取，不确定时填 null）",
            "address": "活动地址（如有，原文）",
            "price": "费用信息（如有，原文）",
            "description": "活动简短描述（原文，200字以内）"
        }}
    ]
}}
"""

FILTER_PROMPT = """你是一位活动筛选助手。请根据以下活动摘要，判断每个活动是否值得抓取详细页面信息。

我们主要对以下类型的活动感兴趣（标记 YES）：
- 免费的公益活动（社区集市、文化节、免费演出、公园活动）
- 免费或低价的户外活动（徒步、骑行、自然探索、免费导览）
- 免费或低价的家庭/儿童活动
- 免费或低价的教育类活动（公开讲座、免费工作坊）
- 免费或低价的节日庆典
- 小额付费活动（门票 $20 以内，或有明显折扣/早鸟价）

以下活动不适合（标记 NO）：
- 高价活动（门票 $20 以上，除非是重大演出/赛事）
- 纯商业促销、打折、招聘广告
- 仅限特定小群体（如仅限某学校/公司内部）
- 宗教布道类活动
- 重复出现的日常课程/例会（如每周固定的瑜伽课）
- 需要长期承诺的活动（如多周课程、会员制活动）
- 信息严重不完整（无标题、无时间）
- 明显不是活动信息（如导航页、静态介绍页）

判断原则：优先免费公益活动，小额付费也可以。不确定时标 NO。

城市: {city_name}

活动列表：
{items}

请严格按以下 JSON 格式输出，只输出 JSON，不要有其他内容：
{{
    "results": [
        {{"index": 1, "worth_fetching": true/false, "reason": "简要原因", "title_zh": "中文标题", "description_zh": "中文摘要（一句话概括活动内容）"}},
        {{"index": 2, "worth_fetching": true/false, "reason": "简要原因", "title_zh": "中文标题", "description_zh": "中文摘要（一句话概括活动内容）"}}
    ]
}}
"""

PROCESS_PROMPT = """你是一个专业的加拿大活动信息处理助手。你的任务是从网页 HTML 中提取活动信息，翻译为中文，并生成适合手机端阅读的纯文本内容。

## 输入

城市: {city_name}
来源: {source}
页面 URL: {source_url}

页面 HTML:
{raw_html}

## 任务

请从上面的 HTML 中提取所有活动信息。注意：
1. 一个页面可能包含多个活动（如活动指南类文章），请逐个提取
2. 如果页面不是活动信息（如导航页、广告页），返回空列表
3. 时间必须从页面中精确提取，把 "June 15, 2025 from 10am to 4pm" 这类自然语言解析为 start_date/start_time/end_date/end_time
4. 如果只有日期没有具体时间，时间填 null
5. 如果只有开始时间没有结束时间，end_time 填 null
6. 地址必须提取完整地址（含街道号、街名、城市、省份、邮编），与 venue_name 区分
7. 从页面中提取所有活动相关图片 URL（不止封面图）

## 输出格式

请以 JSON 格式输出：
{{
    "events": [
        {{
            "title_en": "English title",
            "title_zh": "中文标题",
            "description_zh": "中文摘要（200字以内，一句话概括活动核心内容）",
            "content_zh": "中文纯文本正文（500-1000字，详见下方正文要求）",
            "start_date": "YYYY-MM-DD",
            "start_time": "HH:MM or null",
            "end_date": "YYYY-MM-DD",
            "end_time": "HH:MM or null",
            "address": "完整地址（英文原文，含邮编）",
            "venue_name": "场地名称 or null",
            "price": "费用信息原文",
            "is_free": true/false,
            "image_url": "封面图片 URL",
            "image_urls": ["图片1 URL", "图片2 URL"],
            "highlights": ["亮点1", "亮点2"],
            "activity_type": 1,
            "suitable": true/false,
            "skip_reason": "不适合发布的原因 or null"
        }}
    ]
}}

## 正文 (content_zh) 写作要求

content_zh 是给用户在手机端阅读的活动详情，必须是纯文本，禁止 HTML 标签。

结构建议（按实际情况调整）：
- 第一段：活动简介（2-3句话说明活动是什么、有什么特色）
- 活动亮点：用"•"或"-"列出 2-5 个核心亮点
- 时间和地点：写明具体时间、地址
- 费用信息：写明是否免费，如有票价列出具体金额
- 参与方式：报名链接/方式、是否需要提前注册
- 其他注意事项：停车、交通、适合人群等

写作风格：
- 面向加拿大华人社区，语气亲切自然
- 信息准确完整，保留关键细节（具体时间、价格、网址等）
- 地址、人名、机构名保持英文原文
- 不要使用"该活动"、"本次活动"等生硬表达
- 不要加"总结"、"结语"等段落

## 活动类型说明

activity_type 取值：
1 - 活动（一般活动、社交、文化、体育等）
2 - 招聘
3 - 促销
4 - 美食
5 - 教育
6 - 参政

## 质量评估标准

以下情况标记 suitable=false：
- 纯商业广告/促销
- 仅限特定群体（如仅限某公司员工）
- 内容不当或违法
- 信息严重不完整（无标题、无任何时间信息）
- 不是活动信息（如导航页、静态页面）

## 翻译要求

1. 标题翻译要简洁有力
2. description_zh 不超过 200 字，突出活动亮点
3. content_zh 500-1000 字，信息完整，适合手机阅读
4. 地址、人名、地名、机构名保持英文原文，不要翻译
5. address 和 venue_name 字段保持英文原文，不要翻译
"""

# 过滤批大小：每次发给 AI 的活动数量
FILTER_BATCH_SIZE = 25

# 活动日期窗口：只保留未来 N 天内的活动
DATE_WINDOW_DAYS = 30


def filter_by_date(summaries: list[dict]) -> list[dict]:
    """过滤掉 start_date 超出未来 DATE_WINDOW_DAYS 天的活动。"""
    cutoff = datetime.now(timezone.utc).date() + timedelta(days=DATE_WINDOW_DAYS)
    kept = []
    for s in summaries:
        raw = s.get("start_date")
        if not raw or raw == "null":
            kept.append(s)  # 无日期的保留，交给后续 AI 过滤判断
            continue
        try:
            event_date = datetime.strptime(raw, "%Y-%m-%d").date()
            if event_date <= cutoff:
                kept.append(s)
            else:
                logger.debug(f"Skipped future event: {s.get('title', '')} ({raw})")
        except ValueError:
            kept.append(s)  # 解析失败的保留
    return kept


def html_preclean(html: str, max_chars: int = 30000) -> str:
    """预清洗 HTML：移除噪音标签，只保留正文和 JSON-LD 结构化数据。"""

    json_ld_blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    )

    noise_tags = [
        r'<script[^>]*>.*?</script>',
        r'<style[^>]*>.*?</style>',
        r'<nav[^>]*>.*?</nav>',
        r'<footer[^>]*>.*?</footer>',
        r'<aside[^>]*>.*?</aside>',
        r'<noscript[^>]*>.*?</noscript>',
        r'<svg[^>]*>.*?</svg>',
        r'<iframe[^>]*>.*?</iframe>',
    ]
    for pattern in noise_tags:
        html = re.sub(pattern, '', html, flags=re.DOTALL)

    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

    if json_ld_blocks:
        json_ld_section = '\n'.join(
            f'<script type="application/ld+json">{block}</script>'
            for block in json_ld_blocks
        )
        html = json_ld_section + "\n" + html

    return html[:max_chars]


def _normalize_source_text(text: str) -> str:
    return re.sub(r'\s+', '', text.lower().strip())


def _repair_json(text: str) -> str | None:
    """尝试修复 LLM 输出的常见 JSON 畸形：尾逗号、value 间缺逗号"""
    # 去掉数组/对象末尾的尾逗号: ,]  ,}
    fixed = re.sub(r',\s*([}\]])', r'\1', text)
    # 在 "key": value 后面紧跟 { 或 [ 或 " 时补逗号（value 之间缺分隔）
    fixed = re.sub(r'(["\d}\]])(\s+)({|"|\[)', r'\1,\2\3', fixed)
    try:
        json.loads(fixed)
        return fixed
    except json.JSONDecodeError:
        return None


def _extract_json(text: str) -> str | None:
    """从 LLM 响应中提取 JSON 字符串"""
    code_block = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if code_block:
        candidate = code_block.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            repaired = _repair_json(candidate)
            if repaired:
                return repaired

    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start:i + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    repaired = _repair_json(candidate)
                    if repaired:
                        return repaired
                    start = None
    return None


def _generate_source_id(
    source_url: str, title_en: str,
    start_date: str | None, start_time: str | None, address: str,
) -> str:
    datetime_key = f"{start_date or ''}{'T' + start_time if start_time else ''}"
    key = f"{_normalize_source_text(title_en)}|{datetime_key}|{_normalize_source_text(address)}"
    suffix = hashlib.md5(key.encode()).hexdigest()[:16]
    return f"{source_url}#{suffix}"


def parse_llm_datetime(
    date_str: str | None, time_str: str | None, city_slug: str,
) -> datetime | None:
    """将 LLM 输出的日期+时间解析为 UTC naive datetime"""
    if not date_str:
        return None
    try:
        tz = ZoneInfo(CITY_TIMEZONES[city_slug])
        if time_str:
            local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        else:
            local_dt = datetime.strptime(date_str, "%Y-%m-%d")
        aware = local_dt.replace(tzinfo=tz)
        return aware.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, KeyError) as e:
        logger.warning(f"Failed to parse LLM datetime: date={date_str}, time={time_str}, city={city_slug}: {e}")
        return None


class AIEngine:
    def __init__(self, api_key: str, base_url: str, model: str, max_tokens: int = 8192):
        self.client = anthropic.AsyncAnthropic(
            base_url=base_url,
            api_key=api_key,
        )
        self.model = model
        self.max_tokens = max_tokens
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings) -> "AIEngine":
        return cls(
            api_key=settings.KIMI_API_KEY,
            base_url=settings.KIMI_BASE_URL,
            model=settings.KIMI_MODEL,
            max_tokens=settings.KIMI_MAX_TOKENS,
        )

    async def extract_list_events(
        self, raw_html: str, city_slug: str, source: str, page_url: str,
    ) -> list[dict]:
        """从列表页 HTML 中用 LLM 提取活动摘要，替代 CSS 选择器"""
        city_name = CITIES[city_slug]["eng_name"]
        clean_html = html_preclean(raw_html, max_chars=20000)

        async with self._lock:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{
                    "role": "user",
                    "content": EXTRACT_LIST_PROMPT.format(
                        city_name=city_name,
                        source=source,
                        page_url=page_url,
                        raw_html=clean_html,
                    ),
                }],
            )

        text = response.content[0].text
        json_text = _extract_json(text)
        if not json_text:
            logger.warning(f"No JSON found in list extraction for {page_url}")
            return []

        result = json.loads(json_text)
        events = result.get("events", [])
        logger.info(f"LLM extracted {len(events)} events from {page_url}")
        return events

    async def filter_activities(
        self, city_slug: str, summaries: list[dict],
    ) -> list[dict]:
        """根据列表页摘要批量过滤活动，分批调用 AI 避免响应截断"""
        if not summaries:
            return []

        city_name = CITIES[city_slug]["eng_name"]

        # 分批处理
        for batch_start in range(0, len(summaries), FILTER_BATCH_SIZE):
            batch = summaries[batch_start:batch_start + FILTER_BATCH_SIZE]
            items = []
            for i, s in enumerate(batch, 1):
                items.append(
                    f"{i}. 标题: {s.get('title', '')}\n"
                    f"   日期: {s.get('date', '')}\n"
                    f"   地址: {s.get('address', '')}\n"
                    f"   价格: {s.get('price', '')}\n"
                    f"   描述: {s.get('description', '')[:200]}"
                )

            try:
                async with self._lock:
                    response = await self.client.messages.create(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        messages=[{
                            "role": "user",
                            "content": FILTER_PROMPT.format(
                                city_name=city_name,
                                items="\n\n".join(items),
                            ),
                        }],
                    )
                text = response.content[0].text
                json_text = _extract_json(text)
                if not json_text:
                    logger.warning(f"No JSON in filter response for {city_slug} batch {batch_start}")
                    for s in batch:
                        s.setdefault("worth_fetching", False)
                        s.setdefault("reason", "AI 过滤响应解析失败")
                    continue

                result = json.loads(json_text)
                results = result.get("results", [])

                for r in results:
                    idx = r.get("index", 0) - 1
                    if 0 <= idx < len(batch):
                        batch[idx]["worth_fetching"] = r.get("worth_fetching", False)
                        batch[idx]["reason"] = r.get("reason", "")
                        batch[idx]["title_zh"] = r.get("title_zh", "")
                        batch[idx]["description_zh"] = r.get("description_zh", "")

                worth_in_batch = sum(1 for s in batch if s.get("worth_fetching"))
                logger.info(
                    f"AI filter batch {batch_start // FILTER_BATCH_SIZE + 1}: "
                    f"{len(batch)} -> {worth_in_batch} worth for {city_slug}"
                )

            except Exception as e:
                logger.error(f"AI filter batch failed for {city_slug} batch {batch_start}: {e}")
                for s in batch:
                    s.setdefault("worth_fetching", False)
                    s.setdefault("reason", "AI 过滤失败，默认跳过")

        worth_count = sum(1 for s in summaries if s.get("worth_fetching"))
        skipped = len(summaries) - worth_count
        logger.info(f"AI filter total: {len(summaries)} -> {worth_count} worth fetching (skipped {skipped}) for {city_slug}")
        return summaries

    async def process(self, raw_page: RawPage) -> list[ProcessedActivity]:
        """处理一个原始页面，返回提取到的活动列表"""
        city_name = CITIES[raw_page.city_slug]["eng_name"]
        clean_html = html_preclean(raw_page.raw_html)

        async with self._lock:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{
                    "role": "user",
                    "content": PROCESS_PROMPT.format(
                        city_name=city_name,
                        source=raw_page.source,
                        source_url=raw_page.source_url,
                        raw_html=clean_html,
                    ),
                }],
            )

        text = response.content[0].text
        json_text = _extract_json(text)
        if not json_text:
            logger.warning(f"No JSON found in LLM response for {raw_page.source_url}")
            return []

        result = json.loads(json_text)
        events = result.get("events", [])
        activities = []

        for event in events:
            if not event.get("suitable", True):
                continue

            source_id = _generate_source_id(
                raw_page.source_url, event["title_en"],
                event.get("start_date"), event.get("start_time"),
                event.get("address", ""),
            )

            start_time_utc = parse_llm_datetime(
                event.get("start_date"), event.get("start_time"), raw_page.city_slug,
            )
            end_time_utc = parse_llm_datetime(
                event.get("end_date"), event.get("end_time"), raw_page.city_slug,
            )

            if not end_time_utc and start_time_utc:
                tz = ZoneInfo(CITY_TIMEZONES[raw_page.city_slug])
                local_start = start_time_utc.replace(tzinfo=timezone.utc).astimezone(tz)
                local_end = local_start.replace(hour=23, minute=59, second=0, microsecond=0)
                end_time_utc = local_end.astimezone(timezone.utc).replace(tzinfo=None)

            activities.append(ProcessedActivity(
                source=raw_page.source,
                source_id=source_id,
                source_url=raw_page.source_url,
                city_slug=raw_page.city_slug,
                title_en=event["title_en"],
                title_zh=event["title_zh"],
                description_zh=event["description_zh"],
                content_zh=event.get("content_zh", ""),
                address=event.get("address", ""),
                venue_name=event.get("venue_name"),
                price=event.get("price"),
                is_free=event.get("is_free", True),
                image_url=event.get("image_url") or raw_page.image_url,
                image_urls=event.get("image_urls") or ([event["image_url"]] if event.get("image_url") else ([raw_page.image_url] if raw_page.image_url else [])),
                highlights=event.get("highlights", []),
                activity_type=event.get("activity_type", 1),
                start_time_utc=start_time_utc,
                end_time_utc=end_time_utc,
                timezone=CITY_TIMEZONES.get(raw_page.city_slug),
            ))

        return activities


# ── 房产翻译/润色 Prompt ──

RESIDENTIAL_PROPERTY_PROMPT = """你是一位专业的加拿大房产文案翻译和润色专家。你的任务是将英文房源信息翻译为地道的中文，生成一段流畅的房产介绍，适合华人买家阅读。

## 输入数据

城市: {city}

房源信息:
{listing_json}

## raw_data 字段说明

`raw_data` 是从 Realtor.ca 详情页提取的完整结构化数据，包含以下区块：
- `summary`: Property Summary（房产类型、楼层、面积、社区、建造年份、地税等）
- `building`: Building（浴室数、电器、地下室类型、建筑风格、供暖方式等）
- `measurements`: Measurements（详细尺寸）
- `rooms`: Rooms（房间列表，含楼层、类型、尺寸）
- `land`: Land（地块特征、围栏、临街面等）

请优先从 `raw_data` 各区块中提取信息，生成更准确、更丰富的中文内容。如果 `raw_data` 为空，则仅使用基础字段。

## 任务

1. **title_zh**：简洁有力的中文标题，包含城市名、社区名、房型、卧室/浴室数
2. **description_zh**：200字以内的中文摘要，突出房源核心卖点（用于列表页展示）
3. **content_zh**：一段流畅的中文房源介绍（500-1000字），信息自然融入叙述中。请包含以下信息：
   - 位置与社区环境
   - 价格
   - 室内面积（sqft，可标注近似平方米 1 sqft ≈ 0.093 m²）
   - 建造年份（如有）
   - 卧室/卫浴数量
   - 年度地税（如有）
   - 主要卖点和亮点
   - 适合人群

   写作原则：
   - **只陈述原始数据中提供的事实**，不添加推测或假设
   - **禁止夸大用语**：不使用"稀缺"、"绝版"、"升值潜力巨大"、"错过不再有"等营销词汇
   - **禁止市场预测**：不推测房价走势、租金涨幅等
   - **不要提及 MLS 编号**
   - **不要提及挂牌时间或上市天数**
   - 语气亲切自然、信息完整、重点突出，像一位诚实的房产经纪在向客户介绍房源
   - 写成一段或多段流畅的叙述文字，不要分块罗列或使用 bullet list

4. **highlights**：5-8个核心亮点短语（中文），每个短语简短有力
5. **year_built**：优先从 `raw_data.summary.built_in` 提取建造年份，如果不存在则从描述中提取，仍未找到则留空字符串
6. 地址、人名、机构名、MLS号码保持英文原文，不要翻译
7. 面积单位保持原文（sqft），可在括号内标注近似平方米

## 输出格式

请严格按以下 JSON 格式输出，只输出 JSON，不要有其他内容:
{{
    "title_zh": "中文标题",
    "description_zh": "中文摘要（200字以内）",
    "content_zh": "一段流畅的中文房源介绍...",
    "highlights": ["亮点1", "亮点2", "亮点3"],
    "year_built": "1988",
    "suitable": true/false,
    "suitable_reason": "如果不适合，说明原因；如果适合，写'信息完整，描述积极'"
}}

## 质量评估标准

请从**内容质量**角度判断是否适合发布，标记 suitable=false 的情况：
- 房源描述质量极差（如全是乱码、无意义重复、或明显虚假信息）
- 正文内容过于空洞，无法让买家了解房源实际状况
- 地址或房源信息存在明显矛盾
"""

COMMERCIAL_PROPERTY_PROMPT = """你是一位加拿大商业地产信息编辑。你的任务是将英文商业房产信息翻译为客观、准确的中文介绍，帮助华人读者了解物业的基本情况。

## 输入数据

城市: {city}

商业房产信息:
{listing_json}

## raw_data 字段说明

`raw_data` 是从 Realtor.ca 详情页提取的完整结构化数据，包含以下区块：
- `summary`: Property Summary（房产类型、楼层、面积、社区、建造年份、地税等）
- `building`: Building（浴室数、电器、地下室类型、建筑风格、供暖方式等）
- `measurements`: Measurements（详细尺寸）
- `rooms`: Rooms（房间列表，含楼层、类型、尺寸）
- `land`: Land（地块特征、围栏、临街面等）

请优先从 `raw_data` 各区块中提取信息，生成更准确、更丰富的中文内容。如果 `raw_data` 为空，则仅使用基础字段。

## 任务

1. **title_zh**：中文标题，包含城市名、房产类型和核心特点，客观陈述，不使用夸大词汇
2. **description_zh**：150字以内的摘要，概括物业的基本情况和主要特点
3. **content_zh**：一段客观的中文物业介绍（500-1000字），信息自然融入叙述中。请包含以下内容：
   - 位置与交通情况
   - 价格
   - 建筑面积与占地面积（sqft，可标注近似平方米 1 sqft ≈ 0.093 m²）
   - 建造年份（如有）
   - 物业类型与用途
   - 主要设施与设备配置
   - 现有租赁情况（如有，仅陈述原文信息，不做推测）
   - 年度地税与运营成本（如有）
   - 经纪联系方式

   写作原则：
   - **只陈述原始数据中提供的事实**，不添加任何推测、预估或假设
   - **禁止编造数据**：如入住率、资本化率、投资回报率、砍价空间、未来收益等，除非原始信息中明确提到
   - **禁止夸大用语**：不使用"黄金标的"、"稀缺机会"、"稳定现金流"、"投资良机"、"躺着赚钱"等营销词汇
   - **禁止市场预测**：不推测房价走势、租金涨幅、区域发展潜力等
   - **不要提及 MLS 编号**
   - **不要提及挂牌时间或上市天数**
   - 语气客观平实，像一份尽职调查报告的摘要，而非推销文案
   - 写成一段或多段流畅的文字，不要分块罗列

4. **highlights**：5-8个核心特点短语（中文），每个短语简短有力，只陈述事实。例如"24间客房汽车旅馆"、"1980年建造"、"屋顶与窗户已更新"、"附带业主生活区"等
5. **year_built**：优先从 `raw_data.summary.built_in` 提取建造年份，如果不存在则从描述中提取，仍未找到则留空字符串
6. 地址、人名、机构名、MLS号码保持英文原文，不要翻译
7. 面积单位保持原文（sqft），可在括号内标注近似平方米

## 输出格式

请严格按以下 JSON 格式输出，只输出 JSON，不要有其他内容:
{{
    "title_zh": "中文标题",
    "description_zh": "中文摘要（150字以内）",
    "content_zh": "一段客观的中文物业介绍...",
    "highlights": ["亮点1", "亮点2", "亮点3"],
    "year_built": "1988",
    "suitable": true/false,
    "suitable_reason": "如果不适合，说明原因；如果适合，写'信息完整，描述积极'"
}}

## 质量评估标准

请从**内容质量**角度判断是否适合发布，标记 suitable=false 的情况：
- 房源描述质量极差（如全是乱码、无意义重复、或明显虚假信息）
- 正文内容过于空洞，无法让买家了解房源实际状况
- 地址或房源信息存在明显矛盾
- 正文包含明显的夸大或推测性内容（如编造投资回报数据）
"""

COMMERCIAL_PROPERTY_TYPES = [
    "Commercial", "Business", "Retail", "Hospitality", "Industrial",
    "Office", "Mixed Use", "Mixed", "Shopping Center", "Plaza",
    "Strip Mall", "Warehouse", "Storefront",
]


# ── 房产处理方法 ──

async def process_property(
    ai_engine: "AIEngine",
    candidate: PropertyCandidate,
    description_en: str,
    agent_info: dict | None = None,
) -> Property | None:
    """调用 LLM 翻译/润色房源信息，返回 Property 对象"""

    # 构建 LLM 输入（MLS 编号不传入，避免出现在 content_zh 中）
    city_name = CITIES.get(candidate.city_slug, {}).get("eng_name", candidate.city_slug.title())
    listing_input = {
        "price": candidate.price,
        "price_numeric": candidate.price_numeric,
        "address": candidate.address,
        "property_type": candidate.property_type,
        "bedrooms": candidate.bedrooms,
        "bathrooms": candidate.bathrooms,
        "open_house": candidate.open_house,
        "description_en": description_en,
        "photo_count": len(candidate.photo_urls),
        "latitude": candidate.latitude,
        "longitude": candidate.longitude,
        "agents": [agent_info] if agent_info else [],
        "raw_data": candidate.raw_data or {},
    }

    # 根据房产类型选择 prompt：商业房产用投资分析风格，住宅用房源介绍风格
    is_commercial = candidate.property_type in COMMERCIAL_PROPERTY_TYPES
    prompt_template = COMMERCIAL_PROPERTY_PROMPT if is_commercial else RESIDENTIAL_PROPERTY_PROMPT

    async with ai_engine._lock:
        response = await ai_engine.client.messages.create(
            model=ai_engine.model,
            max_tokens=ai_engine.max_tokens,
            messages=[{
                "role": "user",
                "content": prompt_template.format(
                    city=city_name,
                    listing_json=json.dumps(listing_input, ensure_ascii=False, indent=2),
                ),
            }],
        )

    text = response.content[0].text
    json_text = _extract_json(text)
    if not json_text:
        logger.warning(f"No JSON found in property processing for {candidate.source_id}")
        return None

    result = json.loads(json_text)

    # 质量门禁：只保留 LLM 的内容质量判断
    if not result.get("suitable", True):
        logger.info(f"LLM rejected {candidate.source_id}: {result.get('suitable_reason', '')}")
        return None

    logger.info(f"[DEBUG] property {candidate.source_id}: photos={len(candidate.photo_urls)}, "
                f"desc_len={len(description_en or '')}, price={candidate.price_numeric}, "
                f"type={candidate.property_type}, suitable={result.get('suitable')}")

    # 构建 content_hash
    content = f"{result['title_zh']}|{result['content_zh']}|{candidate.address}"
    content_hash = hashlib.md5(content.encode()).hexdigest()

    return Property(
        source=candidate.source,
        source_id=candidate.source_id,
        source_url=candidate.source_url,
        city_slug=candidate.city_slug,
        agent_id=candidate.agent_id,
        title_en=candidate.title or candidate.address or "",
        title_zh=result["title_zh"],
        price=candidate.price or "",
        price_numeric=candidate.price_numeric,
        mls_number=candidate.mls_number,
        property_type=candidate.property_type,
        bedrooms=candidate.bedrooms,
        bathrooms=candidate.bathrooms,
        address=candidate.address or "",
        postal_code=candidate.postal_code,
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        description_zh=result.get("description_zh"),
        content_zh=result.get("content_zh", ""),
        highlights=result.get("highlights", []),
        open_house=candidate.open_house,
        image_urls=candidate.photo_urls,
        agent_name=agent_info.get("name") if agent_info else None,
        agent_brokerage=agent_info.get("brokerage") if agent_info else None,
        agent_phone=agent_info.get("phone") if agent_info else None,
        status="pending",
        content_hash=content_hash,
        raw_data=candidate.raw_data or {},
    )
