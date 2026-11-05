import json

from django.test import TestCase


class AdvisorTests(TestCase):
    def test_home_and_health_are_available(self):
        self.assertContains(self.client.get("/"), "Actualizar Django")
        self.assertEqual(self.client.get("/health/").json()["status"], "ok")

    def test_invalid_version_is_rejected_before_integration(self):
        response = self.client.post("/api/analyze/", data=json.dumps({"version": "5.2"}), content_type="application/json")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["status"], "integration_error")

    def test_unconfigured_azure_never_returns_a_fake_report(self):
        response = self.client.post("/api/analyze/", data=json.dumps({"version": "5.2.18"}), content_type="application/json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "needs_configuration")
        self.assertNotIn("report_id", response.json())

    def test_report_download_is_session_scoped(self):
        response = self.client.get("/api/reports/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(response.status_code, 404)
