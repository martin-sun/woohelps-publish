import json

import asyncpg

from src.models.activity import ProcessedActivity

_CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS activities (
        id SERIAL PRIMARY KEY,

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
        start_time TEXT,
        end_time TEXT,
        timezone TEXT,
        address TEXT NOT NULL DEFAULT '',
        venue_name TEXT,

        -- 图片
        image_url TEXT,
        image_urls TEXT NOT NULL DEFAULT '[]',

        -- 活动属性
        price TEXT,
        is_free BOOLEAN NOT NULL DEFAULT TRUE,
        fee_amount DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        fee_parsed_free BOOLEAN NOT NULL DEFAULT TRUE,
        activity_type INTEGER NOT NULL DEFAULT 1,

        -- AI 处理结果
        highlights TEXT NOT NULL DEFAULT '[]',

        -- 发布状态
        status TEXT NOT NULL DEFAULT 'pending',
        platform_activity_id INTEGER,
        publish_error TEXT,

        -- 去重
        content_hash TEXT,

        -- 元数据
        created_at TEXT NOT NULL DEFAULT NOW(),
        updated_at TEXT NOT NULL DEFAULT NOW(),

        UNIQUE(source, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_pages (
        id SERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        source_url TEXT NOT NULL,
        html_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        activity_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT NOW(),
        updated_at TEXT NOT NULL DEFAULT NOW(),

        UNIQUE(source, source_url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scrape_tasks (
        id SERIAL PRIMARY KEY,
        task_type TEXT NOT NULL DEFAULT 'discover',
        city_slugs TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'running',
        total_fetched INTEGER NOT NULL DEFAULT 0,
        total_new INTEGER NOT NULL DEFAULT 0,
        total_skipped INTEGER NOT NULL DEFAULT 0,
        current_city TEXT,
        error_message TEXT,
        started_at TEXT NOT NULL DEFAULT NOW(),
        completed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_activities_city ON activities(city_slug)",
    "CREATE INDEX IF NOT EXISTS idx_activities_status ON activities(status)",
    "CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities(start_time)",
    "CREATE INDEX IF NOT EXISTS idx_activities_content_hash ON activities(city_slug, content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_processed_pages_source ON processed_pages(source)",
    """
    CREATE TABLE IF NOT EXISTS candidate_activities (
        id SERIAL PRIMARY KEY,
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
        ai_worth_fetching BOOLEAN,
        ai_reason TEXT,
        human_status TEXT NOT NULL DEFAULT 'pending',
        fetched_detail BOOLEAN NOT NULL DEFAULT FALSE,
        activity_id INTEGER,
        created_at TEXT NOT NULL DEFAULT NOW(),
        UNIQUE(source, source_url)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_candidates_city ON candidate_activities(city_slug)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_activities(human_status)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_source ON candidate_activities(source)",
]


class Database:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def init(self):
        self._pool = await asyncpg.create_pool(self.database_url, min_size=2, max_size=10, statement_cache_size=0)
        async with self._pool.acquire() as conn:
            for ddl in _CREATE_TABLES:
                await conn.execute(ddl)
            # 迁移：为已有表添加新列
            for col, ddl in [
                ("title_zh", "ALTER TABLE candidate_activities ADD COLUMN title_zh TEXT DEFAULT ''"),
                ("description_zh", "ALTER TABLE candidate_activities ADD COLUMN description_zh TEXT DEFAULT ''"),
            ]:
                try:
                    await conn.execute(ddl)
                except asyncpg.DuplicateColumnError:
                    pass

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # --- Activity CRUD ---

    async def save(self, activity: ProcessedActivity) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO activities (
                    source, source_id, source_url, city_slug,
                    title_en, title_zh, description_zh, content_zh,
                    address, venue_name,
                    price, is_free, fee_amount, fee_parsed_free,
                    start_time, end_time, timezone,
                    image_url, image_urls, highlights, activity_type, content_hash,
                    status
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    title_en=EXCLUDED.title_en,
                    title_zh=EXCLUDED.title_zh,
                    description_zh=EXCLUDED.description_zh,
                    content_zh=EXCLUDED.content_zh,
                    address=EXCLUDED.address,
                    venue_name=EXCLUDED.venue_name,
                    price=EXCLUDED.price,
                    is_free=EXCLUDED.is_free,
                    fee_amount=EXCLUDED.fee_amount,
                    fee_parsed_free=EXCLUDED.fee_parsed_free,
                    start_time=EXCLUDED.start_time,
                    end_time=EXCLUDED.end_time,
                    image_url=EXCLUDED.image_url,
                    image_urls=EXCLUDED.image_urls,
                    highlights=EXCLUDED.highlights,
                    activity_type=EXCLUDED.activity_type,
                    content_hash=EXCLUDED.content_hash,
                    updated_at=NOW()
                RETURNING id
                """,
                activity.source, activity.source_id, activity.source_url, activity.city_slug,
                activity.title_en, activity.title_zh, activity.description_zh, activity.content_zh,
                activity.address, activity.venue_name,
                activity.price, activity.is_free, activity.fee_amount, activity.fee_parsed_free,
                activity.start_time_utc.isoformat() if activity.start_time_utc else None,
                activity.end_time_utc.isoformat() if activity.end_time_utc else None,
                activity.timezone,
                activity.image_url,
                json.dumps(activity.image_urls, ensure_ascii=False),
                json.dumps(activity.highlights, ensure_ascii=False),
                activity.activity_type,
                activity.content_hash,
                activity.status,
            )
            return row["id"]

    async def exists(self, source: str, source_id: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM activities WHERE source = $1 AND source_id = $2",
                source, source_id,
            )
            return row is not None

    async def exists_content_hash(self, city_slug: str, content_hash: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM activities WHERE city_slug = $1 AND content_hash = $2",
                city_slug, content_hash,
            )
            return row is not None

    async def delete_activities(self, ids: list[int]) -> None:
        if not ids:
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """UPDATE candidate_activities
                        SET human_status = 'pending', fetched_detail = FALSE, activity_id = NULL
                        WHERE activity_id = ANY($1::int[])""",
                    ids,
                )
                await conn.execute(
                    "DELETE FROM activities WHERE id = ANY($1::int[])", ids,
                )

    async def mark_published(self, source: str, source_id: str, platform_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE activities
                   SET status = 'published', platform_activity_id = $1,
                       updated_at = NOW()
                   WHERE source = $2 AND source_id = $3""",
                platform_id, source, source_id,
            )

    async def mark_publish_failed(self, source: str, source_id: str, error: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE activities
                   SET status = 'failed', publish_error = $1, updated_at = NOW()
                   WHERE source = $2 AND source_id = $3""",
                error, source, source_id,
            )

    async def mark_skipped(self, source: str, source_id: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE activities SET status = 'skipped', updated_at = NOW()
                   WHERE source = $1 AND source_id = $2""",
                source, source_id,
            )

    # --- Processed Pages ---

    async def get_processed_page(self, source: str, source_url: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM processed_pages WHERE source = $1 AND source_url = $2",
                source, source_url,
            )
            return dict(row) if row else None

    async def save_processed_page(
        self, source: str, source_url: str, html_hash: str,
        status: str, activity_count: int = 0,
    ):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO processed_pages (source, source_url, html_hash, status, activity_count)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT(source, source_url) DO UPDATE SET
                    html_hash=EXCLUDED.html_hash,
                    status=EXCLUDED.status,
                    activity_count=EXCLUDED.activity_count,
                    updated_at=NOW()
                """,
                source, source_url, html_hash, status, activity_count,
            )

    # --- Scrape Tasks ---

    async def create_scrape_task(self, city_slugs: list[str], task_type: str = "discover") -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO scrape_tasks (task_type, city_slugs) VALUES ($1, $2) RETURNING id""",
                task_type, json.dumps(city_slugs),
            )
            return row["id"]

    async def create_task(
        self, task_type: str, city_slugs: list[str] | None = None, *, detail: str = "",
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO scrape_tasks (task_type, city_slugs, current_city) VALUES ($1, $2, $3) RETURNING id""",
                task_type, json.dumps(city_slugs or []), detail,
            )
            return row["id"]

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
        parts, args, n = [], [], 0
        if status is not None:
            n += 1; parts.append(f"status = ${n}"); args.append(status)
        if total_fetched is not None:
            n += 1; parts.append(f"total_fetched = ${n}"); args.append(total_fetched)
        if total_new is not None:
            n += 1; parts.append(f"total_new = ${n}"); args.append(total_new)
        if total_skipped is not None:
            n += 1; parts.append(f"total_skipped = ${n}"); args.append(total_skipped)
        if current_city is not None:
            n += 1; parts.append(f"current_city = ${n}"); args.append(current_city)
        if error_message is not None:
            n += 1; parts.append(f"error_message = ${n}"); args.append(error_message)
        if completed_at is not None:
            n += 1; parts.append(f"completed_at = ${n}"); args.append(completed_at)
        if not parts:
            return
        n += 1; args.append(task_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""UPDATE scrape_tasks SET {', '.join(parts)} WHERE id = ${n}""",
                *args,
            )

    async def complete_scrape_task(self, task_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE scrape_tasks SET status = 'completed', completed_at = NOW() WHERE id = $1""",
                task_id,
            )

    async def fail_scrape_task(self, task_id: int, error: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE scrape_tasks SET status = 'failed', error_message = $1, completed_at = NOW() WHERE id = $2""",
                error, task_id,
            )

    async def delete_scrape_task(self, task_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM scrape_tasks WHERE id = $1", task_id)

    async def clear_scrape_tasks(self):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM scrape_tasks")

    async def get_scrape_task(self, task_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM scrape_tasks WHERE id = $1", task_id,
            )
            return dict(row) if row else None

    async def list_recent_scrape_tasks(self, limit: int = 20) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM scrape_tasks ORDER BY id DESC LIMIT $1",
                limit,
            )
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
        conditions, args, n = [], [], 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if status:
            n += 1; conditions.append(f"status = ${n}"); args.append(status)
        if source:
            n += 1; conditions.append(f"source = ${n}"); args.append(source)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        n += 1; limit_n = n
        n += 1; offset_n = n
        args += [limit, offset]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM activities {where} ORDER BY id DESC LIMIT ${limit_n} OFFSET ${offset_n}",
                *args,
            )
            return [dict(r) for r in rows]

    async def count_activities(
        self,
        city_slug: str | None = None,
        status: str | None = None,
        source: str | None = None,
    ) -> int:
        conditions, args, n = [], [], 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if status:
            n += 1; conditions.append(f"status = ${n}"); args.append(status)
        if source:
            n += 1; conditions.append(f"source = ${n}"); args.append(source)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS cnt FROM activities {where}", *args,
            )
            return row["cnt"]

    async def get_activity(self, activity_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM activities WHERE id = $1", activity_id,
            )
            return dict(row) if row else None

    async def count_by_city_and_status(self) -> dict[str, dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT city_slug, status, COUNT(*) AS cnt FROM activities GROUP BY city_slug, status",
            )
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
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM activities WHERE id = ANY($1::int[])", ids,
            )
            return [dict(r) for r in rows]

    # --- Candidate Activities ---

    async def save_candidates(self, candidates: list[dict]) -> int:
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
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO candidate_activities (
                    city_slug, source, source_url, title, title_zh, event_date,
                    address, price, description, description_zh, ai_worth_fetching, ai_reason
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT(source, source_url) DO UPDATE SET
                    title=EXCLUDED.title,
                    title_zh=EXCLUDED.title_zh,
                    event_date=EXCLUDED.event_date,
                    address=EXCLUDED.address,
                    price=EXCLUDED.price,
                    description=EXCLUDED.description,
                    description_zh=EXCLUDED.description_zh,
                    ai_worth_fetching=EXCLUDED.ai_worth_fetching,
                    ai_reason=EXCLUDED.ai_reason
                """,
                rows,
            )
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
        conditions, args, n = [], [], 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if ai_failed is True:
            conditions.append("ai_reason = 'AI 过滤失败，默认跳过'")
        elif ai_worth is not None:
            n += 1; conditions.append(f"ai_worth_fetching = ${n}"); args.append(ai_worth)
            conditions.append("(ai_reason IS NULL OR ai_reason != 'AI 过滤失败，默认跳过')")
        if human_status:
            n += 1; conditions.append(f"human_status = ${n}"); args.append(human_status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        n += 1; limit_n = n
        n += 1; offset_n = n
        args += [limit, offset]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM candidate_activities {where} ORDER BY id DESC LIMIT ${limit_n} OFFSET ${offset_n}",
                *args,
            )
            return [dict(r) for r in rows]

    async def count_candidates(
        self,
        city_slug: str | None = None,
        ai_worth: bool | None = None,
        ai_failed: bool | None = None,
        human_status: str | None = None,
    ) -> int:
        conditions, args, n = [], [], 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if ai_failed is True:
            conditions.append("ai_reason = 'AI 过滤失败，默认跳过'")
        elif ai_worth is not None:
            n += 1; conditions.append(f"ai_worth_fetching = ${n}"); args.append(ai_worth)
            conditions.append("(ai_reason IS NULL OR ai_reason != 'AI 过滤失败，默认跳过')")
        if human_status:
            n += 1; conditions.append(f"human_status = ${n}"); args.append(human_status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS cnt FROM candidate_activities {where}", *args,
            )
            return row["cnt"]

    async def update_candidate_status(self, ids: list[int], status: str) -> None:
        if not ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE candidate_activities SET human_status = $1 WHERE id = ANY($2::int[])",
                status, ids,
            )

    async def delete_candidates(self, ids: list[int]) -> None:
        if not ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM candidate_activities WHERE id = ANY($1::int[])", ids,
            )

    async def mark_candidate_fetched(self, candidate_id: int, activity_id: int | None = None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE candidate_activities
                   SET fetched_detail = TRUE, activity_id = $1 WHERE id = $2""",
                activity_id, candidate_id,
            )

    async def get_candidates_to_fetch(
        self, city_slug: str | None = None,
    ) -> list[dict]:
        conditions = ["human_status = 'selected'", "fetched_detail = FALSE"]
        args: list = []
        n = 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        where = f"WHERE {' AND '.join(conditions)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM candidate_activities {where} ORDER BY id", *args,
            )
            return [dict(r) for r in rows]

    async def get_candidates_to_refilter(self, city_slug: str | None = None) -> list[dict]:
        conditions = ["ai_reason = 'AI 过滤失败，默认跳过'"]
        args: list = []
        n = 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        where = f"WHERE {' AND '.join(conditions)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM candidate_activities {where} ORDER BY id", *args,
            )
            return [dict(r) for r in rows]

    async def update_candidate_ai_result(self, candidate_id: int, worth: bool, reason: str, title_zh: str, description_zh: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE candidate_activities
                   SET ai_worth_fetching = $1, ai_reason = $2, title_zh = $3, description_zh = $4
                   WHERE id = $5""",
                worth, reason, title_zh, description_zh, candidate_id,
            )

    async def get_candidate(self, candidate_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM candidate_activities WHERE id = $1", candidate_id,
            )
            return dict(row) if row else None

    async def count_candidates_by_city(self) -> dict[str, dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT city_slug, human_status, COUNT(*) AS cnt FROM candidate_activities GROUP BY city_slug, human_status",
            )
            result: dict[str, dict] = {}
            for r in rows:
                city = r["city_slug"]
                if city not in result:
                    result[city] = {"total": 0}
                result[city][r["human_status"]] = r["cnt"]
                result[city]["total"] += r["cnt"]
            return result
