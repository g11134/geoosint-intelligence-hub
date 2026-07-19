import asyncio
from pathlib import Path

import aiosqlite

class SeenIdsDB:
    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._cache: set = set()
        self._conn  = None
        self._lock  = None

    async def init(self):
        self._lock = asyncio.Lock()
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_ids (id TEXT PRIMARY KEY)"
        )
        await self._conn.commit()
        cur = await self._conn.execute("SELECT id FROM seen_ids")
        rows = await cur.fetchall()
        self._cache = {r[0] for r in rows}
        print(f"[DB] Загружено уникальных ID: {len(self._cache)}")

    async def is_seen(self, oid: str) -> bool:
        return oid in self._cache

    async def mark_seen(self, oid: str):
        if oid in self._cache:
            return
        async with self._lock:
            if oid in self._cache:
                return
            await self._conn.execute(
                "INSERT OR IGNORE INTO seen_ids VALUES (?)", (oid,)
            )
            await self._conn.commit()
            self._cache.add(oid)

    async def close(self):
        if self._conn:
            await self._conn.close()

    def count(self) -> int:
        return len(self._cache)

