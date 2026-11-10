"""Regression coverage for correction r6; development only, never Azure QA."""
import copy
import io
import json
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase
from advisor.domain import compare, validate_evidence
from advisor.errors import AppError
from advisor.extraction import source_proposal
from advisor.reporting import conclusion, report_payload
from scripts.client import Client, load_review
from tests.test_advisor import EVIDENCE, DOCS, fixture


class SourceExtraction(SimpleTestCase):
    def job(self):
        docs = json.loads((Path(__file__).parent / "fixtures/official-2026-11-09.json").read_text())
        return {"version": "5.2.18", "sources": docs}

    def test_actual_captured_notes_produce_five_exact_branches_and_whole_quotes(self):
        job = self.job()
        evidence = validate_evidence(source_proposal(job), job["sources"])
        self.assertEqual(sum(len(i["branches"]) for i in evidence["issues"]), 5)
        for issue in evidence["issues"]:
            for b in issue["branches"]:
                self.assertEqual(b["affected_from"], b["affected_to"])
                self.assertTrue(b["quote"].startswith("Django " + b["fixed"] + " fixes"))
                self.assertTrue(b["quote"].endswith("in " + b["affected_to"] + "."))
        self.assertEqual(evidence["issues"][1]["branches"][0]["branch"], "6.0")
        self.assertTrue(evidence["support"]["supported"])
        states = compare("5.2.18", evidence)["issues"]
        self.assertEqual([i["state"] for i in states], ["documented_affected", "unresolved", "documented_affected"])
        self.assertIn("Full protection requires GDAL", evidence["issues"][2]["conditions"][0]["quote"])

    def test_missing_or_truncated_sentence_never_becomes_a_patch_range(self):
        job = self.job()
        doc = next(d for d in job["sources"] if "/5.2.19/" in d["url"])
        doc["fragments"] = ["Django 5.2.19 fixes four security issues in 5.2."]
        self.assertFalse(any(b["branch"] == "5.2" for i in source_proposal(job)["issues"] for b in i["branches"]))

    def test_four_corpus_versions_keep_their_own_patch_boundary(self):
        for installed, expected, fixed in (("5.2.18", "documented_affected", "5.2.19"),
                                           ("5.2.19", "documented_fix", "5.2.19"),
                                           ("6.0.9", "documented_affected", "6.0.10"),
                                           ("6.0.10", "documented_fix", "6.0.10")):
            with self.subTest(version=installed):
                job = self.job(); job["version"] = installed
                evidence = validate_evidence(source_proposal(job), job["sources"])
                result = compare(installed, evidence)["issues"][0]
                self.assertEqual((result["state"], result["fixed"]), (expected, fixed))

    def test_context_supplies_exact_fields_without_truncating_conditions(self):
        from advisor.pipeline import grounding_context
        job = self.job(); job["batch_hash"] = "development-only"
        context = json.loads(grounding_context(job))
        doc = next(d for d in context["sources"] if "/5.2.19/" in d["url"])
        self.assertEqual(doc["documented_exact_patch"]["affected_from"], "5.2.18")
        self.assertNotIn("CVE-2026-113733", doc["documented_cves"])


class ReadableConclusion(SimpleTestCase):
    def job(self, installed="5.2.18", reason="security-fix"):
        return {"id": "ee3be8aa-68aa-4803-9630-c38443849415", "version": installed,
                "sources": DOCS, "stage": "complete", "review_version": 1, "changes": [],
                "original": copy.deepcopy(EVIDENCE), "reviewed": copy.deepcopy(EVIDENCE),
                "comparison": compare(installed, EVIDENCE), "decision_stale": False,
                "decision": {"response": {"answers": {"reason": {"choice": reason}}}, "actionable": True, "warnings": []}}

    def test_positive_and_negative_conclusions_keep_branch_and_reasons(self):
        result = conclusion(self.job())
        self.assertEqual(result["label"], "Actualización indicada")
        self.assertEqual(result["proposed_version"], "5.2.19")
        self.assertTrue(result["reasons"])
        fixed = conclusion(self.job("5.2.19", "no-upgrade-needed"))
        self.assertEqual(fixed["label"], "No indicada con la evidencia disponible")
        self.assertIsNone(fixed["proposed_version"])

    def test_unknown_or_stale_or_unreviewed_never_produces_a_final_recommendation(self):
        job = self.job()
        job["comparison"]["unresolved"] = ["GDAL desconocido"]
        self.assertEqual(conclusion(job)["code"], "insufficient")
        job = self.job();job["decision_stale"] = True
        self.assertEqual(conclusion(job)["code"], "insufficient")
        job = self.job();job["reviewed"] = None
        self.assertEqual(conclusion(job)["code"], "insufficient")

    def test_real_environment_observation_required_for_resolving_condition(self):
        evidence = copy.deepcopy(EVIDENCE)
        evidence["issues"][0]["conditions"] = [{"detail": "Development condition", "state": "not_met",
                    "source_url": DOCS[0]["url"], "quote": DOCS[0]["excerpt"]}]
        with self.assertRaises(AppError) as error:
            validate_evidence(evidence, DOCS)
        self.assertEqual(error.exception.code, "environment_evidence")
        evidence["issues"][0]["conditions"][0]["observation"] = "Development observation supplied explicitly"
        valid = validate_evidence(evidence, DOCS)
        self.assertEqual(compare("5.2.18", valid)["issues"][0]["state"], "documented_not_applicable")

    def test_missing_cve_cannot_be_removed_to_hide_uncertainty(self):
        from advisor import pipeline
        job = self.job(); job.update(stage="awaiting_review", owner="owner")
        job["sources"] = copy.deepcopy(DOCS)
        job["sources"][0]["cves"] = ["CVE-2026-113733"]
        backend, _ = fixture(job)
        with patch("advisor.pipeline.store", return_value=backend), self.assertRaises(AppError):
            pipeline.review("owner", job["id"], EVIDENCE, "Development-only confirmation", 1)
        backend.save_job.assert_not_called()

    def test_html_table_and_download_agree_with_json_without_mutations(self):
        job = self.job()
        backend, _ = fixture(job)
        with patch("advisor.auth.owner_for", return_value="owner"), patch("advisor.views.store", return_value=backend):
            response = self.client.get("/reports/" + job["id"] + "/")
            self.assertContains(response, "Actualización indicada")
            self.assertContains(response, "Confirmar aviso")
            self.assertNotContains(response, 'id="evidence"')
            download = self.client.get("/reports/" + job["id"] + "/?download=1")
            self.assertContains(download, "Actualización indicada")
            self.assertNotContains(download, "data-confirm=")
            self.assertNotContains(download, "csrfmiddlewaretoken")
            payload = self.client.get("/api/reports/" + job["id"] + "/").json()
            self.assertEqual(payload["conclusion"], conclusion(job))
            self.assertEqual(payload["original"], EVIDENCE)
        backend.save_job.assert_not_called(); backend.reserve.assert_not_called()


class TerminalReview(SimpleTestCase):
    def test_multiline_file_larger_than_terminal_canonical_limit_is_read_entirely(self):
        value = copy.deepcopy(EVIDENCE)
        value["summary"] = "Development-only " * 75
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "review.json"
            filename.write_text(json.dumps(value, indent=2), encoding="utf-8")
            self.assertGreater(filename.stat().st_size, 1024)
            self.assertEqual(load_review(str(filename)), value)

    def test_invalid_file_is_rejected_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "review.json"
            filename.write_text("[]")
            with self.assertRaises(ValueError): load_review(str(filename))

    def test_error_message_includes_safe_recovery_for_invalid_version(self):
        client = Client("https://example.invalid")
        error = urllib.error.HTTPError(client.url, 400, "Bad request", {}, io.BytesIO(
            json.dumps({"code": "invalid_input", "error": "Escribe una versión final Django X.Y.Z."}).encode()))
        with patch.object(client.http, "open", side_effect=error), self.assertRaises(RuntimeError) as caught:
            client.request("/api/jobs/")
        self.assertIn("X.Y.Z", str(caught.exception)); self.assertIn("Corrige la entrada", str(caught.exception))
