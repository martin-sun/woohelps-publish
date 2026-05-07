# AI 处理引擎设计

## 概述

AI 引擎负责将英文活动信息转化为适合海外新生活平台的中文内容。使用 Kimi API（月之暗面，Anthropic 兼容协议）实现以下功能：

1. **翻译**: 英文标题、描述翻译为中文
2. **摘要生成**: 从活动描述中提取关键信息，生成简洁中文摘要
3. **分类**: 将活动分类到平台对应的类型（一期仅本地存储，不发布到平台；若需生效须先改后端 `release_activity` 接口）
4. **质量评估**: 评估活动是否适合平台发布

## AI 模型选择

| 任务 | 模型 | 说明 |
|------|------|------|
| 翻译 + 摘要 | kimi-k2.6 | 性价比高，翻译质量好 |
| 分类 | kimi-k2.6 | 统一模型，简化调用逻辑 |
| 质量评估 | kimi-k2.6 | 统一模型，简化调用逻辑 |

> 使用 Kimi Coding Plan，通过 Anthropic 兼容协议调用，费用极低。

## Prompt 设计

### 翻译 + 摘要 Prompt

```python
TRANSLATE_PROMPT = """你是一个专业的英中翻译，专门为加拿大华人社区平台翻译活动信息。

请将以下英文活动信息翻译为中文，并生成一个简洁的活动摘要。

要求：
1. 标题翻译要简洁有力，适合作为活动标题
2. 描述翻译要保留关键信息（时间、地点、费用、参与方式等）
3. HTML 内容翻译时保持 HTML 标签结构不变
4. 摘要不超过 200 字，突出活动亮点

输入活动信息：
标题: {title_en}
描述: {description_en}
HTML内容: {html_en}
地址: {address}
时间: {start_time} - {end_time}
费用: {price_info}

请以 JSON 格式输出：
{{
    "title_zh": "中文标题",
    "description_zh": "中文摘要（200字以内）",
    "html_zh": "翻译后的HTML内容",
    "highlights": ["亮点1", "亮点2"]
}}
"""
```

### 活动分类 Prompt

```python
CLASSIFY_PROMPT = """根据以下活动信息，判断活动类型。

平台活动类型：
1 - 活动（一般活动、社交、文化、体育等）
2 - 招聘
3 - 促销
4 - 美食
5 - 教育
6 - 参政

活动标题: {title}
活动描述: {description}
活动分类信息: {category_info}

请只返回一个数字（1-6），表示最适合的活动类型。
"""
```

### 质量过滤 Prompt

```python
QUALITY_PROMPT = """评估以下活动是否适合发布到加拿大华人社区平台。

不适合发布的活动：
- 纯商业广告/促销
- 仅限特定群体（如仅限某公司员工）
- 内容不当或违法
- 信息严重不完整

活动标题: {title}
活动描述: {description}

请返回 JSON:
{{"suitable": true/false, "reason": "原因"}}
"""
```

## 批量处理策略

为了减少 API 调用次数和成本：

1. **批量翻译**: 一次请求翻译多个活动（最多 10 个），减少 API 调用
2. **缓存**: 相同内容的翻译结果缓存到本地，避免重复调用
3. **并行处理**: 不同城市的活动可以并行处理

## 处理流程

```
RawActivity (英文)
    │
    ├── 1. 质量评估 (kimi-k2.6)
    │      └── 不适合 → 标记为 skipped
    │
    ├── 2. 翻译 + 摘要 (kimi-k2.6)
    │      ├── title_zh
    │      ├── description_zh
    │      └── html_zh
    │
    ├── 3. HTML 清理 (本地)
    │      └── html_zh (sanitized)
    │
    ├── 4. 分类 (kimi-k2.6)
    │      └── type (1-6)
    │
    └── ProcessedActivity (中文, 可发布)
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
    """清理 HTML 内容，移除不安全元素并添加来源标注。

    1. 移除 <script>, <iframe>, <object>, <embed> 等标签
    2. 移除所有 on* 事件属性
    3. 只保留白名单标签和属性
    4. 外链添加 target="_blank" rel="noopener"
    5. 在末尾添加来源标注（经二次 bleach.clean 确保安全）
    """
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )

    # 外链安全处理 — bleach linkify callback 需返回修改后的 attrs dict
    def _set_link_attrs(attrs, new=False):
        attrs[(None, "target")] = "_blank"
        attrs[(None, "rel")] = "noopener noreferrer"
        return attrs

    cleaned = bleach.linkify(
        cleaned,
        callbacks=[_set_link_attrs],
        skip_tags=["pre", "code"],
    )

    # 添加来源标注 — 对 URL 做 HTML 转义，整个标注再过一次 clean 防注入
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

### 依赖

```bash
pip install bleach
```

## API 配置

使用 Kimi Coding Plan，通过 Anthropic 兼容协议调用：

```python
# 环境变量
KIMI_BASE_URL=https://api.kimi.com/coding/
KIMI_API_KEY=your-api-key
KIMI_MODEL=kimi-k2.6
```

```python
import anthropic

client = anthropic.Anthropic(
    base_url="https://api.kimi.com/coding/",
    api_key="your-kimi-api-key",
)
```

## 成本估算

以每月 1000 个活动计算，使用 Kimi Coding Plan：

| 任务 | 模型 | 单次 Token 估算 | 月成本估算 |
|------|------|----------------|-----------|
| 翻译+摘要 | kimi-k2.6 | ~2000 in + 1000 out | ~$2/月 |
| 分类 | kimi-k2.6 | ~500 in + 50 out | ~$0.2/月 |
| 质量评估 | kimi-k2.6 | ~300 in + 50 out | ~$0.1/月 |
| **合计** | | | **~$3/月** |

> Kimi Coding Plan 费用远低于 Claude，适合本项目规模。使用批量处理可进一步降低成本。
