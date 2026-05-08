# AI 处理引擎设计

## 概述

AI 引擎接收爬虫抓取的**原始 HTML 页面**，一步完成：提取结构化数据 + 翻译为中文 + 生成摘要 + 质量评估。所有数据源使用同一个 Prompt，无需针对不同网站做适配。

使用 Kimi API（月之暗面，Anthropic 兼容协议）。

## AI 模型

| 任务 | 模型 | 说明 |
|------|------|------|
| 提取 + 翻译 + 摘要 + 质量评估 | kimi-k2.6 | 统一模型，一次调用完成 |

> 使用 Kimi Coding Plan，通过 Anthropic 兼容协议调用，费用极低。

## 核心设计：统一 Process Prompt

所有数据源（TodoCanada、FamilyFunCanada、DiscoverSaskatoon）的原始 HTML 都进入同一个 Prompt。LLM 负责：

1. 从 HTML 中识别并提取活动信息（标题、时间、地点、费用等）
2. 翻译为中文
3. 生成中文摘要
4. 评估是否适合发布
5. 一篇文章可能包含多个活动，逐个提取

```python
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
```

## 处理流程

```
RawPage (原始 HTML)
    │
    └── LLM Process (kimi-k2.6, 一次调用)
           ├── 提取活动信息（支持一页多活动）
           ├── 翻译为中文
           ├── 生成摘要
           ├── 分类 (activity_type)
           └── 质量评估 (suitable)
                │
                ├── suitable=false → 标记为 skipped
                │
                └── suitable=true
                     │
                     ├── HTML 安全清理 (本地 bleach)
                     │
                     └── ProcessedActivity (中文, 可发布)
```

## HTML 预清洗

真实 WordPress/Drupal 页面通常 50KB+，包含大量导航、侧边栏、页脚、脚本、广告等噪音。直接送入 LLM 既浪费 token 又影响提取质量。在调用 LLM 前预清洗 HTML，只保留正文区域和结构化数据。

```python
import re

def html_preclean(html: str, max_chars: int = 30000) -> str:
    """预清洗 HTML：移除噪音标签，只保留正文和 JSON-LD 结构化数据。"""

    # 1. 提取 JSON-LD（活动结构化数据常在此，如 Event schema）
    json_ld_blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    )

    # 2. 移除噪音标签（整体移除，包括内容）
    noise_tags = [
        r'<script[^>]*>.*?</script>',      # JS 脚本
        r'<style[^>]*>.*?</style>',         # CSS 样式
        r'<nav[^>]*>.*?</nav>',             # 导航
        r'<footer[^>]*>.*?</footer>',       # 页脚
        r'<aside[^>]*>.*?</aside>',         # 侧边栏
        r'<noscript[^>]*>.*?</noscript>',   # noscript
        r'<svg[^>]*>.*?</svg>',             # SVG 图标
        r'<iframe[^>]*>.*?</iframe>',       # 嵌入 iframe
    ]
    for pattern in noise_tags:
        html = re.sub(pattern, '', html, flags=re.DOTALL)

    # 3. 移除 HTML 注释
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

    # 4. 重新注入 JSON-LD
    if json_ld_blocks:
        json_ld_section = '\n'.join(
            f'<script type="application/ld+json">{block}</script>'
            for block in json_ld_blocks
        )
        html = json_ld_section + "\n" + html

    return html[:max_chars]
```

> 预清洗通常可将 HTML 从 50-80KB 缩减到 15-25KB，对应 token 从 ~12,000+ 降到 ~3,000-5,000。

## API 调用封装

```python
import anthropic
import hashlib
import json
import re


def _normalize_source_text(text: str) -> str:
    """归一化文本用于 source_id 计算：小写、去空白"""
    return re.sub(r'\s+', '', text.lower().strip())


def _generate_source_id(source_url: str, title_en: str, start_date: str | None, start_time: str | None, address: str) -> str:
    """基于内容的确定性 source_id，不依赖 LLM 输出顺序。

    格式: source_url#<md5(title|datetime|address)[:8]>
    包含 start_time 以区分同一天同地点的多场次活动。
    """
    datetime_key = f"{start_date or ''}{'T' + start_time if start_time else ''}"
    key = f"{_normalize_source_text(title_en)}|{datetime_key}|{_normalize_source_text(address)}"
    suffix = hashlib.md5(key.encode()).hexdigest()[:8]
    return f"{source_url}#{suffix}"

class AIEngine:
    def __init__(self, settings):
        self.client = anthropic.AsyncAnthropic(
            base_url=settings.KIMI_BASE_URL,
            api_key=settings.KIMI_API_KEY,
        )
        self.model = settings.KIMI_MODEL

    async def process(self, raw_page: RawPage) -> list[ProcessedActivity]:
        """处理一个原始页面，返回提取到的活动列表"""
        city_name = CITIES[raw_page.city_slug]["eng_name"]

        # 预清洗 HTML，减少 token 开销和噪音
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

        result = json.loads(response.content[0].text)
        events = result.get("events", [])
        activities = []

        for idx, event in enumerate(events):
            if not event.get("suitable", True):
                continue

            # 事件级 source_id：基于内容的确定性 ID（不依赖 LLM 输出顺序）
            source_id = _generate_source_id(
                raw_page.source_url, event["title_en"],
                event.get("start_date"), event.get("start_time"),
                event.get("address", ""),
            )

            # 解析时间字段为 UTC
            start_time_utc = parse_llm_datetime(
                event.get("start_date"), event.get("start_time"), raw_page.city_slug,
            )
            end_time_utc = parse_llm_datetime(
                event.get("end_date"), event.get("end_time"), raw_page.city_slug,
            )

            # 无结束时间时回退为当天 23:59 本地时间
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
                highlights=event.get("highlights", []),
                activity_type=event.get("activity_type", 1),
                start_time_utc=start_time_utc,
                end_time_utc=end_time_utc,
            ))

        return activities
```

## HTML 清理

平台审核只检查 `name`、`description`、图片，**不审核 `html` 字段**（参考 `reference/overseas-new-life/overseas_api/src/apps/content/views/activity.py:522-529`）。因此必须在发布前对 AI 翻译后的 HTML 做安全清理。

### 清理规则

```python
import html as html_mod
import bleach

ALLOWED_TAGS = [
    "p", "br", "b", "strong", "i", "em", "u",
    "h1", "h2", "h3", "h4",
    "ul", "ol", "li",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "blockquote",
]

ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_html(html: str, source_url: str = "") -> str:
    """清理 HTML 内容，移除不安全元素并添加来源标注。"""
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )

    def _set_link_attrs(attrs, new=False):
        attrs[(None, "target")] = "_blank"
        attrs[(None, "rel")] = "noopener noreferrer"
        return attrs

    cleaned = bleach.linkify(
        cleaned,
        callbacks=[_set_link_attrs],
        skip_tags=["pre", "code"],
    )

    if source_url:
        safe_url = html_mod.escape(source_url, quote=True)
        attribution = (
            f'<p style="color:#999;font-size:12px;">'
            f'来源: <a href="{safe_url}" target="_blank" rel="noopener">原文链接</a>'
            f'</p>'
        )
        attribution = bleach.clean(
            attribution,
            tags=["p", "a"],
            attributes={"a": ["href", "target", "rel"], "p": ["style"]},
            protocols=ALLOWED_PROTOCOLS,
        )
        cleaned += attribution

    return cleaned
```

## 时区处理

LLM 输出的日期时间是城市本地时间。处理流程：

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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

def parse_llm_datetime(date_str: str | None, time_str: str | None, city_slug: str) -> datetime | None:
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
```

## API 配置

```python
# 环境变量
KIMI_BASE_URL=https://api.kimi.com/coding/
KIMI_API_KEY=your-api-key
KIMI_MODEL=kimi-k2.6
```

## 成本估算

以每月 1000 个活动页面计算（每个页面可能包含 1-5 个活动），使用 Kimi Coding Plan：

| 任务 | 模型 | 单次 Token 估算 | 说明 |
|------|------|----------------|------|
| 提取+翻译+摘要+评估（一次完成） | kimi-k2.6 | ~3000 in + 2000 out（预清洗后） | Coding Plan 套餐内，无需额外计费 |

> 统一 Prompt 比拆分多次调用更省 token：上下文只传一次 HTML，输出一次性完成。质量优先场景下可考虑拆分为"提取+评估"和"翻译+摘要"两次调用，以获得更精准的结果。

## 批量处理策略

1. **并行处理**: 不同城市的页面可以并行发送给 LLM
2. **缓存**: 相同 URL 的结果缓存，避免重复处理
3. **页面缓存**: 通过 processed_pages 表的 html_hash 检测页面变化，避免重复调用 LLM
