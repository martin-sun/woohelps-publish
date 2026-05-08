import hashlib
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import anthropic
from loguru import logger

from src.config.settings import CITIES, CITY_TIMEZONES
from src.models.activity import ProcessedActivity, RawPage

PROCESS_PROMPT = """你是一个专业的加拿大活动信息处理助手。你的任务是从网页 HTML 中提取活动信息，翻译为中文，并生成摘要。

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
3. 时间请根据城市时区解析为具体日期时间。如果只有日期没有时间，时间填 null
4. 地址尽量提取完整地址

## 输出格式

请以 JSON 格式输出：
{{
    "events": [
        {{
            "title_en": "English title",
            "title_zh": "中文标题",
            "description_zh": "中文摘要（200字以内，突出活动亮点）",
            "html_zh": "翻译后的中文 HTML 内容（保留 HTML 标签结构）",
            "start_date": "YYYY-MM-DD",
            "start_time": "HH:MM or null",
            "end_date": "YYYY-MM-DD",
            "end_time": "HH:MM or null",
            "address": "完整地址",
            "venue_name": "场地名称 or null",
            "price": "费用信息原文",
            "is_free": true/false,
            "image_url": "图片 URL or null",
            "highlights": ["亮点1", "亮点2"],
            "activity_type": 1,
            "suitable": true/false,
            "skip_reason": "不适合发布的原因 or null"
        }}
    ]
}}

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
2. 描述翻译要保留关键信息（时间、地点、费用、参与方式）
3. HTML 内容翻译时保持 HTML 标签结构不变
4. 摘要不超过 200 字，突出活动亮点
"""


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


def _generate_source_id(
    source_url: str, title_en: str,
    start_date: str | None, start_time: str | None, address: str,
) -> str:
    datetime_key = f"{start_date or ''}{'T' + start_time if start_time else ''}"
    key = f"{_normalize_source_text(title_en)}|{datetime_key}|{_normalize_source_text(address)}"
    suffix = hashlib.md5(key.encode()).hexdigest()[:8]
    return f"{source_url}#{suffix}"


def parse_llm_datetime(
    date_str: str | None, time_str: str | None, city_slug: str,
) -> datetime | None:
    """将 LLM 输出的日期+时间解析为 UTC naive datetime"""
    if not date_str:
        return None
    tz = ZoneInfo(CITY_TIMEZONES[city_slug])
    if time_str:
        local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    else:
        local_dt = datetime.strptime(date_str, "%Y-%m-%d")
    aware = local_dt.replace(tzinfo=tz)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


class AIEngine:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = anthropic.AsyncAnthropic(
            base_url=base_url,
            api_key=api_key,
        )
        self.model = model

    @classmethod
    def from_settings(cls, settings) -> "AIEngine":
        return cls(
            api_key=settings.KIMI_API_KEY,
            base_url=settings.KIMI_BASE_URL,
            model=settings.KIMI_MODEL,
        )

    async def process(self, raw_page: RawPage) -> list[ProcessedActivity]:
        """处理一个原始页面，返回提取到的活动列表"""
        city_name = CITIES[raw_page.city_slug]["eng_name"]
        clean_html = html_preclean(raw_page.raw_html)

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=8192,
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
        # 提取 JSON：优先提取 ```json ``` 代码块，回退到第一个完整 JSON 对象
        json_text = None
        code_block = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
        if code_block:
            json_text = code_block.group(1).strip()
        else:
            # 逐层匹配花括号，找到第一个有效的 JSON 对象
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
                        json_text = text[start:i + 1]
                        break

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
                html_zh=event["html_zh"],
                address=event.get("address", ""),
                venue_name=event.get("venue_name"),
                price=event.get("price"),
                is_free=event.get("is_free", True),
                image_url=event.get("image_url") or raw_page.image_url,
                image_urls=[event["image_url"]] if event.get("image_url") else ([raw_page.image_url] if raw_page.image_url else []),
                highlights=event.get("highlights", []),
                activity_type=event.get("activity_type", 1),
                start_time_utc=start_time_utc,
                end_time_utc=end_time_utc,
                timezone=CITY_TIMEZONES.get(raw_page.city_slug),
            ))

        return activities
