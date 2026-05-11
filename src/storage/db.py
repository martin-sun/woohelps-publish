import json
import os

import aiosqlite
from datetime import datetime

from src.models.activity import ProcessedActivity

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 来源信息
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    city_slug TEXT NOT NULL,

    -- 标题
    title_en TEXT NOT NULL,
    title_zh TEXT NOT NULL,

    -- 中文处理后的数据
    description_zh TEXT NOT NULL,
    content_zh TEXT NOT NULL DEFAULT '',

    -- 时间和地点
    start_time TEXT,                    -- UTC
    end_time TEXT,                      -- UTC
    timezone TEXT,                      -- IANA 时区
    address TEXT NOT NULL DEFAULT '',
    venue_name TEXT,

    -- 图片
    image_url TEXT,
    image_urls TEXT NOT NULL DEFAULT '[]',

    -- 活动属性
    price TEXT,
    is_free INTEGER NOT NULL DEFAULT 1,
    fee_amount REAL NOT NULL DEFAULT 0.0,
    fee_parsed_free INTEGER NOT NULL DEFAULT 1,
    activity_type INTEGER NOT NULL DEFAULT 1,

    -- AI 处理结果
    highlights TEXT NOT NULL DEFAULT '[]',

    -- 发布状态
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/published/failed/skipped
    platform_activity_id INTEGER,
    publish_error TEXT,

    -- 去重
    content_hash TEXT,

    -- 元数据
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS processed_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    html_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    activity_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(source, source_url)
);

CREATE TABLE IF NOT EXISTS scrape_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL DEFAULT 'discover',  -- discover/fetch_details/refilter
    city_slugs TEXT NOT NULL DEFAULT '[]',       -- JSON array, e.g. ["toronto", "vancouver"]
    status TEXT NOT NULL DEFAULT 'running',      -- running/completed/failed
    total_fetched INTEGER NOT NULL DEFAULT 0,
    total_new INTEGER NOT NULL DEFAULT 0,
    total_skipped INTEGER NOT NULL DEFAULT 0,
    current_city TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_activities_city ON activities(city_slug);
CREATE INDEX IF NOT EXISTS idx_activities_status ON activities(status);
CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_activities_content_hash ON activities(city_slug, content_hash);
CREATE INDEX IF NOT EXISTS idx_processed_pages_source ON processed_pages(source);

CREATE TABLE IF NOT EXISTS candidate_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_slug TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    title_zh TEXT DEFAULT '',
    event_date TEXT,
    address TEXT DEFAULT '',
    price TEXT DEFAULT '',
    description TEXT DEFAULT '',
    description_zh TEXT DEFAULT '',
    ai_worth_fetching INTEGER,
    ai_reason TEXT,
    human_status TEXT NOT NULL DEFAULT 'pending',
    fetched_detail INTEGER NOT NULL DEFAULT 0,
    activity_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_url)
);

CREATE INDEX IF NOT EXISTS idx_candidates_city ON candidate_activities(city_slug);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_activities(human_status);
CREATE INDEX IF NOT EXISTS idx_candidates_source ON candidate_activities(source);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_CREATE_TABLES)
        await self._db.commit()

        # 迁移：为已有表添加新列
        for col, ddl in [
            ("title_zh", "ALTER TABLE candidate_activities ADD COLUMN title_zh TEXT DEFAULT ''"),
            ("description_zh", "ALTER TABLE candidate_activities ADD COLUMN description_zh TEXT DEFAULT ''"),
        ]:
            try:
                await self._db.execute(ddl)
                await self._db.commit()
            except Exception:
                pass  # 列已存在

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # --- Activity CRUD ---

    async def save(self, activity: ProcessedActivity) -> int:
        cursor = await self._db.execute(
            """INSERT INTO activities (
                source, source_id, source_url, city_slug,
                title_en, title_zh, description_zh, content_zh,
                address, venue_name,
                price, is_free, fee_amount, fee_parsed_free,
                start_time, end_time, timezone,
                image_url, image_urls, highlights, activity_type, content_hash,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                title_en=excluded.title_en,
                title_zh=excluded.title_zh,
                description_zh=excluded.description_zh,
                content_zh=excluded.content_zh,
                address=excluded.address,
                venue_name=excluded.venue_name,
                price=excluded.price,
                is_free=excluded.is_free,
                fee_amount=excluded.fee_amount,
                fee_parsed_free=excluded.fee_parsed_free,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                image_url=excluded.image_url,
                image_urls=excluded.image_urls,
                highlights=excluded.highlights,
                activity_type=excluded.activity_type,
                content_hash=excluded.content_hash,
                updated_at=datetime('now')
            """,
            (
                activity.source, activity.source_id, activity.source_url, activity.city_slug,
                activity.title_en, activity.title_zh, activity.description_zh, activity.content_zh,
                activity.address, activity.venue_name,
                activity.price, int(activity.is_free), activity.fee_amount, int(activity.fee_parsed_free),
                activity.start_time_utc.isoformat() if activity.start_time_utc else None,
                activity.end_time_utc.isoformat() if activity.end_time_utc else None,
                activity.timezone,
                activity.image_url,
                json.dumps(activity.image_urls, ensure_ascii=False),
                json.dumps(activity.highlights, ensure_ascii=False),
                activity.activity_type,
                activity.content_hash,
                activity.status,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def exists(self, source: str, source_id: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM activities WHERE source = ? AND source_id = ?",
            (source, source_id),
        )
        return await cursor.fetchone() is not None

    async def exists_content_hash(self, city_slug: str, content_hash: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM activities WHERE city_slug = ? AND content_hash = ?",
            (city_slug, content_hash),
        )
        return await cursor.fetchone() is not None

    async def delete_activities(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        # 重置关联的 candidate 状态为待处理
        await self._db.execute(
            f"""UPDATE candidate_activities
                SET human_status = 'pending', fetched_detail = 0, activity_id = NULL
                WHERE activity_id IN ({placeholders})""",
            ids,
        )
        await self._db.execute(
            f"DELETE FROM activities WHERE id IN ({placeholders})", ids,
        )
        await self._db.commit()

    async def mark_published(self, source: str, source_id: str, platform_id: int):
        await self._db.execute(
            """UPDATE activities
               SET status = 'published', platform_activity_id = ?,
                   updated_at = datetime('now')
               WHERE source = ? AND source_id = ?""",
            (platform_id, source, source_id),
        )
        await self._db.commit()

    async def mark_publish_failed(self, source: str, source_id: str, error: str):
        await self._db.execute(
            """UPDATE activities
               SET status = 'failed', publish_error = ?, updated_at = datetime('now')
               WHERE source = ? AND source_id = ?""",
            (error, source, source_id),
        )
        await self._db.commit()

    async def mark_skipped(self, source: str, source_id: str):
        await self._db.execute(
            """UPDATE activities SET status = 'skipped', updated_at = datetime('now')
               WHERE source = ? AND source_id = ?""",
            (source, source_id),
        )
        await self._db.commit()

    # --- Processed Pages ---

    async def get_processed_page(self, source: str, source_url: str) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM processed_pages WHERE source = ? AND source_url = ?",
            (source, source_url),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def save_processed_page(
        self, source: str, source_url: str, html_hash: str,
        status: str, activity_count: int = 0,
    ):
        await self._db.execute(
            """INSERT INTO processed_pages (source, source_url, html_hash, status, activity_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, source_url) DO UPDATE SET
                html_hash=excluded.html_hash,
                status=excluded.status,
                activity_count=excluded.activity_count,
                updated_at=datetime('now')
            """,
            (source, source_url, html_hash, status, activity_count),
        )
        await self._db.commit()

    # --- Scrape Tasks ---

    async def create_scrape_task(self, city_slugs: list[str], task_type: str = "discover") -> int:
        cursor = await self._db.execute(
            """INSERT INTO scrape_tasks (task_type, city_slugs) VALUES (?, ?)""",
            (task_type, json.dumps(city_slugs)),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def create_task(
        self, task_type: str, city_slugs: list[str] | None = None, *, detail: str = "",
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO scrape_tasks (task_type, city_slugs, current_city) VALUES (?, ?, ?)""",
            (task_type, json.dumps(city_slugs or []), detail),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def update_scrape_task(
        self, task_id: int, *,
        status: str | None = None,
        total_fetched: int | None = None,
        total_new: int | None = None,
        total_skipped: int | None = None,
        current_city: str | None = None,
        error_message: str | None = None,
        completed_at: str | None = None,
    ):
        parts, args = [], []
        if status is not None:
            parts.append("status = ?")
            args.append(status)
        if total_fetched is not None:
            parts.append("total_fetched = ?")
            args.append(total_fetched)
        if total_new is not None:
            parts.append("total_new = ?")
            args.append(total_new)
        if total_skipped is not None:
            parts.append("total_skipped = ?")
            args.append(total_skipped)
        if current_city is not None:
            parts.append("current_city = ?")
            args.append(current_city)
        if error_message is not None:
            parts.append("error_message = ?")
            args.append(error_message)
        if completed_at is not None:
            parts.append("completed_at = ?")
            args.append(completed_at)
        if not parts:
            return
        args.append(task_id)
        await self._db.execute(
            f"""UPDATE scrape_tasks SET {', '.join(parts)} WHERE id = ?""",
            args,
        )
        await self._db.commit()

    async def complete_scrape_task(self, task_id: int):
        await self._db.execute(
            """UPDATE scrape_tasks SET status = 'completed', completed_at = datetime('now') WHERE id = ?""",
            (task_id,),
        )
        await self._db.commit()

    async def fail_scrape_task(self, task_id: int, error: str):
        await self._db.execute(
            """UPDATE scrape_tasks SET status = 'failed', error_message = ?, completed_at = datetime('now') WHERE id = ?""",
            (error, task_id),
        )
        await self._db.commit()

    async def delete_scrape_task(self, task_id: int):
        await self._db.execute("DELETE FROM scrape_tasks WHERE id = ?", (task_id,))
        await self._db.commit()

    async def clear_scrape_tasks(self):
        await self._db.execute("DELETE FROM scrape_tasks")
        await self._db.commit()

    async def get_scrape_task(self, task_id: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM scrape_tasks WHERE id = ?", (task_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_recent_scrape_tasks(self, limit: int = 20) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM scrape_tasks ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # --- Activity Queries (Admin UI) ---

    async def list_activities(
        self,
        city_slug: str | None = None,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions, args = [], []
        if city_slug:
            conditions.append("city_slug = ?")
            args.append(city_slug)
        if status:
            conditions.append("status = ?")
            args.append(status)
        if source:
            conditions.append("source = ?")
            args.append(source)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self._db.execute(
            f"SELECT * FROM activities {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count_activities(
        self,
        city_slug: str | None = None,
        status: str | None = None,
        source: str | None = None,
    ) -> int:
        conditions, args = [], []
        if city_slug:
            conditions.append("city_slug = ?")
            args.append(city_slug)
        if status:
            conditions.append("status = ?")
            args.append(status)
        if source:
            conditions.append("source = ?")
            args.append(source)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self._db.execute(
            f"SELECT COUNT(*) FROM activities {where}", args,
        )
        row = await cursor.fetchone()
        return row[0]

    async def get_activity(self, activity_id: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM activities WHERE id = ?", (activity_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def count_by_city_and_status(self) -> dict[str, dict]:
        cursor = await self._db.execute(
            "SELECT city_slug, status, COUNT(*) as cnt FROM activities GROUP BY city_slug, status",
        )
        rows = await cursor.fetchall()
        result: dict[str, dict] = {}
        for r in rows:
            city = r["city_slug"]
            if city not in result:
                result[city] = {"total": 0}
            result[city][r["status"]] = r["cnt"]
            result[city]["total"] += r["cnt"]
        return result

    async def get_activities_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        cursor = await self._db.execute(
            f"SELECT * FROM activities WHERE id IN ({placeholders})", ids,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # --- Candidate Activities ---

    async def save_candidates(self, candidates: list[dict]) -> int:
        """批量保存候选活动，返回插入/更新数量"""
        if not candidates:
            return 0
        rows = [
            (
                c["city_slug"], c["source"], c["source_url"], c.get("title", ""),
                c.get("title_zh", ""), c.get("event_date", ""), c.get("address", ""),
                c.get("price", ""), c.get("description", ""), c.get("description_zh", ""),
                c.get("ai_worth_fetching"), c.get("ai_reason", ""),
            )
            for c in candidates
        ]
        await self._db.executemany(
            """INSERT INTO candidate_activities (
                city_slug, source, source_url, title, title_zh, event_date,
                address, price, description, description_zh, ai_worth_fetching, ai_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_url) DO UPDATE SET
                title=excluded.title,
                title_zh=excluded.title_zh,
                event_date=excluded.event_date,
                address=excluded.address,
                price=excluded.price,
                description=excluded.description,
                description_zh=excluded.description_zh,
                ai_worth_fetching=excluded.ai_worth_fetching,
                ai_reason=excluded.ai_reason
            """,
            rows,
        )
        await self._db.commit()
        return len(rows)

    async def list_candidates(
        self,
        city_slug: str | None = None,
        ai_worth: bool | None = None,
        ai_failed: bool | None = None,
        human_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions, args = [], []
        if city_slug:
            conditions.append("city_slug = ?")
            args.append(city_slug)
        if ai_failed is True:
            conditions.append("ai_reason = 'AI 过滤失败，默认跳过'")
        elif ai_worth is not None:
            conditions.append("ai_worth_fetching = ?")
            args.append(1 if ai_worth else 0)
            conditions.append("(ai_reason IS NULL OR ai_reason != 'AI 过滤失败，默认跳过')")
        if human_status:
            conditions.append("human_status = ?")
            args.append(human_status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self._db.execute(
            f"SELECT * FROM candidate_activities {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count_candidates(
        self,
        city_slug: str | None = None,
        ai_worth: bool | None = None,
        ai_failed: bool | None = None,
        human_status: str | None = None,
    ) -> int:
        conditions, args = [], []
        if city_slug:
            conditions.append("city_slug = ?")
            args.append(city_slug)
        if ai_failed is True:
            conditions.append("ai_reason = 'AI 过滤失败，默认跳过'")
        elif ai_worth is not None:
            conditions.append("ai_worth_fetching = ?")
            args.append(1 if ai_worth else 0)
            conditions.append("(ai_reason IS NULL OR ai_reason != 'AI 过滤失败，默认跳过')")
        if human_status:
            conditions.append("human_status = ?")
            args.append(human_status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self._db.execute(
            f"SELECT COUNT(*) FROM candidate_activities {where}", args,
        )
        row = await cursor.fetchone()
        return row[0]

    async def update_candidate_status(self, ids: list[int], status: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        await self._db.execute(
            f"UPDATE candidate_activities SET human_status = ? WHERE id IN ({placeholders})",
            [status] + ids,
        )
        await self._db.commit()

    async def delete_candidates(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        await self._db.execute(
            f"DELETE FROM candidate_activities WHERE id IN ({placeholders})", ids,
        )
        await self._db.commit()

    async def mark_candidate_fetched(self, candidate_id: int, activity_id: int | None = None) -> None:
        await self._db.execute(
            """UPDATE candidate_activities
               SET fetched_detail = 1, activity_id = ? WHERE id = ?""",
            (activity_id, candidate_id),
        )
        await self._db.commit()

    async def get_candidates_to_fetch(
        self, city_slug: str | None = None,
    ) -> list[dict]:
        conditions = ["human_status = 'selected'", "fetched_detail = 0"]
        args = []
        if city_slug:
            conditions.append("city_slug = ?")
            args.append(city_slug)
        where = f"WHERE {' AND '.join(conditions)}"
        cursor = await self._db.execute(
            f"SELECT * FROM candidate_activities {where} ORDER BY id", args,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_candidates_to_refilter(self, city_slug: str | None = None) -> list[dict]:
        conditions = ["ai_reason = 'AI 过滤失败，默认跳过'"]
        args: list = []
        if city_slug:
            conditions.append("city_slug = ?")
            args.append(city_slug)
        where = f"WHERE {' AND '.join(conditions)}"
        cursor = await self._db.execute(
            f"SELECT * FROM candidate_activities {where} ORDER BY id", args,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_candidate_ai_result(self, candidate_id: int, worth: bool, reason: str, title_zh: str, description_zh: str) -> None:
        await self._db.execute(
            """UPDATE candidate_activities
               SET ai_worth_fetching = ?, ai_reason = ?, title_zh = ?, description_zh = ?
               WHERE id = ?""",
            (1 if worth else 0, reason, title_zh, description_zh, candidate_id),
        )
        await self._db.commit()

    async def get_candidate(self, candidate_id: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM candidate_activities WHERE id = ?", (candidate_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def count_candidates_by_city(self) -> dict[str, dict]:
        cursor = await self._db.execute(
            "SELECT city_slug, human_status, COUNT(*) as cnt FROM candidate_activities GROUP BY city_slug, human_status",
        )
        rows = await cursor.fetchall()
        result: dict[str, dict] = {}
        for r in rows:
            city = r["city_slug"]
            if city not in result:
                result[city] = {"total": 0}
            result[city][r["human_status"]] = r["cnt"]
            result[city]["total"] += r["cnt"]
        return result
