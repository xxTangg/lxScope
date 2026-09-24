"""Request-local organization identity used by application adapters."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


IDENTITY_SEPARATOR = "::"


_identity: ContextVar[Any | None] = ContextVar("lxscope_identity", default=None)


def current_identity() -> Any | None:
    """Return the authenticated application identity for this request."""
    return _identity.get()


def current_tenant_id() -> str | None:
    """Return the active Logto organization id, when one is present."""
    identity = _identity.get()
    tenant_id = getattr(identity, "tenant_id", None)
    return tenant_id if isinstance(tenant_id, str) and tenant_id else None


def scoped_user_id(tenant_id: str, subject_id: str) -> str:
    """Build the application user key used by AgentScope-facing services."""
    tenant = tenant_id.strip()
    subject = subject_id.strip()
    if not tenant or not subject:
        raise ValueError("tenant_id and subject_id are required.")
    return f"{tenant}{IDENTITY_SEPARATOR}{subject}"


def split_scoped_user_id(user_id: str) -> tuple[str, str] | None:
    """Split an application user key into tenant and subject components."""
    tenant_id, separator, subject_id = user_id.partition(IDENTITY_SEPARATOR)
    if not separator or not tenant_id or not subject_id:
        return None
    return tenant_id, subject_id


def set_identity(identity: Any | None) -> Token[Any | None]:
    """Set request-local identity and return the token needed to restore it."""
    return _identity.set(identity)


def reset_identity(token: Token[Any | None]) -> None:
    """Restore the identity that was active before the current request."""
    _identity.reset(token)
