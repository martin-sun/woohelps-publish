"""刷新全站已发文章台账（近 N 天）。

用法： uv run python scripts/refresh_published_index.py [--days 30]

原理： 平台文章 id 递增。 从台账记录的 max_id 之上探新文章，
再从 max_id 往下逐篇读 article/info/get， 收集 id、 标题、 更新时间、 作者，
直到连续 60 篇的更新时间都早于截止日为止。 结果写 data/published_index.json。
台账覆盖所有账号， 不只发布账号本人。
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.publisher.woohelps import WoohelpsPublisher

INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "published_index.json"


async def fetch_article(pub, article_id):
    try:
        resp = await pub.client.get(
            pub.base_url + "/api/applet/article/info/get",
            params={"article_id": article_id},
            headers={"LOGIN-SESSION": pub.login_session},
        )
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("title"):
        return None
    user = data.get("user") or {}
    return {
        "id": data["id"],
        "title": data["title"],
        "update_time": data.get("update_time") or 0,
        "user_id": user.get("id"),
        "user_name": user.get("name"),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp()

    old = {"articles": [], "max_id": 0}
    if INDEX_PATH.exists():
        old = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in old.get("articles", [])}
    max_id = int(old.get("max_id") or 0)

    settings = get_settings()
    async with WoohelpsPublisher(base_url=settings.WOOHELPS_API_URL, login_session=settings.WOOHELPS_LOGIN_SESSION) as pub:
        if max_id == 0:
            resp = await pub.client.get(
                pub.base_url + "/api/applet/own/articles/userid/get",
                params={"user_id": 1, "page": 1, "size": 1},
                headers={"LOGIN-SESSION": pub.login_session},
            )
            arts = resp.json().get("articles") or []
            max_id = int(arts[0]["id"]) if arts else 0
        probe = max_id + 1
        misses = 0
        while misses < 8:
            found = await fetch_article(pub, probe)
            if found is None:
                await asyncio.sleep(0.5)
                found = await fetch_article(pub, probe)
            if found:
                max_id = probe
                by_id[probe] = found
                misses = 0
            else:
                misses += 1
            probe += 1
        old_streak = 0
        scanned = 0
        aid = max_id
        while aid > 0 and old_streak < 60:
            cached = by_id.get(aid)
            if cached is not None and (cached.get("update_time") or 0) >= cutoff_ts:
                aid -= 1
                continue
            article = await fetch_article(pub, aid)
            scanned += 1
            if article is not None:
                by_id[aid] = article
                if (article.get("update_time") or 0) >= cutoff_ts:
                    old_streak = 0
                else:
                    old_streak += 1
            aid -= 1

    kept = [a for a in by_id.values() if (a.get("update_time") or 0) >= cutoff_ts]
    kept.sort(key=lambda a: a["id"], reverse=True)
    payload = {
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "max_id": max_id,
        "count": len(kept),
        "articles": kept,
    }
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "max_id": max_id, "kept": len(kept), "scanned": scanned}))


if __name__ == "__main__":
    asyncio.run(main())
