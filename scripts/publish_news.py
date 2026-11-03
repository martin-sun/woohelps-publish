"""新闻发布命令行：读一个 JSON 文件，发布为平台文章并核验可见性。

用法：
    uv run python scripts/publish_news.py --input news_item.json [--dry-run]

JSON 字段：
    title            文章标题（必填）
    category         分类中文名或 id（必填），如 移民留学 / 房产资讯 / 理财金融 /
                     教育文化 / 生活百科 / 商业就业
    paragraphs       正文段落列表（纯文本），与 html 二选一
    html             直接给文章 html 时用这个字段
    source_name      来源名，如 CBC News
    source_url       原文链接，文末自动附来源行
    cover_image_url  封面图外链，可选，发布前自动转存到平台存储
    city_id          城市 id，可选，0 或不填为全国可见
    country_id       国家 id，可选，默认 1（加拿大）

输出：一行 JSON。真实发布时含 article_id 与核验状态。--dry-run 只打印将提交的 payload。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.publisher.news import publish_news
from src.publisher.woohelps import WoohelpsPublisher


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    item = json.loads(Path(args.input).read_text(encoding="utf-8"))

    async with WoohelpsPublisher(
        base_url=get_settings().WOOHELPS_API_URL,
        login_session=get_settings().WOOHELPS_LOGIN_SESSION,
    ) as publisher:
        result = await publish_news(publisher, item, dry_run=args.dry_run)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
