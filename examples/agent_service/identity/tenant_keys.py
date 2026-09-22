# -*- coding: utf-8 -*-
"""Tenant-aware keys for application-owned Redis data.

AgentScope core keeps its existing ``user_id`` contract.  Application-owned
business data can use this helper to add the verified tenant dimension without
changing AgentScope's storage interfaces or trusting request payloads.
"""

from __future__ import annotations


def tenant_scoped_key(prefix: str, suffix: str) -> str:
    """Return a tenant-scoped key when a verified tenant is bound.

    Local-auth requests keep the legacy key shape for compatibility.  Logto
    requests bind the tenant before application services run, so their data is
    stored below ``<prefix>:tenant:<tenant_id>:<suffix>``.
    """

    normalized_prefix = prefix.strip().rstrip(":")
    normalized_suffix = suffix.strip().lstrip(":")
    try:
        from identity.dependencies import get_bound_tenant_id
    except ModuleNotFoundError:  # pragma: no cover - package import mode
        from examples.agent_service.identity.dependencies import (
            get_bound_tenant_id,
        )

    tenant_id = get_bound_tenant_id()
    if tenant_id is None:
        return f"{normalized_prefix}:{normalized_suffix}"
    return f"{normalized_prefix}:tenant:{tenant_id}:{normalized_suffix}"


__all__ = ["tenant_scoped_key"]
