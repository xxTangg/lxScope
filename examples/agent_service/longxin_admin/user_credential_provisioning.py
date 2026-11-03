# -*- coding: utf-8 -*-
"""Deployment-owned provider credential provisioning.

The product keeps provider credentials managed by the deployment instead of
exposing their secrets in the customer-facing credential UI.  Knowledge-base
workers resolve an embedding credential under the knowledge-base owner's
storage key, so each active account needs the deployment credential record.
"""
from __future__ import annotations

import os
from collections.abc import Iterable

from pydantic import SecretStr

from agentscope.app.storage import StorageBase
from agentscope.credential import OpenAICredential


def siliconflow_credential_from_env() -> OpenAICredential | None:
    """Build the configured SiliconFlow OpenAI-compatible credential.

    Returns ``None`` when SiliconFlow is not configured, which keeps local
    deployments that do not use this provider fully functional.
    """
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAICredential(
        id=os.getenv("SILICONFLOW_CREDENTIAL_ID", "siliconflow"),
        name="SiliconFlow",
        api_key=SecretStr(api_key),
        base_url=os.getenv(
            "SILICONFLOW_BASE_URL",
            "https://api.siliconflow.cn/v1",
        ),
    )


async def provision_siliconflow_credential(
    storage: StorageBase,
    user_ids: Iterable[str],
    *,
    tenant_ids: Iterable[str] = (),
) -> int:
    """Upsert the deployment credential for the supplied account ids.

    The credential API remains administrator-managed through
    ``AdminManagedCredentialPolicy``.  These owner-scoped records exist only
    for trusted model and knowledge-base runtime resolution.

    Returns:
        Number of account records updated.  Returns zero if SiliconFlow is
        not configured.
    """
    credential = siliconflow_credential_from_env()
    if credential is None:
        return 0

    count = 0
    tenant_upsert = getattr(storage, "upsert_tenant_credential", None)
    for tenant_id in set(tenant_ids):
        if not tenant_id:
            continue
        if callable(tenant_upsert):
            await tenant_upsert(tenant_id, credential)
        else:
            await storage.upsert_credential(tenant_id, credential)
        count += 1

    for user_id in set(user_ids):
        if not user_id:
            continue
        await storage.upsert_credential(user_id, credential)
        count += 1
    return count
