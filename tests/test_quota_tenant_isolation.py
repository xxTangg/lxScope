"""Phase 4 tenant quota isolation tests."""

from __future__ import annotations

import unittest

from examples.agent_service.longxin_admin.tenant_quota import tenant_balance_after


class TenantQuotaIsolationTest(unittest.TestCase):
    def test_tenant_a_cannot_consume_tenant_b_balance(self) -> None:
        balances = {
            "tenant-a": 10_000,
            "tenant-b": 50_000,
        }

        balances["tenant-a"] = tenant_balance_after(balances["tenant-a"], -2_500)

        self.assertEqual(balances["tenant-a"], 7_500)
        self.assertEqual(balances["tenant-b"], 50_000)

    def test_tenant_a_insufficient_balance_does_not_mutate_tenant_b(self) -> None:
        balances = {
            "tenant-a": 10_000,
            "tenant-b": 50_000,
        }

        with self.assertRaises(Exception) as context:
            tenant_balance_after(balances["tenant-a"], -10_001)

        self.assertEqual(getattr(context.exception, "status_code", 409), 409)
        self.assertEqual(balances["tenant-b"], 50_000)


if __name__ == "__main__":
    unittest.main()
