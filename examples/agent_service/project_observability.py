# -*- coding: utf-8 -*-
"""Project-level observability for the AgentScope example service.

This module deliberately observes the whole service rather than one product
feature.  It provides:

* request correlation through ``X-Request-ID`` and OpenTelemetry trace IDs;
* bounded, structured event logs for HTTP, Agent, model and tool lifecycles;
* a small Prometheus-compatible in-process metrics endpoint;
* optional OTLP span export for the existing AgentScope tracing middleware;
* an AgentScope middleware that measures Agent, model and tool timings.

Payloads, prompts, tool arguments and tool results are never written by this
module. The existing AgentScope tracing middleware is enabled only when this
application configures an OTel SDK; exporting its richer span attributes
remains an explicit deployment choice.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
from contextvars import ContextVar
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

from agentscope._logging import logger
from agentscope.app.deps import get_current_user_id
from agentscope.middleware import MiddlewareBase

try:
    from observability_analytics import ObservabilityEventStore
except ModuleNotFoundError:
    from examples.agent_service.observability_analytics import ObservabilityEventStore

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
except ImportError:  # pragma: no cover - optional runtime packaging fallback
    OTLPSpanExporter = None  # type: ignore[assignment,misc]


_request_id_context: ContextVar[str] = ContextVar(
    "lxscope_observability_request_id",
    default="",
)
_trace_id_context: ContextVar[str] = ContextVar(
    "lxscope_observability_trace_id",
    default="",
)
_user_id_context: ContextVar[str] = ContextVar(
    "lxscope_observability_user_id",
    default="",
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ObservabilitySettings:
    """Runtime switches for project-level observability."""

    enabled: bool = True
    service_name: str = "lxscope-agent-service"
    otlp_endpoint: str | None = None
    max_events: int = 50_000

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        """Load settings without requiring a monitoring backend."""
        endpoint = (
            os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
            or os.getenv("LXSCOPE_OTEL_ENDPOINT", "").strip()
            or None
        )
        try:
            max_events = max(
                100,
                int(os.getenv("LXSCOPE_OBSERVABILITY_MAX_EVENTS", "50000")),
            )
        except ValueError:
            max_events = 50_000
        return cls(
            enabled=_env_bool("LXSCOPE_OBSERVABILITY_ENABLED", True),
            service_name=(
                os.getenv("OTEL_SERVICE_NAME", "lxscope-agent-service").strip()
                or "lxscope-agent-service"
            ),
            otlp_endpoint=endpoint,
            max_events=max_events,
        )


@dataclass
class _Duration:
    count: int = 0
    total: float = 0.0
    maximum: float = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.maximum = max(self.maximum, value)


class ObservabilityMetrics:
    """Thread-safe, bounded metrics registry for the service process.

    The registry is intentionally small and backend-neutral.  Prometheus can
    scrape the rendered text directly, while a future collector can consume
    the same logical names without changing application code.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], int]] = (
            defaultdict(dict)
        )
        self._durations: dict[
            str,
            dict[tuple[tuple[str, str], ...], _Duration],
        ] = defaultdict(dict)

    @staticmethod
    def _labels(values: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (str(key), str(value).replace("\n", " "))
                for key, value in values.items()
                if value is not None
            ),
        )

    def increment(self, name: str, **labels: Any) -> None:
        self.add(name, 1, **labels)

    def add(self, name: str, value: int, **labels: Any) -> None:
        """Add a non-negative integer value to a counter."""
        if value < 0:
            raise ValueError("Counter increments cannot be negative.")
        key = self._labels(labels)
        with self._lock:
            bucket = self._counters[name]
            bucket[key] = bucket.get(key, 0) + value

    def observe_duration(self, name: str, seconds: float, **labels: Any) -> None:
        key = self._labels(labels)
        with self._lock:
            bucket = self._durations[name]
            stat = bucket.setdefault(key, _Duration())
            stat.add(max(0.0, seconds))

    def render_prometheus(self) -> str:
        """Render counters and duration summaries as Prometheus text."""
        lines: list[str] = []
        with self._lock:
            counters = {
                name: dict(values) for name, values in self._counters.items()
            }
            durations = {
                name: {
                    labels: _Duration(
                        count=value.count,
                        total=value.total,
                        maximum=value.maximum,
                    )
                    for labels, value in values.items()
                }
                for name, values in self._durations.items()
            }

        for name in sorted(counters):
            lines.append(f"# TYPE {name} counter")
            for labels, value in sorted(counters[name].items()):
                lines.append(f"{name}{_format_labels(labels)} {value}")

        for name in sorted(durations):
            lines.append(f"# TYPE {name} summary")
            for labels, value in sorted(durations[name].items()):
                rendered = _format_labels(labels)
                lines.append(f'{name}_count{rendered} {value.count}')
                lines.append(f'{name}_sum{rendered} {value.total:.9f}')
                lines.append(f'{name}_max{rendered} {value.maximum:.9f}')

        return "\n".join(lines) + ("\n" if lines else "")


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = []
    for key, value in labels:
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped.append(f'{key}="{value}"')
    return "{" + ",".join(escaped) + "}"


class ProjectObservability:
    """Own project-wide metrics, tracing and bounded event logging."""

    def __init__(
        self,
        settings: ObservabilitySettings | None = None,
        metrics: ObservabilityMetrics | None = None,
        events: ObservabilityEventStore | None = None,
        persistent_store: Any | None = None,
    ) -> None:
        self.settings = settings or ObservabilitySettings.from_env()
        self.metrics = metrics or ObservabilityMetrics()
        self.events = events or ObservabilityEventStore(
            max_events=self.settings.max_events,
        )
        self._persistent_store = persistent_store
        self._persistence_tasks: set[asyncio.Task[Any]] = set()
        self._tracer_provider: TracerProvider | None = None
        self._owns_tracer_provider = False
        self._configured = False

    def set_persistent_store(self, store: Any | None) -> None:
        """Attach the application-owned durable event repository."""
        self._persistent_store = store

    async def restore_persisted_events(self) -> int:
        """Restore the bounded recent event window after service startup."""
        if self._persistent_store is None:
            return 0
        events = await self._persistent_store.load_recent(
            limit=self.events.max_events,
        )
        self.events.replace(events)
        return len(events)

    async def close_persistent_store(self) -> None:
        """Flush pending writes before disposing the durable repository."""
        while self._persistence_tasks:
            tasks = tuple(self._persistence_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
        store = self._persistent_store
        self._persistent_store = None
        if store is not None:
            await store.close()

    def configure(self) -> None:
        """Configure a process-wide OTel provider once.

        A provider is created even without an exporter so the AgentScope
        tracing middleware has a real SDK context.  Without an OTLP endpoint
        spans stay in-process and are dropped, preserving local compatibility.
        """
        if self._configured or not self.settings.enabled:
            return

        try:
            current_provider = trace.get_tracer_provider()
            if isinstance(current_provider, TracerProvider):
                self._tracer_provider = current_provider
            else:
                provider = TracerProvider(
                    resource=Resource.create(
                        {"service.name": self.settings.service_name},
                    ),
                )
                if self.settings.otlp_endpoint:
                    if OTLPSpanExporter is None:
                        logger.warning(
                            "observability.otlp_unavailable endpoint=%s",
                            self.settings.otlp_endpoint,
                        )
                    else:
                        try:
                            provider.add_span_processor(
                                BatchSpanProcessor(
                                    OTLPSpanExporter(
                                        endpoint=self.settings.otlp_endpoint,
                                    ),
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "observability.otlp_setup_failed endpoint=%s",
                                self.settings.otlp_endpoint,
                            )
                trace.set_tracer_provider(provider)
                self._tracer_provider = provider
                self._owns_tracer_provider = True
        except Exception:
            # Observability must never prevent the API from starting. The
            # regular logging and in-process metrics paths still work.
            logger.exception("observability.tracing_setup_failed")

        self._configured = True
        logger.info(
            "observability.configured service=%s otlp_export_enabled=%s",
            self.settings.service_name,
            bool(self.settings.otlp_endpoint),
        )

    def shutdown(self) -> None:
        """Flush an exporter during graceful service shutdown."""
        if self._owns_tracer_provider and self._tracer_provider is not None:
            self._tracer_provider.shutdown()

    def tracer(self):
        self.configure()
        return trace.get_tracer(self.settings.service_name)

    @staticmethod
    def trace_id(span: Span | None) -> str:
        if span is None:
            return ""
        context = span.get_span_context()
        if not context.is_valid:
            return ""
        return format(context.trace_id, "032x")

    def record_event(
        self,
        event_name: str,
        *,
        component: str,
        result: str,
        duration_seconds: float | None = None,
        **attributes: Any,
    ) -> None:
        """Record a bounded event without request or payload contents."""
        if not self.settings.enabled:
            return
        safe_attributes = {
            key: value
            for key, value in attributes.items()
            if value is not None
            and key not in {"prompt", "messages", "input", "output", "content"}
        }
        safe_attributes.setdefault("request_id", _request_id_context.get() or None)
        safe_attributes.setdefault("trace_id", _trace_id_context.get() or None)
        safe_attributes.setdefault("user_id", _user_id_context.get() or None)
        event_payload = {
            "event": event_name,
            "component": component,
            "result": result,
            **safe_attributes,
        }
        if duration_seconds is not None:
            event_payload["duration_seconds"] = round(max(0.0, duration_seconds), 6)
        event = self.events.record(
            event_name,
            component=component,
            result=result,
            duration_seconds=duration_seconds,
            **safe_attributes,
        )
        self._schedule_persistence(event)
        logger.info(
            "observability.event %s",
            json.dumps(event_payload, sort_keys=True),
        )

        self.metrics.increment(
            "lxscope_observability_events_total",
            component=component,
            event=event_name,
            result=result,
        )
        if duration_seconds is not None:
            self.metrics.observe_duration(
                "lxscope_observability_event_duration_seconds",
                duration_seconds,
                component=component,
                event=event_name,
            )

    def _schedule_persistence(self, event: Any) -> None:
        store = self._persistent_store
        if store is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._persist_event(store, event))
        self._persistence_tasks.add(task)
        task.add_done_callback(self._persistence_tasks.discard)

    @staticmethod
    async def _persist_event(store: Any, event: Any) -> None:
        try:
            await store.record(event)
        except Exception:
            logger.warning(
                "observability.event_persist_failed event_name=%s "
                "component=%s",
                event.event_name,
                event.component,
                exc_info=True,
            )

    def record_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
        request_id: str | None = None,
        trace_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        if not self.settings.enabled:
            return
        result = "success" if status_code < 400 else "error"
        labels = {
            "method": method,
            "route": route,
            "status_class": f"{status_code // 100}xx",
            "result": result,
        }
        self.metrics.increment("lxscope_http_requests_total", **labels)
        self.metrics.observe_duration(
            "lxscope_http_request_duration_seconds",
            duration_seconds,
            method=method,
            route=route,
        )
        self.record_event(
            "http.request.completed",
            component="http",
            result=result,
            method=method,
            route=route,
            status_code=status_code,
            duration_seconds=duration_seconds,
            request_id=request_id,
            trace_id=trace_id,
            user_id=user_id,
            error_code=f"http_{status_code}" if status_code >= 400 else None,
        )

    @contextmanager
    def context(
        self,
        request_id: str,
        trace_id: str = "",
        user_id: str = "",
    ) -> Iterator[None]:
        """Propagate correlation IDs into background Agent tasks and logs."""
        request_token = _request_id_context.set(request_id)
        trace_token = _trace_id_context.set(trace_id)
        user_token = _user_id_context.set(user_id)
        try:
            yield
        finally:
            _request_id_context.reset(request_token)
            _trace_id_context.reset(trace_token)
            _user_id_context.reset(user_token)

    def agent_middleware(self) -> "ProjectAgentObservabilityMiddleware":
        return ProjectAgentObservabilityMiddleware(self)

    @contextmanager
    def http_span(self, request: Request) -> Iterator[Span]:
        """Create an HTTP span and keep it current for downstream calls."""
        tracer = self.tracer()
        # Keep raw paths out of span names and attributes: resource IDs in a
        # URL can be sensitive and would also create high-cardinality traces.
        span_name = f"HTTP {request.method}"
        with tracer.start_as_current_span(
            span_name,
            attributes={
                "http.request.method": request.method,
                "service.name": self.settings.service_name,
            },
        ) as span:
            yield span


class ProjectAgentObservabilityMiddleware(MiddlewareBase):
    """Measure Agent, model and tool lifecycles without capturing payloads."""

    def __init__(self, observability: ProjectObservability) -> None:
        self._observability = observability

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        started = time.perf_counter()
        try:
            async for item in next_handler(**input_kwargs):
                yield item
        except BaseException as exc:
            self._record(
                "agent.reply.completed",
                "agent",
                "failed",
                started,
                agent_name=getattr(agent, "name", None),
                session_id=_session_id(agent),
                error_code=type(exc).__name__,
            )
            raise
        else:
            self._record(
                "agent.reply.completed",
                "agent",
                "success",
                started,
                agent_name=getattr(agent, "name", None),
                session_id=_session_id(agent),
            )

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        started = time.perf_counter()
        model = input_kwargs.get("current_model")
        model_name = getattr(model, "model", None)
        try:
            result = await next_handler(**input_kwargs)
        except BaseException as exc:
            self._record(
                "model.call.completed",
                "model",
                "failed",
                started,
                model=model_name,
                agent_name=getattr(agent, "name", None),
                session_id=_session_id(agent),
                error_code=type(exc).__name__,
            )
            raise

        if inspect.isasyncgen(result):
            async def observed() -> AsyncGenerator[Any, None]:
                last: Any = None
                try:
                    async for item in result:
                        last = item
                        yield item
                except BaseException as exc:
                    self._record(
                        "model.call.completed",
                        "model",
                        "failed",
                        started,
                        model=model_name,
                        agent_name=getattr(agent, "name", None),
                        session_id=_session_id(agent),
                        error_code=type(exc).__name__,
                    )
                    raise
                else:
                    self._record_model_result(
                        started,
                        model_name,
                        last,
                        "success",
                        agent_name=getattr(agent, "name", None),
                        session_id=_session_id(agent),
                    )

            return observed()

        self._record_model_result(
            started,
            model_name,
            result,
            "success",
            agent_name=getattr(agent, "name", None),
            session_id=_session_id(agent),
        )
        return result

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        started = time.perf_counter()
        tool_call = input_kwargs.get("tool_call")
        tool_name = getattr(tool_call, "name", None)
        tool_kind, mcp_server = _tool_metadata(tool_call)
        try:
            async for item in next_handler(**input_kwargs):
                yield item
        except BaseException as exc:
            self._record(
                "tool.call.completed",
                "tool",
                "failed",
                started,
                tool=tool_name,
                tool_kind=tool_kind,
                mcp_server=mcp_server,
                agent_name=getattr(agent, "name", None),
                session_id=_session_id(agent),
                error_code=type(exc).__name__,
            )
            raise
        else:
            self._record(
                "tool.call.completed",
                "tool",
                "success",
                started,
                tool=tool_name,
                tool_kind=tool_kind,
                mcp_server=mcp_server,
                agent_name=getattr(agent, "name", None),
                session_id=_session_id(agent),
            )

    def _record(
        self,
        event_name: str,
        component: str,
        result: str,
        started: float,
        **attributes: Any,
    ) -> None:
        duration = time.perf_counter() - started
        self._observability.record_event(
            event_name,
            component=component,
            result=result,
            duration_seconds=duration,
            **attributes,
        )

    def _record_model_result(
        self,
        started: float,
        model_name: Any,
        result: Any,
        outcome: str,
        **attributes: Any,
    ) -> None:
        input_tokens, output_tokens = _usage_tokens(result)
        self._record(
            "model.call.completed",
            "model",
            outcome,
            started,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            **attributes,
        )
        if not self._observability.settings.enabled:
            return
        for name, value in (
            ("input", input_tokens),
            ("output", output_tokens),
        ):
            if value:
                self._observability.metrics.add(
                    "lxscope_model_tokens_total",
                    value,
                    direction=name,
                    model=model_name or "unknown",
                )


def _session_id(agent: Any) -> str | None:
    """Read the session correlation field without depending on Agent type."""
    return getattr(getattr(agent, "state", None), "session_id", None)


def _tool_metadata(tool_call: Any) -> tuple[str, str | None]:
    """Read optional MCP metadata without depending on AgentScope internals."""
    mcp_server = (
        getattr(tool_call, "mcp_server", None)
        or getattr(tool_call, "server_name", None)
    )
    declared_kind = (
        getattr(tool_call, "tool_kind", None)
        or getattr(tool_call, "kind", None)
    )
    tool_kind = str(declared_kind).strip() if declared_kind else "Tool"
    if mcp_server and tool_kind.casefold() == "tool":
        tool_kind = "MCP"
    return tool_kind, mcp_server


def _usage_tokens(result: Any) -> tuple[int, int]:
    """Read only numeric usage fields from a model response."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return 0, 0
    values: list[int] = []
    for name in ("input_tokens", "output_tokens"):
        value = getattr(usage, name, None)
        values.append(value if isinstance(value, int) and value >= 0 else 0)
    return values[0], values[1]


observability_router = APIRouter(prefix="/observability", tags=["observability"])


@observability_router.get(
    "/metrics",
    response_class=Response,
    summary="Return project-level Prometheus metrics",
)
async def get_observability_metrics(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Return metrics to an authenticated administrator only."""
    auth = getattr(request.app.state, "auth", None)
    if auth is not None:
        if not await auth.is_admin_user(user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrator access is required.",
            )

    observability = getattr(request.app.state, "observability", None)
    if not isinstance(observability, ProjectObservability):
        return Response(content="", media_type="text/plain; version=0.0.4")
    return Response(
        content=observability.metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4",
    )


def new_request_id(request: Request) -> str:
    """Read or create a bounded request correlation ID."""
    return (
        request.headers.get("X-Request-ID", "").strip()
        or f"req-{uuid4().hex}"
    )
