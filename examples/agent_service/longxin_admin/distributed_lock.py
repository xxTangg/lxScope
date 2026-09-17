"""Small Redis-backed lease lock for product-level state transitions."""
from __future__ import annotations

import asyncio
import hmac
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4


_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class DistributedLease:
    """Coordinate a short critical section across workers and processes.

    The local lock remains useful for avoiding needless Redis contention. A
    Redis lease is used whenever the configured client supports deletion or
    scripts; minimal in-memory test doubles safely fall back to the local
    lock because they cannot represent cross-process state.
    """

    def __init__(
        self,
        client_factory: Callable[[], Any],
        local_lock: asyncio.Lock,
        key: str,
        *,
        ttl_ms: int = 30_000,
        wait_seconds: float = 10.0,
    ) -> None:
        self._client_factory = client_factory
        self._local_lock = local_lock
        self._key = key
        self._ttl_ms = ttl_ms
        self._wait_seconds = wait_seconds
        self._token = f"lock-{uuid4().hex}"
        self._client: Any | None = None
        self._distributed = False

    async def __aenter__(self) -> "DistributedLease":
        await self._local_lock.acquire()
        try:
            client = self._client_factory()
            self._client = client
            self._distributed = hasattr(client, "delete") or hasattr(client, "eval")
            if not self._distributed:
                return self
            deadline = time.monotonic() + self._wait_seconds
            while True:
                try:
                    acquired = await client.set(
                        self._key,
                        self._token,
                        nx=True,
                        px=self._ttl_ms,
                    )
                except TypeError:
                    # A compatible Redis wrapper may expose NX but not PX.
                    acquired = await client.set(self._key, self._token, nx=True)
                if acquired:
                    return self
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for the distributed state lock.")
                await asyncio.sleep(0.05)
        except BaseException:
            self._local_lock.release()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if self._distributed and self._client is not None:
                if hasattr(self._client, "eval"):
                    await self._client.eval(
                        _RELEASE_SCRIPT,
                        1,
                        self._key,
                        self._token,
                    )
                elif hasattr(self._client, "delete"):
                    current = await self._client.get(self._key)
                    current_text = (
                        current.decode("utf-8")
                        if isinstance(current, bytes)
                        else str(current)
                    )
                    if hmac.compare_digest(current_text, self._token):
                        await self._client.delete(self._key)
        finally:
            self._local_lock.release()
