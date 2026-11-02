# -*- coding: utf-8 -*-
"""Local-only persistent stand-in for Redis.

The development setup runs AgentScope on an in-process ``fakeredis`` server so
no external Redis service is required.  Plain fakeredis keeps every key in
process memory, which means *restarting the service wipes all agents,
sessions, messages and credentials* -- users see empty lists and "nothing
works" right after a restart.

This module keeps the fakeredis behaviour (nothing to install, no server to
manage) but adds a disk snapshot: the whole in-memory database is serialised
to a pickle file on a timer and restored at startup.

Only local development uses this.  Deployments with a real Redis never import
it.
"""
from __future__ import annotations

import asyncio
import os
import pickle
from pathlib import Path
from typing import Any

from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis

DEFAULT_SNAPSHOT_PATH = "./local_redis_snapshot.pkl"
_AUTOSAVE_INTERVAL_SECONDS = 10.0


def _export_state(server: FakeServer) -> dict[int, dict[Any, Any]]:
    """Copy the live databases into plain dicts.

    ``server.dbs`` is a ``defaultdict`` whose factory is a lambda defined
    inside ``FakeServer.__init__``; pickling the defaultdict directly fails
    with "Can't get local object".  The per-key ``Item`` objects themselves
    only hold ``value`` and ``expireat``, so flattening to a plain ``dict``
    makes the state picklable.
    """
    return {int(db): dict(items) for db, items in server.dbs.items()}


def save_snapshot(server: FakeServer, path: str | Path) -> None:
    """Atomically write the current in-memory database to ``path``."""
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    payload = pickle.dumps(_export_state(server), protocol=pickle.HIGHEST_PROTOCOL)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)


def load_server(path: str | Path) -> FakeServer:
    """Create a FakeServer pre-populated from the snapshot, if it exists."""
    server = FakeServer()
    target = Path(path)
    if not target.exists():
        return server
    try:
        state = pickle.loads(target.read_bytes())
        restored = 0
        for db, items in state.items():
            server.dbs[db].update(items)
            restored += len(items)
        print(f"[local-redis] restored {restored} keys from {target}", flush=True)
    except Exception as exc:  # pragma: no cover - corrupt snapshot is not fatal
        print(
            f"[local-redis] snapshot at {target} ignored ({exc!r}); starting empty",
            flush=True,
        )
    return server


def create_persistent_redis(
    path: str | Path | None = None,
) -> tuple[FakeRedis, FakeServer, Path]:
    """Build a FakeRedis client backed by a snapshot-aware server.

    ``decode_responses=True`` is required.  The upstream ``RedisStorage``
    treats every value read back from Redis as ``str``: it takes the ids from
    ``smembers`` and interpolates them into key templates.  With the
    redis-py default (``decode_responses=False``) those ids are ``bytes``, so
    the lookup key becomes ``...:agent:b'874abc...'``, ``GET`` misses and every
    ``list_*`` call silently returns an empty list while ``create_*`` succeeds.
    A real Redis deployment sets this on the client, so mirroring it here keeps
    behaviour identical.
    """
    snapshot = Path(path or os.getenv("LXSCOPE_LOCAL_REDIS_SNAPSHOT", DEFAULT_SNAPSHOT_PATH))
    server = load_server(snapshot)
    client = FakeRedis(server=server, decode_responses=True)
    return client, server, snapshot


async def autosave_loop(
    server: FakeServer,
    path: str | Path,
    interval: float = _AUTOSAVE_INTERVAL_SECONDS,
) -> None:
    """Persist the snapshot periodically until cancelled."""
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(save_snapshot, server, path)
            except Exception as exc:  # pragma: no cover - never kill the app
                print(f"[local-redis] autosave failed: {exc!r}", flush=True)
    except asyncio.CancelledError:
        try:
            await asyncio.to_thread(save_snapshot, server, path)
        except Exception as exc:  # pragma: no cover
            print(f"[local-redis] final save failed: {exc!r}", flush=True)
        raise


def start_autosave(
    server: FakeServer,
    path: str | Path,
    interval: float = _AUTOSAVE_INTERVAL_SECONDS,
) -> asyncio.Task:
    return asyncio.create_task(autosave_loop(server, path, interval))


async def stop_autosave(task: asyncio.Task, server: FakeServer, path: str | Path) -> None:
    """Cancel the autosave task (it writes a last snapshot on cancel)."""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    try:
        await asyncio.to_thread(save_snapshot, server, path)
    except Exception as exc:  # pragma: no cover
        print(f"[local-redis] shutdown save failed: {exc!r}", flush=True)
