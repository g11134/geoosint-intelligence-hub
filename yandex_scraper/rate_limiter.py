import asyncio
import time


class RateLimiter:
    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self, prefix: str = "") -> None:
        async with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]
            if len(self._timestamps) >= self.max_per_minute:
                oldest = self._timestamps[0]
                wait_sec = 60.0 - (now - oldest) + 0.2
                print(
                    f"{prefix} [RateLimit] "
                    f"{len(self._timestamps)}/{self.max_per_minute} сессий/мин — "
                    f"пауза {wait_sec:.1f} сек..."
                )
                should_wait = True
                wait_duration = wait_sec
            else:
                should_wait = False
                wait_duration = 0.0
            if not should_wait:
                self._timestamps.append(time.monotonic())
                return
        await asyncio.sleep(wait_duration)
        async with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]
            self._timestamps.append(time.monotonic())

    def current_rate(self) -> int:
        now = time.monotonic()
        return sum(1 for t in self._timestamps if now - t < 60.0)
