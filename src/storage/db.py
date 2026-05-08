import json

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

    -- 英文原始数据
    title_en TEXT NOT NULL,
    description_en TEXT,
    html_en TEXT,

    -- 中文处理后的数据
    title_zh TEXT NOT NULL,
    description_zh TEXT NOT NULL,
    html_zh TEXT NOT NULL,

    -- 时间和地点
    start_time TEXT,                    -- UTC
    end_time TEXT,                      -- UTC
    start_time_local TEXT,              -- 城市本地时间
    end_time_local TEXT,                -- 城市本地时间
    timezone TEXT,                      -- IANA 时区
    address TEXT NOT NULL DEFAULT '',
    venue_name TEXT,
    latitude REAL,
    longitude REAL,

    -- 图片
    image_url TEXT,
    local_image_url TEXT,
    image_urls TEXT NOT NULL DEFAULT '[]',

    -- 活动属性
    price TEXT,
    is_free INTEGER NOT NULL DEFAULT 1,
    fee_amount REAL NOT NULL DEFAULT 0.0,
    fee_parsed_free INTEGER NOT NULL DEFAULT 1,
    activity_type INTEGER NOT NULL DEFAULT 1,

    -- AI 处理结果
    highlights TEXT NOT NULL DEFAULT '[]',
    ai_highlights TEXT,                 -- JSON array (alias, for future use)
    quality_score REAL,
    is_suitable INTEGER NOT NULL DEFAULT 1,

    -- 发布状态
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/published/failed/skipped
    woohelps_activity_id INTEGER,
    publish_time TEXT,
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

CREATE TABLE IF NOT EXISTS scrape_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    city_slug TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    total_fetched INTEGER NOT NULL DEFAULT 0,
    total_new INTEGER NOT NULL DEFAULT 0,
    total_skipped INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_activities_city ON activities(city_slug);
CREATE INDEX IF NOT EXISTS idx_activities_status ON activities(status);
CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_activities_content_hash ON activities(city_slug, content_hash);
CREATE INDEX IF NOT EXISTS idx_processed_pages_source ON processed_pages(source);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_CREATE_TABLES)
        await self._db.commit()

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
                title_en, description_zh, html_zh,
                address, venue_name,
                price, is_free, fee_amount, fee_parsed_free,
                start_time, end_time, timezone,
                image_url, image_urls, highlights, activity_type, content_hash,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                title_zh=excluded.title_zh,
                description_zh=excluded.description_zh,
                html_zh=excluded.html_zh,
                updated_at=datetime('now')
            """,
            (
                activity.source, activity.source_id, activity.source_url, activity.city_slug,
                activity.title_en, activity.description_zh, activity.html_zh,
                activity.address, activity.venue_name,
                activity.price, int(activity.is_free), activity.fee_amount, int(activity.fee_parsed_free),
                activity.start_time_utc.isoformat() if activity.start_time_utc else None,
                activity.end_time_utc.isoformat() if activity.end_time_utc else None,
                activity.timezone,
                activity.image_url,
                json.dumps(activity.image_urls),
                json.dumps(activity.highlights),
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

    async def mark_published(self, source: str, source_id: str, platform_id: int):
        await self._db.execute(
            """UPDATE activities
               SET status = 'published', woohelps_activity_id = ?,
                   publish_time = datetime('now'), updated_at = datetime('now')
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

    # --- Scrape Logs ---

    async def save_scrape_log(
        self, source: str, city_slug: str,
        start_date: str, end_date: str,
        total_fetched: int = 0, total_new: int = 0, total_skipped: int = 0,
        status: str = "success", error_message: str | None = None,
    ):
        await self._db.execute(
            """INSERT INTO scrape_logs
               (source, city_slug, start_date, end_date, total_fetched, total_new, total_skipped, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source, city_slug, start_date, end_date, total_fetched, total_new, total_skipped, status, error_message),
        )
        await self._db.commit()
