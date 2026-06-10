import json
import ssl
from datetime import datetime, timezone

import asyncpg
from loguru import logger

from src.models.activity import ProcessedActivity
from src.models.property import Agent, PropertyCandidate, Property


class Database:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool: asyncpg.Pool | None = None

    @staticmethod
    async def _check_connection(conn):
        await conn.execute("SELECT 1")

    async def init(self):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self._pool = await asyncpg.create_pool(
            self.database_url, min_size=2, max_size=10,
            statement_cache_size=0, ssl=ssl_ctx,
            command_timeout=30, setup=self._check_connection,
        )

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
        new_candidates: int | None = None,
        updated_candidates: int | None = None,
        delisted_candidates: int | None = None,
        failed_count: int | None = None,
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
        if new_candidates is not None:
            n += 1; parts.append(f"new_candidates = ${n}"); args.append(new_candidates)
        if updated_candidates is not None:
            n += 1; parts.append(f"updated_candidates = ${n}"); args.append(updated_candidates)
        if delisted_candidates is not None:
            n += 1; parts.append(f"delisted_candidates = ${n}"); args.append(delisted_candidates)
        if failed_count is not None:
            n += 1; parts.append(f"failed_count = ${n}"); args.append(failed_count)
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

    async def list_recent_scrape_tasks(
        self,
        limit: int = 20,
        agent_id: int | None = None,
        task_type: str | None = None,
        since: datetime | None = None,
    ) -> list[dict]:
        conditions: list[str] = []
        args: list = []
        n = 0
        if agent_id is not None:
            n += 1; conditions.append(f"agent_id = ${n}"); args.append(agent_id)
        if task_type is not None:
            n += 1; conditions.append(f"task_type = ${n}"); args.append(task_type)
        if since is not None:
            n += 1; conditions.append(f"created_at >= ${n}"); args.append(since)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        n += 1; args.append(limit)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM scrape_tasks {where} ORDER BY id DESC LIMIT ${n}",
                *args,
            )
            return [dict(r) for r in rows]

    async def list_failed_scrape_tasks(
        self,
        since: datetime | None = None,
        max_retries: int = 3,
    ) -> list[dict]:
        """列出可重试的失败任务（失败次数未达上限）"""
        conditions = ["status = 'failed'"]
        args: list = []
        n = 0
        if since is not None:
            n += 1; conditions.append(f"created_at >= ${n}"); args.append(since)
        if max_retries is not None:
            n += 1; conditions.append(f"failed_count < ${n}"); args.append(max_retries)
        where = f"WHERE {' AND '.join(conditions)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM scrape_tasks {where} ORDER BY id DESC",
                *args,
            )
            return [dict(r) for r in rows]

    async def fetchval(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

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

    async def reset_stuck_candidates(self, city_slug: str | None = None) -> int:
        """Reset candidates stuck in 'selected' + fetched_detail=FALSE back to 'pending'."""
        conditions = ["human_status = 'selected'", "fetched_detail = FALSE"]
        args: list = []
        n = 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        where = f"WHERE {' AND '.join(conditions)}"
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE candidate_activities SET human_status = 'pending' {where}", *args,
            )
            # result is like 'UPDATE 5'
            return int(result.split()[-1]) if result else 0

    async def count_stuck_candidates(self, city_slug: str | None = None) -> int:
        """Count candidates stuck in 'selected' + fetched_detail=FALSE."""
        conditions = ["human_status = 'selected'", "fetched_detail = FALSE"]
        args: list = []
        n = 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        where = f"WHERE {' AND '.join(conditions)}"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) as cnt FROM candidate_activities {where}", *args,
            )
            return row["cnt"] if row else 0

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

    # --- Agents ---

    async def save_agent(self, agent: Agent) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO agents (
                    source, agent_id, name, name_zh, brokerage, phone, email, website,
                    city_slugs, province_code, is_active, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT(source, agent_id) DO UPDATE SET
                    name=EXCLUDED.name,
                    name_zh=EXCLUDED.name_zh,
                    brokerage=EXCLUDED.brokerage,
                    phone=EXCLUDED.phone,
                    email=EXCLUDED.email,
                    website=EXCLUDED.website,
                    city_slugs=EXCLUDED.city_slugs,
                    province_code=EXCLUDED.province_code,
                    is_active=EXCLUDED.is_active,
                    notes=EXCLUDED.notes,
                    updated_at=NOW()
                RETURNING id
                """,
                agent.source, agent.agent_id, agent.name, agent.name_zh,
                agent.brokerage, agent.phone, agent.email, agent.website,
                json.dumps(agent.city_slugs, ensure_ascii=False),
                agent.province_code, agent.is_active, agent.notes,
            )
            return row["id"]

    async def update_agent(self, agent: Agent) -> str:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE agents SET
                    agent_id = $2,
                    name = $3,
                    name_zh = $4,
                    brokerage = $5,
                    phone = $6,
                    email = $7,
                    website = $8,
                    city_slugs = $9,
                    province_code = $10,
                    is_active = $11,
                    notes = $12,
                    updated_at = NOW()
                WHERE id = $1
                """,
                agent.id, agent.agent_id, agent.name, agent.name_zh,
                agent.brokerage, agent.phone, agent.email, agent.website,
                json.dumps(agent.city_slugs, ensure_ascii=False),
                agent.province_code, agent.is_active, agent.notes,
            )
            logger.info(f"[update_agent] SQL result: {result}, agent_id={agent.id}")
            return result

    async def list_agents(self, is_active: bool | None = None) -> list[dict]:
        conditions, args, n = [], [], 0
        if is_active is not None:
            n += 1; conditions.append(f"is_active = ${n}"); args.append(is_active)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM agents {where} ORDER BY id", *args,
            )
            return [dict(r) for r in rows]

    async def get_agent(self, agent_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
            return dict(row) if row else None

    async def get_agent_by_source_id(self, source: str, agent_id: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM agents WHERE source = $1 AND agent_id = $2",
                source, agent_id,
            )
            return dict(row) if row else None

    async def delete_agent(self, id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM agents WHERE id = $1", id)

    # --- Property Candidates ---

    async def upsert_property_candidates(self, candidates: list[PropertyCandidate]) -> tuple[int, int, int]:
        """批量 upsert 候选房源，返回 (新增数, 更新数, 价格变动数)

        优化：先批量查询旧价格，再逐条 upsert（asyncpg executemany 不支持 RETURNING，
        故仍用循环，但减少了 N 次旧价格查询）。
        """
        if not candidates:
            return 0, 0, 0

        # 1. 批量查询旧价格（一次 DB round-trip）
        sources = [c.source for c in candidates]
        source_ids = [c.source_id for c in candidates]
        agent_ids = [c.agent_id for c in candidates]
        old_prices: dict[tuple[str, str, int | None], float | None] = {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT source, source_id, agent_id, price_numeric FROM property_candidates
                   WHERE (source, source_id, agent_id) IN (
                       SELECT * FROM unnest($1::text[], $2::text[], $3::int[])
                   )""",
                sources, source_ids, agent_ids,
            )
            for r in rows:
                old_prices[(r["source"], r["source_id"], r["agent_id"])] = r["price_numeric"]

        inserted = updated = price_changed = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for c in candidates:
                    old_price = old_prices.get((c.source, c.source_id, c.agent_id))

                    row = await conn.fetchrow(
                        """INSERT INTO property_candidates (
                            city_slug, agent_id, source, source_id, source_url, mls_number,
                            title, price, price_numeric, property_type, bedrooms, bathrooms,
                            address, postal_code, latitude, longitude,
                            photo_urls, open_house, description_en, raw_data,
                            listing_status, last_seen_at, miss_count,
                            human_status, fetched_detail, property_id
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, NOW(), 0, $22, $23, $24)
                        ON CONFLICT(source, source_id, agent_id) DO UPDATE SET
                            city_slug=EXCLUDED.city_slug,
                            title=EXCLUDED.title,
                            price=EXCLUDED.price,
                            price_numeric=EXCLUDED.price_numeric,
                            property_type=EXCLUDED.property_type,
                            bedrooms=EXCLUDED.bedrooms,
                            bathrooms=EXCLUDED.bathrooms,
                            address=EXCLUDED.address,
                            postal_code=EXCLUDED.postal_code,
                            latitude=EXCLUDED.latitude,
                            longitude=EXCLUDED.longitude,
                            photo_urls=EXCLUDED.photo_urls,
                            open_house=EXCLUDED.open_house,
                            description_en=EXCLUDED.description_en,
                            raw_data=EXCLUDED.raw_data,
                            last_seen_at=NOW(),
                            miss_count=0,
                            updated_at=NOW()
                        RETURNING id, xmax::text::int = 0 AS is_insert
                        """,
                        c.city_slug, c.agent_id, c.source, c.source_id, c.source_url, c.mls_number,
                        c.title, c.price, c.price_numeric, c.property_type, c.bedrooms, c.bathrooms,
                        c.address, c.postal_code, c.latitude, c.longitude,
                        json.dumps(c.photo_urls, ensure_ascii=False),
                        json.dumps(c.open_house, ensure_ascii=False),
                        c.description_en,
                        json.dumps(c.raw_data, ensure_ascii=False) if c.raw_data else None,
                        c.listing_status, c.human_status, c.fetched_detail, c.property_id,
                    )

                    if row["is_insert"]:
                        inserted += 1
                    else:
                        updated += 1
                        # 价格变动检测
                        if old_price is not None and c.price_numeric != old_price:
                            await conn.execute(
                                """UPDATE property_candidates SET
                                    previous_price_numeric = $1,
                                    listing_status = 'price_changed',
                                    history_log = jsonb_insert(history_log::jsonb, '{0}', $2::jsonb)::text
                                WHERE id = $3
                                """,
                                old_price,
                                json.dumps({"field": "price", "old": old_price, "new": c.price_numeric, "at": datetime.now(timezone.utc).isoformat()}),
                                row["id"],
                            )
                            price_changed += 1

        return inserted, updated, price_changed

    async def upsert_city_property_candidates(self, candidates: list[PropertyCandidate]) -> tuple[int, int]:
        """批量 upsert 城市爬虫候选房源（agent_id=NULL），返回 (新增数, 更新数)。

        使用 partial unique index (source, source_id) WHERE agent_id IS NULL
        配合 ON CONFLICT 实现原子 upsert，避免并发重复插入。
        """
        if not candidates:
            return 0, 0

        inserted = updated = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for c in candidates:
                    row = await conn.fetchrow(
                        """INSERT INTO property_candidates (
                            city_slug, agent_id, source, source_id, source_url, mls_number,
                            title, price, price_numeric, property_type, bedrooms, bathrooms,
                            address, postal_code, latitude, longitude,
                            photo_urls, open_house, description_en, raw_data,
                            listing_status, last_seen_at, miss_count,
                            human_status, fetched_detail, property_id
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, NOW(), 0, $22, $23, $24)
                        ON CONFLICT (source, source_id) WHERE agent_id IS NULL
                        DO UPDATE SET
                            city_slug = EXCLUDED.city_slug,
                            title = EXCLUDED.title,
                            price = EXCLUDED.price,
                            price_numeric = EXCLUDED.price_numeric,
                            property_type = EXCLUDED.property_type,
                            bedrooms = EXCLUDED.bedrooms,
                            bathrooms = EXCLUDED.bathrooms,
                            address = EXCLUDED.address,
                            postal_code = EXCLUDED.postal_code,
                            latitude = EXCLUDED.latitude,
                            longitude = EXCLUDED.longitude,
                            photo_urls = EXCLUDED.photo_urls,
                            open_house = EXCLUDED.open_house,
                            raw_data = EXCLUDED.raw_data,
                            last_seen_at = NOW(),
                            updated_at = NOW()
                        RETURNING xmax::text::int = 0 AS is_insert
                        """,
                        c.city_slug, c.agent_id, c.source, c.source_id, c.source_url, c.mls_number,
                        c.title, c.price, c.price_numeric, c.property_type, c.bedrooms, c.bathrooms,
                        c.address, c.postal_code, c.latitude, c.longitude,
                        json.dumps(c.photo_urls, ensure_ascii=False),
                        json.dumps(c.open_house, ensure_ascii=False),
                        c.description_en,
                        json.dumps(c.raw_data, ensure_ascii=False) if c.raw_data else None,
                        c.listing_status, c.human_status, c.fetched_detail, c.property_id,
                    )

                    if row["is_insert"]:
                        inserted += 1
                    else:
                        updated += 1

        return inserted, updated

    async def incremental_update_candidates(self, agent_id: int, current_ids: list[str]) -> int:
        """增量更新：标记下架房源。返回标记为 delisted 的数量。"""
        async with self._pool.acquire() as conn:
            # 未见到的房源 miss_count + 1
            await conn.execute(
                "UPDATE property_candidates SET miss_count = miss_count + 1 "
                "WHERE agent_id = $1 AND listing_status = 'active' AND source_id NOT IN (SELECT unnest($2::text[]))",
                agent_id, current_ids,
            )
            # 见到的房源重置 miss_count
            await conn.execute(
                "UPDATE property_candidates SET miss_count = 0 "
                "WHERE agent_id = $1 AND source_id IN (SELECT unnest($2::text[]))",
                agent_id, current_ids,
            )
            # 标记下架
            rows = await conn.fetch(
                "UPDATE property_candidates SET listing_status = 'delisted', updated_at = NOW() "
                "WHERE agent_id = $1 AND listing_status = 'active' AND miss_count >= 2 "
                "RETURNING id",
                agent_id,
            )
            return len(rows)

    async def list_property_candidates(
        self,
        agent_id: int | None = None,
        listing_status: str | None = None,
        human_status: str | None = None,
        city_slug: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions, args, n = [], [], 0
        if agent_id:
            n += 1; conditions.append(f"agent_id = ${n}"); args.append(agent_id)
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if listing_status:
            n += 1; conditions.append(f"listing_status = ${n}"); args.append(listing_status)
        if human_status:
            n += 1; conditions.append(f"human_status = ${n}"); args.append(human_status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        n += 1; limit_n = n
        n += 1; offset_n = n
        args += [limit, offset]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM property_candidates {where} ORDER BY id DESC LIMIT ${limit_n} OFFSET ${offset_n}",
                *args,
            )
            return [dict(r) for r in rows]

    async def count_property_candidates(
        self,
        agent_id: int | None = None,
        listing_status: str | None = None,
        human_status: str | None = None,
        city_slug: str | None = None,
    ) -> int:
        conditions, args, n = [], [], 0
        if agent_id:
            n += 1; conditions.append(f"agent_id = ${n}"); args.append(agent_id)
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if listing_status:
            n += 1; conditions.append(f"listing_status = ${n}"); args.append(listing_status)
        if human_status:
            n += 1; conditions.append(f"human_status = ${n}"); args.append(human_status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS cnt FROM property_candidates {where}", *args,
            )
            return row["cnt"]

    async def get_property_candidate(self, candidate_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM property_candidates WHERE id = $1", candidate_id,
            )
            return dict(row) if row else None

    async def update_property_candidate_status(self, ids: list[int], status: str) -> None:
        if not ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE property_candidates SET human_status = $1 WHERE id = ANY($2::int[])",
                status, ids,
            )

    async def mark_property_candidate_fetched(self, candidate_id: int) -> None:
        """标记候选已抓取详情页描述（仅设置 fetched_detail，不关联 property_id）"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE property_candidates SET fetched_detail = TRUE WHERE id = $1",
                candidate_id,
            )

    async def link_candidate_to_property(self, candidate_id: int, property_id: int) -> None:
        """将候选关联到已生成的 property 记录"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE property_candidates SET property_id = $1 WHERE id = $2",
                property_id, candidate_id,
            )

    async def update_candidate_description(self, candidate_id: int, description: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE property_candidates SET description_en = $1 WHERE id = $2",
                description, candidate_id,
            )

    async def update_candidate_photos(self, candidate_id: int, photo_urls: list[str]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE property_candidates SET photo_urls = $1 WHERE id = $2",
                json.dumps(photo_urls, ensure_ascii=False), candidate_id,
            )

    async def update_candidate_raw_data(self, candidate_id: int, raw_data: dict) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE property_candidates SET raw_data = $1 WHERE id = $2",
                json.dumps(raw_data, ensure_ascii=False) if raw_data else None, candidate_id,
            )

    async def get_property_candidates_to_fetch(self, agent_id: int | None = None, candidate_ids: list[int] | None = None, city_slug: str | None = None, limit: int = 20) -> list[dict]:
        conditions = ["human_status = 'selected'", "fetched_detail = FALSE"]
        args: list = []
        n = 0
        if agent_id:
            n += 1; conditions.append(f"agent_id = ${n}"); args.append(agent_id)
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if candidate_ids:
            n += 1; conditions.append(f"id = ANY(${n}::int[])"); args.append(candidate_ids)
        where = f"WHERE {' AND '.join(conditions)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM property_candidates {where} ORDER BY id LIMIT ${n+1}", *args, limit,
            )
            return [dict(r) for r in rows]

    async def get_property_candidates_to_process(self, agent_id: int | None = None, limit: int = 20) -> list[dict]:
        conditions = ["fetched_detail = TRUE", "property_id IS NULL", "human_status = 'selected'"]
        args: list = []
        n = 0
        if agent_id:
            n += 1; conditions.append(f"agent_id = ${n}"); args.append(agent_id)
        where = f"WHERE {' AND '.join(conditions)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM property_candidates {where} ORDER BY id LIMIT ${n+1}", *args, limit,
            )
            return [dict(r) for r in rows]

    async def delete_property_candidates(self, ids: list[int]) -> None:
        if not ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM property_candidates WHERE id = ANY($1::int[])", ids,
            )

    # --- Properties ---

    async def save_property(self, prop: Property) -> int | None:
        """保存房源到 properties 表。

        若房源已存在（source, source_id 冲突），按设计文档方案 A：
        拒绝创建重复内容，返回现有记录 id 并记录警告日志。
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO properties (
                    source, source_id, source_url, city_slug, agent_id,
                    title_en, title_zh, price, price_numeric, mls_number, property_type,
                    bedrooms, bathrooms,
                    address, postal_code, latitude, longitude,
                    description_zh, content_zh, highlights, open_house,
                    image_url, image_urls,
                    agent_name, agent_brokerage, agent_phone,
                    status, last_scraped_at, content_hash,
                    raw_data
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30)
                ON CONFLICT(source, source_id) DO NOTHING
                RETURNING id
                """,
                prop.source, prop.source_id, prop.source_url, prop.city_slug, prop.agent_id,
                prop.title_en, prop.title_zh, prop.price, prop.price_numeric, prop.mls_number, prop.property_type,
                prop.bedrooms, prop.bathrooms,
                prop.address, prop.postal_code, prop.latitude, prop.longitude,
                prop.description_zh, prop.content_zh,
                json.dumps(prop.highlights, ensure_ascii=False),
                json.dumps(prop.open_house, ensure_ascii=False),
                prop.image_url,
                json.dumps(prop.image_urls, ensure_ascii=False),
                prop.agent_name, prop.agent_brokerage, prop.agent_phone,
                prop.status, prop.last_scraped_at.isoformat() if prop.last_scraped_at else None,
                prop.content_hash,
                json.dumps(prop.raw_data, ensure_ascii=False) if prop.raw_data else None,
            )
            if row:
                return row["id"]

            # 冲突：查询现有记录 id
            existing = await conn.fetchrow(
                "SELECT id, agent_id FROM properties WHERE source = $1 AND source_id = $2",
                prop.source, prop.source_id,
            )
            if existing:
                logger.warning(
                    f"Property conflict: source_id={prop.source_id} already exists "
                    f"(existing_agent_id={existing['agent_id']}, new_agent_id={prop.agent_id}). "
                    f"Skipping duplicate creation per design doc policy A."
                )
                return existing["id"]
            return None

    COMMERCIAL_PROPERTY_TYPES = [
        "Commercial", "Business", "Retail", "Hospitality", "Industrial",
        "Office", "Mixed Use", "Mixed", "Shopping Center", "Plaza",
        "Strip Mall", "Warehouse", "Storefront",
    ]

    async def list_properties(
        self,
        city_slug: str | None = None,
        status: str | None = None,
        agent_id: int | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions, args, n = [], [], 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if status:
            n += 1; conditions.append(f"status = ${n}"); args.append(status)
        if agent_id:
            n += 1; conditions.append(f"agent_id = ${n}"); args.append(agent_id)
        if category == "commercial":
            n += 1; conditions.append(f"property_type = ANY(${n}::text[])"); args.append(self.COMMERCIAL_PROPERTY_TYPES)
        elif category == "residential":
            n += 1; conditions.append(f"property_type IS NOT NULL AND property_type <> ALL(${n}::text[])"); args.append(self.COMMERCIAL_PROPERTY_TYPES)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        n += 1; limit_n = n
        n += 1; offset_n = n
        args += [limit, offset]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM properties {where} ORDER BY id DESC LIMIT ${limit_n} OFFSET ${offset_n}",
                *args,
            )
            return [dict(r) for r in rows]

    async def count_properties(
        self,
        city_slug: str | None = None,
        status: str | None = None,
        agent_id: int | None = None,
        category: str | None = None,
    ) -> int:
        conditions, args, n = [], [], 0
        if city_slug:
            n += 1; conditions.append(f"city_slug = ${n}"); args.append(city_slug)
        if status:
            n += 1; conditions.append(f"status = ${n}"); args.append(status)
        if agent_id:
            n += 1; conditions.append(f"agent_id = ${n}"); args.append(agent_id)
        if category == "commercial":
            n += 1; conditions.append(f"property_type = ANY(${n}::text[])"); args.append(self.COMMERCIAL_PROPERTY_TYPES)
        elif category == "residential":
            n += 1; conditions.append(f"property_type IS NOT NULL AND property_type <> ALL(${n}::text[])"); args.append(self.COMMERCIAL_PROPERTY_TYPES)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS cnt FROM properties {where}", *args,
            )
            return row["cnt"]

    async def get_property(self, property_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM properties WHERE id = $1", property_id,
            )
            if not row:
                return None
            result = dict(row)
            # raw_data 可能是 JSONB（dict）或 JSON 字符串，统一为 dict
            # 注：asyncpg 通常将 JSONB 返回为 Python dict，str 分支为防御性处理
            raw = result.get("raw_data")
            if isinstance(raw, str):
                try:
                    result["raw_data"] = json.loads(raw)
                except Exception:
                    result["raw_data"] = {}
            elif raw is None:
                result["raw_data"] = {}
            return result

    async def get_property_by_source(self, source: str, source_id: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM properties WHERE source = $1 AND source_id = $2",
                source, source_id,
            )
            return dict(row) if row else None

    async def get_properties_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM properties WHERE id = ANY($1::int[])", ids,
            )
            return [dict(r) for r in rows]

    async def update_property_status(self, property_id: int, status: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE properties SET status = $1, updated_at = NOW() WHERE id = $2",
                status, property_id,
            )

    async def mark_property_published(self, source: str, source_id: str, platform_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE properties SET status = 'published', platform_property_id = $1, updated_at = NOW() "
                "WHERE source = $2 AND source_id = $3",
                platform_id, source, source_id,
            )

    async def mark_property_publish_failed(self, source: str, source_id: str, error: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE properties SET status = 'failed', publish_error = $1, updated_at = NOW() "
                "WHERE source = $2 AND source_id = $3",
                error, source, source_id,
            )

    async def delete_properties(self, ids: list[int]) -> None:
        if not ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM properties WHERE id = ANY($1::int[])", ids,
            )
