"""Development-only doubles. These are never product data or cloud acceptance."""
import copy
import json
import uuid
from unittest.mock import MagicMock, patch
from django.test import Client, SimpleTestCase
from advisor.domain import compare, validate_evidence, version, decision_warnings
from advisor.sources import release_intro
from advisor.errors import AppError
from advisor.repository import AzureStore
from advisor import pipeline

URL = "https://docs.djangoproject.com/test-only-fixture"
QUOTE = "Test-only CVE-2026-999999: Django 5.2 before 5.2.19 is affected."
SUPPORT = "Test-only: Django 5.2 is a supported branch."
DOCS = [{"url": URL, "excerpt": QUOTE + " " + SUPPORT}]
EVIDENCE = {"summary": "Unit-test fixture only", "issues": [
    {"cve": "CVE-2026-999999", "branches": [{"branch": "5.2", "affected_from": "5.2.0",
     "affected_to": "5.2.18", "fixed": "5.2.19", "source_url": URL, "quote": QUOTE}],
     "conditions": []}],
    "support": {"supported": True, "source_url": URL, "quote": SUPPORT}, "uncertainty": []}


def fixture(job):
    state = {"job": copy.deepcopy(job)}
    backend = MagicMock(spec=AzureStore)
    backend.job.side_effect = lambda *args: (copy.deepcopy(state["job"]), "etag")
    backend.save_job.side_effect = lambda value, etag: state.update(job=copy.deepcopy(value))
    return backend, state


class WebAccess(SimpleTestCase):
    def test_anonymous_is_denied_before_storage_or_model(self):
        with patch("advisor.auth.store", side_effect=AssertionError("unexpected backend")):
            self.assertEqual(self.client.get("/").status_code, 302)
            self.assertEqual(self.client.get("/api/reports/" + str(uuid.uuid4()) + "/").status_code, 401)
            self.assertEqual(self.client.post("/api/jobs/", data="{}", content_type="application/json").status_code, 401)

    def test_login_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/login/").status_code, 200)
        self.assertEqual(client.post("/login/", {"username": "test", "password": "not-a-credential"}).status_code, 403)

    def test_invalid_input_is_rejected_before_paid_effect(self):
        with patch("advisor.auth.owner_for", return_value="owner"), patch("advisor.pipeline.store", side_effect=AssertionError("unexpected backend")):
            for value in ("5.2", "5.2rc1", None, [], "5.2.19;curl"):
                response = self.client.post("/api/jobs/", data=json.dumps({"version": value, "idempotency_key": str(uuid.uuid4())}), content_type="application/json")
                self.assertEqual(response.status_code, 400)

    def test_health_does_not_claim_integration(self):
        self.assertEqual(self.client.get("/health/").json()["status"], "ok")
        self.assertIn("grounding_configured", self.client.get("/health/").json())


class EvidenceRules(SimpleTestCase):
    def test_release_body_selected_instead_of_duplicate_navigation_title(self):
        text = "Django 5.2.19 release notes | Navigation " + ("menu " * 200) + "Django 5.2.19 fixes a test-only issue in 5.2.18."
        self.assertIn("in 5.2.18", release_intro(text, "5.2.19"))
        with self.assertRaises(AppError):
            release_intro("Django 5.2.19 release notes | Navigation only", "5.2.19")

    def test_model_choice_cannot_hide_documented_applicability(self):
        comparison = compare("5.2.18", EVIDENCE)
        warnings = decision_warnings(comparison, {"answers": {"reason": {"choice": "no-upgrade-needed"}}})
        self.assertTrue(warnings)

    def test_cve_absent_from_release_notes_cannot_inherit_their_fix(self):
        e = copy.deepcopy(EVIDENCE)
        url = "https://docs.djangoproject.com/en/dev/releases/5.2.19/"
        e["issues"][0]["branches"][0]["source_url"] = url
        docs = [{"url": url, "excerpt": QUOTE, "cves": []}, DOCS[0]]
        with self.assertRaises(AppError) as exc:
            validate_evidence(e, docs)
        self.assertEqual(exc.exception.code, "unverified_branch")
    def test_numeric_patch_comparison_and_fix_boundary(self):
        e = validate_evidence(EVIDENCE, DOCS)
        self.assertEqual(compare("5.2.9", e)["issues"][0]["state"], "documented_affected")
        self.assertEqual(compare("5.2.18", e)["issues"][0]["state"], "documented_affected")
        self.assertEqual(compare("5.2.19", e)["issues"][0]["state"], "documented_fix")
        self.assertEqual(compare("6.0.9", e)["issues"][0]["state"], "unresolved")

    def test_no_inferred_lower_bound_or_environment_condition(self):
        e = copy.deepcopy(EVIDENCE)
        e["issues"][0]["branches"][0]["affected_from"] = None
        e["issues"][0]["conditions"] = [{"detail": "Test-only GDAL version unknown", "state": "unknown"}]
        self.assertEqual(compare("5.2.18", e)["issues"][0]["state"], "unresolved")
        self.assertFalse(compare("5.2.19", e)["actionable"])

    def test_critical_fields_need_exact_quote_and_same_branch(self):
        for field, value in (("fixed", "5.2.20"), ("fixed", "6.0.10"), ("branch", 5.2), ("quote", "invented quote not in batch")):
            e = copy.deepcopy(EVIDENCE)
            e["issues"][0]["branches"][0][field] = value
            with self.assertRaises(AppError):
                validate_evidence(e, DOCS)

    def test_non_boolean_support_rejected(self):
        e = copy.deepcopy(EVIDENCE)
        e["support"]["supported"] = 1
        with self.assertRaises(AppError):
            validate_evidence(e, DOCS)


class RecoveryRules(SimpleTestCase):
    def test_invalid_original_can_be_corrected_without_repeating_grounding(self):
        job = self.job()
        job.update(stage="grounding_running", run_id="test-only", thread_id="test-only", review_version=0, decision=None)
        backend, state = fixture(job)
        invalid = copy.deepcopy(EVIDENCE)
        invalid["issues"][0]["branches"][0]["fixed"] = "6.0.10"
        result = {"extraction": invalid, "searches": 1}
        with patch("advisor.pipeline.store", return_value=backend), patch("advisor.pipeline.agents"), patch("advisor.pipeline.read_grounding", return_value=result):
            pending = pipeline.reconcile("owner", job["id"])
            self.assertEqual(pending["stage"], "awaiting_review")
            self.assertFalse(pending["original_validation"]["valid"])
            repaired = pipeline.review("owner", job["id"], EVIDENCE, "Correct test-only branch attribution", 0)
            self.assertEqual(repaired["stage"], "reviewed")
            self.assertEqual(repaired["original"], invalid)
            backend.reserve.assert_not_called()

    def job(self):
        return {"id": str(uuid.uuid4()), "owner": "owner", "version": "5.2.18",
                "stage": "complete", "review_version": 1, "sources": DOCS,
                "original": copy.deepcopy(EVIDENCE), "reviewed": copy.deepcopy(EVIDENCE),
                "changes": [], "decision": {"response": "unit-test-only"}, "decision_stale": False,
                "comparison": compare("5.2.18", EVIDENCE), "batch_hash": "unit-test-only",
                "grounding": {"searches": 1}}

    def test_review_marks_decision_stale_without_new_call(self):
        job = self.job()
        backend, state = fixture(job)
        with patch("advisor.pipeline.store", return_value=backend), patch("advisor.pipeline.call_decision", side_effect=AssertionError("unexpected paid call")):
            result = pipeline.review("owner", job["id"], EVIDENCE, "Confirm test fixture only", 1)
            self.assertTrue(result["decision_stale"])
            self.assertEqual(result["original"], job["original"])
            self.assertEqual(result["review_version"], 2)
            backend.reserve.assert_not_called()

    def test_uncertain_decision_is_not_resent(self):
        job = self.job()
        job["stage"] = "reviewed"
        backend, state = fixture(job)
        with patch("advisor.pipeline.store", return_value=backend), patch("advisor.pipeline.decision_body", return_value=b"unit-test"), patch("advisor.pipeline.call_decision", side_effect=AppError("decision_uncertain", "unit-test timeout", 502)) as call:
            with self.assertRaises(AppError):
                pipeline.decide("owner", job["id"])
            self.assertEqual(state["job"]["stage"], "uncertain")
            with self.assertRaises(AppError):
                pipeline.decide("owner", job["id"])
            self.assertEqual(call.call_count, 1)
            backend.release.assert_not_called()

    def test_oversized_decision_input_is_rejected_before_reservation(self):
        job = self.job()
        job["stage"] = "reviewed"
        job["reviewed"]["uncertainty"] = ["x" * 500] * 8
        job["reviewed"]["summary"] = "x" * 1500
        backend, _ = fixture(job)
        with patch("advisor.pipeline.store", return_value=backend), patch.dict("os.environ", {"AZURE_AI_ENDPOINT": "https://unit-test.services.ai.azure.com", "AZURE_DECISION_DEPLOYMENT": "unit-test"}):
            with self.assertRaises(AppError) as exc:
                pipeline.decide("owner", job["id"])
            self.assertEqual(exc.exception.code, "decision_input_limit")
            backend.reserve.assert_not_called()

    def test_missing_bing_never_reserves_or_calls_model(self):
        job = self.job()
        job["stage"] = "sources_ready"
        backend, _ = fixture(job)
        with patch("advisor.pipeline.store", return_value=backend), patch.dict("os.environ", {"BING_CONNECTION_ID": ""}):
            with self.assertRaises(AppError) as error:
                pipeline.ground("owner", job["id"])
            self.assertEqual(error.exception.code, "grounding_unavailable")
            backend.reserve.assert_not_called()


class BingReferences(SimpleTestCase):
    def client_for(self, citations, searches=True, request_url=None):
        from types import SimpleNamespace
        client = MagicMock()
        client.runs.get.return_value = SimpleNamespace(
            status="completed", usage=SimpleNamespace(
                as_dict=lambda: {"prompt_tokens": 100, "completion_tokens": 50}))
        calls = [{"type": "bing_custom_search", "bing_custom_search": {
            "requesturl": request_url}}] if searches else []
        client.run_steps.list.return_value = [SimpleNamespace(
            as_dict=lambda: {"step_details": {"tool_calls": calls}})]
        payload = {"type": "text", "text": {
            "value": '{"summary":"Development fixture only"}',
            "annotations": [{"url_citation": cite} for cite in citations]}}
        client.messages.list.return_value = [SimpleNamespace(
            role="assistant", content=[SimpleNamespace(as_dict=lambda: payload)])]
        return client

    def read(self, client):
        from advisor.integrations import read_grounding
        return read_grounding(client, {"thread_id": "test-thread", "run_id": "test-run"})

    def test_custom_search_accepts_original_website_citation_without_query_link(self):
        url = "https://docs.djangoproject.com/en/dev/releases/5.2.19/#s-security"
        result = self.read(self.client_for([{"url": url, "title": "Django release notes"}]))
        self.assertEqual(result["citations"], [{"url": url, "title": "Django release notes"}])
        self.assertEqual(result["query_urls"], [])

    def test_internal_request_url_is_never_converted_to_a_public_citation(self):
        result = self.read(self.client_for(
            [{"url": URL, "title": "Development citation"}],
            request_url="https://api.bing.microsoft.com/search?q=fixture&key=TEST_ONLY_NOT_A_CREDENTIAL"))
        self.assertEqual(result["query_urls"], [])
        self.assertNotIn("TEST_ONLY_NOT_A_CREDENTIAL", json.dumps(result))

    def test_returned_query_reference_keeps_its_exact_url(self):
        query = "https://www.bing.com/search?q=Django%205.2%20release&form=TEST_ONLY"
        result = self.read(self.client_for([
            {"url": URL, "title": "Development citation"},
            {"url": query, "title": "Original returned query"}]))
        self.assertEqual(result["query_urls"], [query])
        self.assertIn({"url": query, "title": "Original returned query"}, result["citations"])

    def test_citations_cannot_substitute_for_actual_search_steps(self):
        with self.assertRaises(AppError) as error:
            self.read(self.client_for([{"url": URL}], searches=False))
        self.assertEqual(error.exception.code, "grounding_not_used")

    def test_query_only_output_cannot_substitute_for_official_website_citations(self):
        with self.assertRaises(AppError) as error:
            self.read(self.client_for([{"url": "https://www.bing.com/search?q=test"}]))
        self.assertEqual(error.exception.code, "website_citation_missing")
