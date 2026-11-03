"""新闻发布通道：把一条新闻稿发布为平台文章，并核验前台可见性。

供每日新闻线使用。输入是一个 dict（见 scripts/publish_news.py 的 JSON 格式），
内部复用 WoohelpsPublisher 的图片上传与登录会话，发布走
POST /api/applet/article/release，分类用文章标签 id 关联。

分类 id 来自 GET /api/applet/tags/get?type=4 的实时清单（2026-11-03 核过）：
移民留学 66、房产资讯 816、理财金融 818、教育文化 819、生活百科 820、
商业就业 821、就业信息 3757。平台没有单独的「新闻」分类，新闻按题材进上述分类。

可见性约定：city_id=0 且 country_id=加拿大时文章在全国范围可见；
带 city_id 时只在该城市与其所属国家范围可见（后端 get_articles 的口径）。
发布后同步文本审核一般即时通过（moderation_status 默认 approved），
带图片时平台还有异步图片审核，核验以 info/get 返回的状态为准。
"""

import html as html_lib
import json

import httpx
from loguru import logger

from src.publisher.woohelps import WoohelpsPublisher

CANADA_COUNTRY_ID = 3  # 2026-11-03 经城市接口实测核对：3 为加拿大

CATEGORY_IDS: dict[str, int] = {
    "移民留学": 66,
    "房产资讯": 816,
    "理财金融": 818,
    "教育文化": 819,
    "生活百科": 820,
    "商业就业": 821,
    "就业信息": 3757,
}


def build_news_html(item: dict) -> str:
    """把结构化新闻稿拼成平台文章 html。

    item 字段：
    - paragraphs: 正文段落列表（纯文本，函数负责转义）
    - source_name / source_url: 来源名与原文链接，文末自动附来源行
    """
    parts: list[str] = []
    for para in item.get("paragraphs", []):
        text = html_lib.escape(str(para)).replace("\n", "<br>")
        parts.append(f"<p>{text}</p>")
    source_name = item.get("source_name")
    source_url = item.get("source_url")
    if source_name and source_url:
        parts.append(
            "<p>来源："
            f'<a href="{html_lib.escape(str(source_url), quote=True)}">'
            f"{html_lib.escape(str(source_name))}</a></p>"
        )
    elif source_name:
        parts.append(f"<p>来源：{html_lib.escape(str(source_name))}</p>")
    return "\n".join(parts)


def resolve_category_id(item: dict) -> int:
    """category 可以直接给 id，也可以给分类中文名。"""
    raw = item.get("category")
    if raw is None:
        raise ValueError("缺少 category（分类名或 id）")
    if isinstance(raw, int):
        return raw
    if str(raw).isdigit():
        return int(str(raw))
    if raw in CATEGORY_IDS:
        return CATEGORY_IDS[raw]
    raise ValueError(f"未知分类：{raw}，可用分类：{', '.join(CATEGORY_IDS)}")


async def publish_news(
    publisher: WoohelpsPublisher,
    item: dict,
    dry_run: bool = False,
) -> dict:
    """发布一条新闻文章。dry_run 时只组装并返回 payload，不真正提交。"""
    title = (item.get("title") or "").strip()
    if not title:
        raise ValueError("缺少 title")

    category_id = resolve_category_id(item)
    category_name = next(
        (name for name, cid in CATEGORY_IDS.items() if cid == category_id),
        str(category_id),
    )
    city_id = int(item.get("city_id") or 0)
    country_id = int(item.get("country_id") or CANADA_COUNTRY_ID)

    cover_url = ""
    cover_source = item.get("cover_image_url")
    if cover_source and not dry_run:
        uploaded = await publisher.upload_image(cover_source)
        if uploaded:
            cover_url = uploaded
        else:
            logger.warning(f"封面图上传失败，无封面发布：{cover_source}")

    body_html = item.get("html") or build_news_html(item)

    data = {
        "title": title,
        "content": "[]",
        "html": body_html,
        "img": cover_url,
        "imgs": json.dumps([cover_url] if cover_url else [], ensure_ascii=False),
        "tags": json.dumps(
            [{"id": category_id, "name": category_name}], ensure_ascii=False
        ),
        "country_id": country_id,
        "city_id": city_id,
    }

    if dry_run:
        return {"dry_run": True, "payload": data}

    headers = {"LOGIN-SESSION": publisher.login_session}
    resp = await publisher.client.post(
        f"{publisher.base_url}/api/applet/article/release",
        data=data,
        headers=headers,
    )
    result = resp.json()
    if result.get("errcode") != 0:
        return {"ok": False, "release_result": result}

    article_id = result.get("article_id")
    verification = await verify_article(publisher, article_id)
    return {
        "ok": True,
        "article_id": article_id,
        "category": category_name,
        "verification": verification,
    }


async def verify_article(publisher: WoohelpsPublisher, article_id) -> dict:
    """发布后核验：读文章详情，回报审核与展示状态。"""
    headers = {"LOGIN-SESSION": publisher.login_session}
    try:
        resp = await publisher.client.get(
            f"{publisher.base_url}/api/applet/article/info/get",
            params={"article_id": article_id},
            headers=headers,
        )
        info = resp.json()
    except Exception as e:
        return {"checked": False, "error": str(e)}
    article = info.get("article") if isinstance(info, dict) else None
    if not isinstance(article, dict):
        article = info if isinstance(info, dict) else {}
    return {
        "checked": True,
        "title": article.get("title"),
        "status": article.get("status"),
        "moderation_status": article.get("moderation_status"),
        "publication_status": article.get("publication_status"),
    }
