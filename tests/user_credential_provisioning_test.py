# -*- coding: utf-8 -*-
"""Tests for the deployment-owned end-user credential provisioner."""
import os
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from examples.agent_service.longxin_admin.user_credential_provisioning import (
    provision_siliconflow_credential,
)


class _Storage:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}

    async def upsert_credential(self, user_id: str, credential: object) -> str:
        self.records[user_id] = credential
        return credential.id  # type: ignore[attr-defined]


class UserCredentialProvisioningTest(IsolatedAsyncioTestCase):
    """The deployment key is available under every active user's key."""

    async def test_provisions_each_distinct_user(self) -> None:
        storage = _Storage()
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test-key"}, clear=False):
            count = await provision_siliconflow_credential(
                storage, ["admin", "customer", "customer", ""]
            )

        self.assertEqual(count, 2)
        self.assertEqual(set(storage.records), {"admin", "customer"})
        self.assertEqual(storage.records["customer"].id, "siliconflow")  # type: ignore[attr-defined]

    async def test_skips_provisioning_without_a_key(self) -> None:
        storage = _Storage()
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": ""}, clear=False):
            count = await provision_siliconflow_credential(storage, ["customer"])

        self.assertEqual(count, 0)
        self.assertEqual(storage.records, {})
