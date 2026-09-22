import unittest

from deploy.logto import migration


class FakeClient:
    def __init__(self):
        self.calls = []

    def paged_get(self, path, *, params=None):
        self.calls.append(("GET", path, params))
        if path == "/applications":
            return []
        if path == "/organizations/org_demo/users":
            return []
        return []

    def request(self, method, path, *, params=None, payload=None):
        self.calls.append((method, path, params, payload))
        if method == "POST" and path == "/applications":
            return {"id": "app_demo", "name": payload["name"], "oidcClientMetadata": payload["oidcClientMetadata"]}
        return {}


class LogtoMigrationTest(unittest.TestCase):
    def test_manifest_validation_and_deterministic_tenant_id(self):
        manifest = {
            "logto_tenant_id": "default",
            "browser_application": {
                "enabled": True,
                "public_url": "https://lxscope.example.com",
                "redirect_paths": ["/callback"],
                "post_logout_redirect_paths": ["/"],
            },
            "organizations": [{"code": "demo", "name": "Demo", "users": []}],
        }
        migration._validate_manifest(manifest)
        self.assertEqual(
            migration.tenant_uuid(manifest["organizations"][0]),
            migration.tenant_uuid(manifest["organizations"][0]),
        )

    def test_browser_application_is_created_with_redirects(self):
        client = FakeClient()
        application = migration.ensure_browser_application(
            client,
            {
                "enabled": True,
                "name": "lxScope Web",
                "public_url": "https://lxscope.example.com",
                "redirect_paths": ["/callback"],
                "post_logout_redirect_paths": ["/"],
            },
        )
        self.assertEqual(application["id"], "app_demo")
        post = next(call for call in client.calls if call[0:2] == ("POST", "/applications"))
        self.assertEqual(
            post[3]["oidcClientMetadata"]["redirectUris"],
            ["https://lxscope.example.com/callback"],
        )
        self.assertEqual(
            migration._merge_uris(
                ["https://lxscope.example.com/callback"],
                ["http://localhost:18200/callback"],
            ),
            [
                "http://localhost:18200/callback",
                "https://lxscope.example.com/callback",
            ],
        )

    def test_tenant_binding_sql_is_additive(self):
        sql = migration.build_tenant_bindings_sql(
            [{
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "code": "demo",
                "name": "Demo tenant",
                "external_org_id": "org_demo",
            }],
        )
        self.assertIn(
            "ON CONFLICT (identity_provider, external_org_id) WHERE external_org_id IS NOT NULL",
            sql,
        )
        self.assertIn("'org_demo'", sql)


if __name__ == "__main__":
    unittest.main()
