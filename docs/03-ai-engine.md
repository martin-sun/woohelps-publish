# AI 处理引擎设计

## 概述

AI 引擎承担两个任务：
1. **预过滤**（`filter_activities`）：基于列表页摘要批量判断活动是否值得抓取详情页
2. **详情处理**（`process`）：接收详情页原始 HTML，一步完成提取结构化数据 + 翻译为中文 + 生成摘要 + 质量评估

两个任务使用不同的 Prompt，同一模型（kimi-k2.7），通过 Kimi API（月之暗面，Anthropic 兼容协议）调用。

## AI 模型

| 任务 | 模型 | 说明 |
|------|------|------|
| 预过滤 | kimi-k2.7 | 批量判断摘要是否值得抓详情 |
| 提取 + 翻译 + 摘要 + 质量评估 | kimi-k2.7 | 详情页处理，一次调用完成 |

> 使用 Kimi Coding Plan，通过 Anthropic 兼容协议调用，费用极低。

## 核心设计：Filter Prompt（列表页预过滤）

`filter_activities()` 方法用于在阶段1（Discover）中批量过滤列表页摘要，只保留值得抓取详情页的活动。

### 输入

- `city_slug`: 城市标识（用于获取城市英文名）
- `summaries`: 活动摘要列表，每条包含 `title`/`date`/`address`/`price`/`description`

### 输出

返回 `worth_fetching=true` 的摘要子集。LLM 原始输出格式：

```json
{
    "results": [
        {"index": 1, "worth_fetching": true, "reason": "社区文化节"},
        {"index": 2, "worth_fetching": false, "reason": "商业促销"}
    ]
}
```

### Prompt 设计

```python
FILTER_PROMPT = """你是一位活动筛选助手。请根据以下活动摘要，判断每个活动是否值得抓取详细页面信息。

我们只对以下类型的活动感兴趣（标记 YES）：
- 文化演出（戏剧、音乐会、舞蹈、艺术展览）
- 户外活动（徒步、骑行、跑步、自然探索）
- 节日庆典和社区活动
- 适合家庭/儿童的活动
- 教育类活动（讲座、工作坊）
- 体育赛事、比赛

以下活动不适合（标记 NO）：
- 纯商业促销、打折、招聘广告
- 仅限特定小群体（如仅限某学校/公司内部）
- 宗教布道类活动
- 重复出现的日常课程/例会（如每周固定的瑜伽课）
- 信息严重不完整（无标题、无时间）
- 明显不是活动信息（如导航页、静态介绍页）

城市: {city_name}

活动列表：
{items}

请严格按以下 JSON 格式输出，只输出 JSON，不要有其他内容：
{{
    "results": [
        {{"index": 1, "worth_fetching": true/false, "reason": "简要原因"}},
        {{"index": 2, "worth_fetching": true/false, "reason": "简要原因"}}
    ]
}}
"""
```

### 容错行为

- JSON 解析失败或 LLM 返回无法解析的内容时，返回全部原始摘要（不丢弃任何数据）
- 单次调用处理一个城市的全部摘要，批量判断

## 核心设计：Process Prompt（详情页处理）

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

### filter_activities（阶段1: 列表页预过滤）

```
活动摘要列表 (title/date/address/price/description)
    │
    └── LLM Filter (kimi-k2.7, 批量判断)
           ├── worth_fetching=true  → 保留，存入 candidate_activities
           └── worth_fetching=false → 标记原因，不入库或入库供参考
```

### process（阶段2: 详情页处理）

```
RawPage (原始 HTML)
    │
    ├── HTML 预清洗 (本地裁剪)
    │
    └── LLM Process (kimi-k2.7, 一次调用)
           ├── 提取活动信息（支持一页多活动）
           ├── 翻译为中文
           ├── 生成摘要
           ├── 分类 (activity_type)
           └── 质量评估 (suitable)
                │
                ├── suitable=false → 跳过
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
class AIEngine:
    def __init__(self, api_key: str, base_url: str, model: str, max_tokens: int = 8192):
        self.client = anthropic.AsyncAnthropic(base_url=base_url, api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    @classmethod
    def from_settings(cls, settings) -> "AIEngine":
        return cls(
            api_key=settings.KIMI_API_KEY,
            base_url=settings.KIMI_BASE_URL,
            model=settings.KIMI_MODEL,
            max_tokens=settings.KIMI_MAX_TOKENS,
        )

    async def filter_activities(self, city_slug: str, summaries: list[dict]) -> list[dict]:
        """根据列表页摘要批量过滤活动，返回 worth_fetching=true 的条目。
        失败时返回全部原始摘要（不丢弃数据）。"""

    async def process(self, raw_page: RawPage) -> list[ProcessedActivity]:
        """处理一个详情页，返回提取到的活动列表（仅 suitable=true 的）。"""
```

### 两个方法的区别

| 维度 | `filter_activities()` | `process()` |
|------|----------------------|-------------|
| 阶段 | 阶段1 Discover | 阶段2 Fetch Details |
| 输入 | 城市slug + 摘要列表 | RawPage（详情页 HTML） |
| 输出 | 过滤后的摘要子集 | ProcessedActivity 列表 |
| 用途 | 批量判断是否值得抓详情 | 提取完整结构化数据 + 翻译 |
| Token 消耗 | 低（只有摘要文本） | 高（含 HTML 页面） |
| 容错 | 失败返回全部摘要 | 失败返回空列表 |

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
KIMI_MODEL=kimi-k2.7
```

## 成本估算

以每月 1000 个活动页面计算（每个页面可能包含 1-5 个活动），使用 Kimi Coding Plan：

| 任务 | 模型 | 单次 Token 估算 | 说明 |
|------|------|----------------|------|
| 提取+翻译+摘要+评估（一次完成） | kimi-k2.7 | ~3000 in + 2000 out（预清洗后） | Coding Plan 套餐内，无需额外计费 |

> 统一 Prompt 比拆分多次调用更省 token：上下文只传一次 HTML，输出一次性完成。质量优先场景下可考虑拆分为"提取+评估"和"翻译+摘要"两次调用，以获得更精准的结果。

## 批量处理策略

1. **预过滤批量处理**: `filter_activities()` 一次调用处理一个城市的全部摘要，减少 API 调用次数
2. **并行处理**: 不同城市的页面可以并行发送给 LLM
3. **缓存**: 相同 URL 的结果缓存，避免重复处理
4. **页面缓存**: 通过 processed_pages 表的 html_hash 检测页面变化，避免重复调用 LLM
