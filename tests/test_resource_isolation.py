"""Phase 3 resource isolation tests."""

from __future__ import annotations

import unittest

from examples.agent_service.resource_isolation import filter_visible_resources


class ResourceIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resources = [
            {"id": "kb-a", "scope": "tenant", "tenant_id": "tenant-a"},
            {"id": "kb-b", "scope": "tenant", "tenant_id": "tenant-b"},
            {"id": "skill-platform", "scope": "platform"},
            {"id": "skill-a", "scope": "tenant", "tenant_id": "tenant-a"},
            {"id": "skill-b", "scope": "tenant", "tenant_id": "tenant-b"},
            {"id": "mcp-a", "scope": "tenant", "tenant_id": "tenant-a"},
            {"id": "mcp-b", "scope": "tenant", "tenant_id": "tenant-b"},
        ]

    def test_tenant_a_cannot_see_b_knowledge_skill_or_mcp(self) -> None:
        visible = {
            resource["id"]
            for resource in filter_visible_resources(
                self.resources,
                tenant_id="tenant-a",
                membership_id="membership-a",
            )
        }
        self.assertIn("kb-a", visible)
        self.assertIn("skill-a", visible)
        self.assertIn("mcp-a", visible)
        self.assertIn("skill-platform", visible)
        self.assertNotIn("kb-b", visible)
        self.assertNotIn("skill-b", visible)
        self.assertNotIn("mcp-b", visible)

    def test_personal_resources_are_membership_scoped(self) -> None:
        resources = [
            {"id": "personal-a", "scope": "personal", "owner_membership_id": "membership-a"},
            {"id": "personal-b", "scope": "personal", "owner_membership_id": "membership-b"},
        ]
        visible = filter_visible_resources(
            resources,
            tenant_id="tenant-a",
            membership_id="membership-a",
        )
        self.assertEqual(["personal-a"], [resource["id"] for resource in visible])


if __name__ == "__main__":
    unittest.main()
