"""在已发台账里按关键词查同题文章。

用法： uv run python scripts/search_published.py 热泵 [关键词2 ...] [--days 30]

标题命中任一关键词即列出（id、 日期、 标题、 作者）， 按更新时间倒序。
挑选题目前先刷新台账， 再用主题词查： 计划名、 项目名、 关键金额、 机构名。
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "published_index.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keywords", nargs="+")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    if not INDEX_PATH.exists():
        print("NO INDEX: run scripts/refresh_published_index.py first")
        sys.exit(1)
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp()
    hits = []
    for article in payload.get("articles", []):
        title = article.get("title") or ""
        matched = [k for k in args.keywords if k in title]
        ts = article.get("update_time") or 0
        if matched and ts >= cutoff_ts:
            hits.append((ts, len(matched), article))
    hits.sort(key=lambda x: x[0], reverse=True)
    if not hits:
        print("NO MATCH")
        return
    tz = ZoneInfo("America/Regina")
    for ts, _n, article in hits:
        date = datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")
        print(article["id"], date, article["title"], "| author:", article.get("user_name"))


if __name__ == "__main__":
    main()
