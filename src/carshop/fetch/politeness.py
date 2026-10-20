"""Per-host concurrency and pacing (requirements 7.4).

At most one request in flight per host, and a pause drawn uniformly from
[min_pause_s, max_pause_s] between consecutive requests to the same host (v1.3 baseline
5-10 s). Dealers that share a group domain share that host's lock and pause.
"""

from __future__ import annotations

import asyncio
import random
import time
from urllib.parse import urlsplit


class HostGate:
    def __init__(self, min_pause_s: float, max_pause_s: float | None = None) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._min = max(0.0, min_pause_s)
        self._max = max(self._min, max_pause_s if max_pause_s is not None else min_pause_s)
        self._rng = random.Random()  # noqa: S311 - pacing jitter, not security

    def _lock(self, host: str) -> asyncio.Lock:
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    def _pause(self) -> float:
        return self._min if self._max <= self._min else self._rng.uniform(self._min, self._max)

    async def __call__(self, url: str) -> _Slot:
        host = (urlsplit(url).hostname or "").lower()
        return _Slot(self, host)

    async def acquire(self, host: str) -> None:
        await self._lock(host).acquire()
        elapsed = time.monotonic() - self._last.get(host, 0.0)
        pause = self._pause()
        if elapsed < pause:
            await asyncio.sleep(pause - elapsed)

    def release(self, host: str) -> None:
        self._last[host] = time.monotonic()
        self._lock(host).release()


class _Slot:
    def __init__(self, gate: HostGate, host: str) -> None:
        self._gate = gate
        self._host = host

    async def __aenter__(self) -> None:
        await self._gate.acquire(self._host)

    async def __aexit__(self, *exc: object) -> None:
        self._gate.release(self._host)
