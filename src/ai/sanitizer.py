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
