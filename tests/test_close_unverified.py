"""Development-only closure regressions; no Azure, provider requests or QA claim."""
import copy
import json
from unittest.mock import patch
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from advisor import pipeline
from advisor.closure import state_token
from advisor.errors import AppError
from advisor.reporting import report_payload
from advisor.repository import AzureStore
from tests.test_advisor import fixture
from tests.test_response_recovery import pending_job
from tests.test_readable_report import ReadableConclusion
from scripts.client import main


KEY = "d98dc9e3-bec7-4d07-b1b2-bcc6a4bc4d12"
REASON = "La respuesta no está disponible; continuaré con otro análisis."


def setup(stage="recovery_required"):
    job = pending_job()
    job.update(stage=stage, recovery={"blocked": True, "provider_status": 404},
               error={"code": "grounding_response_unavailable", "message": "Diagnóstico anterior"})
    backend, state = fixture(job)
    state["ledger"] = {"active_job": job["id"], "model_calls": 22, "searches": 12,
                       "reservations": [{"id": job["effect"], "models": 5, "searches": 3}],
                       "settlements": [{"id": "preserved-history"}]}
    backend.ledger.side_effect = lambda: (copy.deepcopy(state["ledger"]), "ledger-etag")
    def put(partition, key, value, etag):
        state["ledger"] = copy.deepcopy(value)
    backend.put.side_effect = put
    backend.release.side_effect = lambda job_id: AzureStore.release(backend, job_id)
    return job, backend, state


class CloseWithoutVerification(SimpleTestCase):
    def close(self, job, backend, **overrides):
        data = dict(reason=REASON, confirmed=True, expected_state=state_token(job), key=KEY)
        data.update(overrides)
        with patch("advisor.pipeline.store", return_value=backend), patch("advisor.pipeline.response_client", side_effect=AssertionError("No provider access")):
            return pipeline.close_unverified("owner", job["id"], **data)

    def test_unknown_result_is_closed_without_losing_identity_or_consumption(self):
        job, backend, state = setup()
        before = copy.deepcopy(state["ledger"])
        result = self.close(job, backend)
        self.assertEqual(result["stage"], "closed_unverified")
        for name in ("id", "effect", "response_id", "conversation_id", "sources", "original", "reviewed", "decision", "error"):
            self.assertEqual(result.get(name), job.get(name))
        self.assertEqual(result["unverified_closure"]["provider_outcome"], "unknown")
        self.assertEqual(result["unverified_closure"]["retained_reservation_ids"], [job["effect"]])
        self.assertFalse(result["recovery"]["blocked"])
        before["active_job"] = None
        self.assertEqual(state["ledger"], before)
        backend.reserve.assert_not_called()

    def test_sources_only_can_close_then_another_id_can_acquire_its_own_lock(self):
        job, backend, state = setup("sources_ready")
        for field in ("effect", "response_id", "conversation_id", "recovery", "error"):
            job.pop(field, None)
        state["job"] = copy.deepcopy(job)
        result = self.close(job, backend)
        self.assertEqual(result["unverified_closure"]["provider_outcome"], "not_started")
        AzureStore.acquire(backend, "new-analysis-id")
        self.assertEqual(state["ledger"]["active_job"], "new-analysis-id")
        self.assertEqual(state["job"]["stage"], "closed_unverified")

    def test_confirmation_reason_and_current_state_are_required_before_writes(self):
        for override, code in (({"confirmed": False}, "closure_confirmation"),
                               ({"confirmed": "true"}, "closure_confirmation"),
                               ({"reason": ""}, "closure_reason"),
                               ({"expected_state": "stale"}, "concurrent_change"),
                               ({"expected_state": "á" * 64}, "concurrent_change")):
            with self.subTest(code=code, override=override):
                job, backend, state = setup()
                before = copy.deepcopy(state)
                with self.assertRaises(AppError) as caught:
                    self.close(job, backend, **override)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(state, before)
                backend.save_job.assert_not_called(); backend.release.assert_not_called()

    def test_known_running_and_complete_states_cannot_be_closed_by_this_action(self):
        for stage in ("fetching_sources", "grounding_running", "grounding_sending", "decision_sending", "complete"):
            with self.subTest(stage=stage):
                job, backend, state = setup(stage)
                with self.assertRaises(AppError) as caught:
                    self.close(job, backend)
                self.assertEqual(caught.exception.code, "stage_locked")
                backend.save_job.assert_not_called(); backend.release.assert_not_called()

    def test_same_request_is_idempotent_but_another_closure_does_not_rewrite_history(self):
        job, backend, state = setup()
        first = copy.deepcopy(self.close(job, backend))
        again = self.close(job, backend)
        self.assertEqual(first, again)
        backend.save_job.assert_called_once()
        with self.assertRaises(AppError) as caught:
            self.close(job, backend, key="ca92e606-dc00-4277-b571-ea62afde767b")
        self.assertEqual(caught.exception.code, "already_closed")

    def test_compare_and_swap_failure_does_not_release_the_active_lock(self):
        job, backend, state = setup()
        before = copy.deepcopy(state)
        backend.save_job.side_effect = AppError("concurrent_change", "Concurrent update")
        with self.assertRaises(AppError):
            self.close(job, backend)
        self.assertEqual(state, before)
        backend.release.assert_not_called()

    def test_retry_finishes_lock_release_after_job_commit_without_a_second_save(self):
        job, backend, state = setup()
        original_put = backend.put.side_effect
        backend.put.side_effect = AppError("azure_unavailable", "Unconfirmed release", 503)
        with self.assertRaises(AppError):
            self.close(job, backend)
        self.assertEqual(state["job"]["stage"], "closed_unverified")
        self.assertEqual(state["ledger"]["active_job"], job["id"])
        backend.put.side_effect = original_put
        self.close(job, backend)
        self.assertIsNone(state["ledger"]["active_job"])
        backend.save_job.assert_called_once()

    def test_closing_an_old_report_never_releases_another_jobs_lock(self):
        job, backend, state = setup("awaiting_review")
        state["ledger"]["active_job"] = "other-active-job"
        before = copy.deepcopy(state["ledger"])
        self.close(job, backend)
        self.assertEqual(state["ledger"], before)
        backend.put.assert_not_called()

    def test_closed_report_is_explicitly_not_verified_in_html_and_json(self):
        job, backend, state = setup()
        closed = self.close(job, backend)
        payload = report_payload(closed)
        self.assertFalse(payload["verification"]["verified"])
        self.assertEqual(payload["conclusion"]["code"], "insufficient")
        with patch("advisor.auth.owner_for", return_value="owner"), patch("advisor.views.store", return_value=backend):
            response = self.client.get("/reports/" + job["id"] + "/")
            self.assertContains(response, "Cerrado sin verificar")
            self.assertContains(response, "Sin confirmar")
            self.assertContains(response, "Preparar otro análisis")
            download = self.client.get("/reports/" + job["id"] + "/?download=1")
            self.assertContains(download, "Cerrado sin verificar")
            self.assertNotContains(download, "close-unverified-form")
            self.assertNotContains(download, "csrfmiddlewaretoken")
            result = self.client.get("/api/reports/" + job["id"] + "/").json()
            self.assertEqual(result["verification"]["provider_outcome"], "unknown")

    def test_closed_legacy_disposition_remains_readable_and_is_not_rewritten(self):
        job, backend, state = setup("closed_unrecoverable")
        job["operator_disposition"] = {"provider_outcome": "unknown", "reservation_retained_in_full": True}
        state["job"] = copy.deepcopy(job)
        result = self.close(job, backend)
        self.assertEqual(result, job)
        self.assertFalse(report_payload(result)["verification"]["verified"])
        backend.save_job.assert_not_called()

    def test_closure_keeps_prior_evidence_and_decision_but_never_their_actionability(self):
        job, backend, state = setup("reviewed")
        prior = ReadableConclusion().job()
        for field in ("sources", "original", "reviewed", "decision", "comparison", "decision_stale", "changes"):
            job[field] = copy.deepcopy(prior[field])
        state["job"] = copy.deepcopy(job)
        self.assertEqual(report_payload(job)["conclusion"]["code"], "indicated")
        result = self.close(job, backend)
        for field in ("sources", "original", "reviewed", "decision", "comparison", "decision_stale", "changes"):
            self.assertEqual(result[field], prior[field])
        payload = report_payload(result)
        self.assertEqual(payload["conclusion"]["code"], "insufficient")
        self.assertIs(payload["verification"]["actionable"], False)
        with patch("advisor.auth.owner_for", return_value="owner"), patch("advisor.views.store", return_value=backend):
            response = self.client.get("/reports/" + job["id"] + "/")
            self.assertContains(response, "Decisión histórica conservada")
            self.assertContains(response, "Urgencia no accionable")
            self.assertNotContains(response, 'id="review-form"')
            self.assertNotContains(response, "data-confirm=")
            self.assertNotContains(response, 'data-action="decide"')
        with patch("advisor.pipeline.store", return_value=backend):
            with self.assertRaises(AppError):
                pipeline.review("owner", job["id"], prior["reviewed"], REASON, job["review_version"])
            with self.assertRaises(AppError):
                pipeline.decide("owner", job["id"])

    def test_http_action_requires_owner_confirmation_and_returns_safe_errors(self):
        job, backend, state = setup()
        data = {"reason": REASON, "confirmed": False, "state_token": state_token(job), "idempotency_key": KEY}
        with patch("advisor.auth.owner_for", return_value="owner"), patch("advisor.pipeline.store", return_value=backend):
            response = self.client.post("/api/jobs/" + job["id"] + "/close-unverified/", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "closure_confirmation")
        with patch("advisor.auth.owner_for", return_value=None):
            unauthenticated = self.client.post("/api/jobs/" + job["id"] + "/close-unverified/", data=json.dumps(data), content_type="application/json")
        self.assertEqual(unauthenticated.status_code, 401)

    def test_other_owner_cannot_close_a_report(self):
        job, backend, state = setup()
        backend.get.return_value = (copy.deepcopy(job), "etag")
        backend.job.side_effect = lambda job_id, owner: AzureStore.job(backend, job_id, owner)
        data = {"reason": REASON, "confirmed": True, "state_token": state_token(job), "idempotency_key": KEY}
        with patch("advisor.auth.owner_for", return_value="someone-else"), patch("advisor.pipeline.store", return_value=backend):
            response = self.client.post("/api/jobs/" + job["id"] + "/close-unverified/", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 404)
        backend.save_job.assert_not_called();backend.release.assert_not_called()


class ClosureClient(SimpleTestCase):
    def test_new_cli_session_resumes_a_persisted_close_before_continuation(self):
        client = MagicMock()
        client.request.side_effect = [
            {"verification": {"closed": True, "state_token": "development-state"},
             "unverified_closure": {"id": KEY, "reason": REASON}}, {"stage": "closed_unverified"}]
        inputs = iter(["owner", "close-unverified", "e48c8f34-7aa2-48ce-a42d-98a1894fa5a7", "quit"])
        with patch("scripts.client.Client", return_value=client), patch("sys.argv", ["client.py", "--url", "https://example.invalid"]), patch(
                "builtins.input", side_effect=lambda _: next(inputs)), patch("getpass.getpass", return_value="development-only"), patch("builtins.print"):
            main()
        posted = client.request.call_args_list[1]
        self.assertEqual(posted.args[1]["idempotency_key"], KEY)
        self.assertEqual(posted.args[1]["reason"], REASON)
        self.assertIs(posted.args[1]["confirmed"], True)

    def test_unconfirmed_cli_close_does_not_send_a_mutation(self):
        client = MagicMock()
        client.request.return_value = {"verification": {"closed": False, "state_token": "development-state"}}
        inputs = iter(["owner", "close-unverified", "e48c8f34-7aa2-48ce-a42d-98a1894fa5a7", REASON, "NO", "quit"])
        with patch("scripts.client.Client", return_value=client), patch("sys.argv", ["client.py", "--url", "https://example.invalid"]), patch(
                "builtins.input", side_effect=lambda _: next(inputs)), patch("getpass.getpass", return_value="development-only"), patch("builtins.print"):
            main()
        self.assertEqual([c.args[0] for c in client.request.call_args_list], ["/api/reports/e48c8f34-7aa2-48ce-a42d-98a1894fa5a7/"])

    def test_cli_retries_only_the_same_closure_after_unconfirmed_release(self):
        client = MagicMock()
        client.request.side_effect = [
            {"verification": {"closed": False, "state_token": "development-state"}},
            RuntimeError("HTTP 503 Consulta show antes de repetir la operación."),
            {"verification": {"closed": True}}, {"stage": "closed_unverified"}]
        inputs = iter(["owner", "close-unverified", "e48c8f34-7aa2-48ce-a42d-98a1894fa5a7", REASON, "CERRAR SIN VERIFICAR", "close-unverified", "quit"])
        with patch("scripts.client.Client", return_value=client), patch("sys.argv", ["client.py", "--url", "https://example.invalid"]), patch(
                "builtins.input", side_effect=lambda _: next(inputs)), patch("getpass.getpass", return_value="development-only"), patch("builtins.print"):
            main()
        mutations = [c for c in client.request.call_args_list if len(c.args) == 2]
        self.assertEqual(len(mutations), 2)
        self.assertEqual(mutations[0].args, mutations[1].args)
        self.assertIn("/close-unverified/", mutations[0].args[0])
