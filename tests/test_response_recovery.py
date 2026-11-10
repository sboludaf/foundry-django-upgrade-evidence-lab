"""Development regressions for D2; no network, inference or independent QA."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx2
from django.test import SimpleTestCase
from openai import APIConnectionError, NotFoundError

from advisor import pipeline
from advisor.errors import AppError, ResponseLookupError
from advisor.integrations import read_response, response_snapshot
from scripts.recover import record_observed_failure
from tests.test_advisor import EVIDENCE, RecoveryRules, ResponsesRecovery, fixture


def pending_job():
    job = RecoveryRules().job()
    job.update(stage='grounding_running', grounding_api='responses', response_id='test-response',
        conversation_id='test-conversation', review_version=0, decision=None, reviewed=None, original=None,
        effect=job['id'] + ':grounding')
    job.pop('grounding', None)
    return job


def missing_client():
    client = MagicMock()
    response = httpx2.Response(404, request=httpx2.Request('GET', 'https://example.invalid/responses/test-response'),
                              headers={'x-request-id': 'trace-test-only'})
    client.responses.retrieve.side_effect = NotFoundError('TEST_ONLY_SECRET_BODY', response=response,
                                                         body={'unsafe': 'TEST_ONLY_SECRET_BODY'})
    return client


class ResponseReadFailures(SimpleTestCase):
    def test_not_found_maps_to_safe_known_error(self):
        with self.assertRaises(ResponseLookupError) as caught:
            read_response(missing_client(), pending_job())
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.diagnostic['provider_status'], 404)
        self.assertEqual(caught.exception.diagnostic['request_id'], 'trace-test-only')
        self.assertNotIn('TEST_ONLY_SECRET_BODY', str(caught.exception))
        self.assertNotIn('TEST_ONLY_SECRET_BODY', json.dumps(caught.exception.diagnostic))

    def test_connection_error_is_uncertainty_not_terminal_inference(self):
        client = MagicMock()
        client.responses.retrieve.side_effect = APIConnectionError(request=httpx2.Request('GET', 'https://example.invalid/'))
        with self.assertRaises(ResponseLookupError) as caught:
            read_response(client, pending_job())
        self.assertEqual(caught.exception.status, 503)

    def test_context_drift_does_not_query_another_project(self):
        job = pending_job();job['response_context'] = {'base_url': 'https://original.example.invalid/v1/'}
        client = MagicMock();client.base_url = 'https://other.example.invalid/v1/'
        with self.assertRaises(ResponseLookupError):read_response(client, job)
        client.responses.retrieve.assert_not_called()

    def test_missing_response_preserves_effect_lock_budget_and_ids(self):
        job = pending_job();backend, state = fixture(job)
        with patch('advisor.pipeline.store', return_value=backend), patch('advisor.pipeline.prompt_project'), patch(
                'advisor.pipeline.response_client', return_value=missing_client()), self.assertRaises(ResponseLookupError):
            pipeline.reconcile('owner', job['id'])
        saved = state['job']
        self.assertEqual(saved['stage'], 'recovery_required')
        self.assertTrue(saved['recovery']['blocked'])
        for field in ('id', 'effect', 'response_id', 'conversation_id', 'review_version', 'original'):
            self.assertEqual(saved[field], job[field])
        backend.release.assert_not_called();backend.reserve.assert_not_called()

    def test_repeated_click_after_error_is_a_noop_for_provider_and_storage(self):
        job = pending_job();job.update(stage='recovery_required', recovery={'blocked': True},
                                      error={'message': 'Operator reconciliation is required.'})
        backend, _ = fixture(job)
        with patch('advisor.pipeline.store', return_value=backend), patch('advisor.pipeline.response_client') as client, self.assertRaises(AppError) as caught:
            pipeline.reconcile('owner', job['id'])
        self.assertEqual(caught.exception.code, 'grounding_recovery_required')
        client.assert_not_called();backend.save_job.assert_not_called()
        backend.release.assert_not_called();backend.reserve.assert_not_called()

    def test_real_exception_through_http_is_json409_not_html500(self):
        job = pending_job();backend, _ = fixture(job)
        with patch('advisor.auth.owner_for', return_value='owner'), patch('advisor.pipeline.store', return_value=backend), patch(
                'advisor.pipeline.prompt_project'), patch('advisor.pipeline.response_client', return_value=missing_client()):
            result = self.client.post('/api/jobs/' + job['id'] + '/reconcile/', data='{}', content_type='application/json')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()['code'], 'grounding_response_unavailable')
        self.assertNotIn('TEST_ONLY_SECRET_BODY', result.content.decode())


class TerminalResponseStorage(SimpleTestCase):
    def response(self):
        return ResponsesRecovery().client_for().responses.retrieve.return_value

    def test_terminal_cache_recovers_without_any_provider_request(self):
        job = pending_job();job['response_snapshot'] = response_snapshot(self.response(), 'test-response')
        backend, state = fixture(job)
        with patch('advisor.pipeline.store', return_value=backend), patch('advisor.pipeline.response_client', side_effect=AssertionError('unexpected provider client')):
            result = pipeline.reconcile('owner', job['id'])
        self.assertEqual(result['stage'], 'awaiting_review')
        self.assertEqual(result['original'], EVIDENCE)
        backend.reserve.assert_not_called();backend.release.assert_called_once_with(job['id'])

    def test_snapshot_rejects_other_response_id(self):
        with self.assertRaises(AppError):response_snapshot(self.response(), 'different-response')

    def test_snapshot_omits_internal_tool_urls(self):
        response = self.response();data = response.model_dump()
        data['output'][0]['action'] = {'requesturl': 'https://api.bing.microsoft.com/?key=TEST_ONLY_SECRET'}
        response.model_dump = lambda **kwargs: data
        snapshot = response_snapshot(response, 'test-response')
        self.assertNotIn('TEST_ONLY_SECRET', json.dumps(snapshot))

    def test_ground_consumes_terminal_event_instead_of_stopping_at_created(self):
        job = pending_job();job.update(stage='sources_ready');job.pop('response_id')
        backend, state = fixture(job)
        agent = SimpleNamespace(id='agent:test', name='test-agent', version='1')
        client = MagicMock();client.base_url = 'https://project.services.ai.azure.com/api/projects/test/openai/v1/'
        client.conversations.create.return_value = SimpleNamespace(id='test-conversation')
        client.responses.create.return_value.__enter__.return_value = iter([
            SimpleNamespace(type='response.created', response=SimpleNamespace(id='test-response')),
            SimpleNamespace(type='response.completed', response=self.response())])
        with patch('advisor.pipeline.store', return_value=backend), patch('advisor.pipeline.prompt_grounding_tool'), patch(
                'advisor.pipeline.prompt_project'), patch('advisor.pipeline.prompt_application_agent', return_value=agent), patch(
                'advisor.pipeline.response_client', return_value=client), patch.dict('os.environ', {'AZURE_AI_PROJECT_ENDPOINT': 'https://project.services.ai.azure.com/api/projects/test'}):
            result = pipeline.ground('owner', job['id'])
        self.assertEqual(result['response_snapshot']['status'], 'completed')
        self.assertEqual(result['response_id'], 'test-response')
        backend.reserve.assert_called_once();client.responses.create.assert_called_once()

    def test_stream_without_terminal_event_keeps_id_and_reservation(self):
        job = pending_job();job.update(stage='sources_ready');job.pop('response_id')
        backend, state = fixture(job)
        client = MagicMock();client.base_url = 'https://example.invalid/v1/'
        client.conversations.create.return_value = SimpleNamespace(id='test-conversation')
        client.responses.create.return_value.__enter__.return_value = iter([
            SimpleNamespace(type='response.created', response=SimpleNamespace(id='test-response'))])
        with patch('advisor.pipeline.store', return_value=backend), patch('advisor.pipeline.prompt_grounding_tool'), patch(
                'advisor.pipeline.prompt_project'), patch('advisor.pipeline.prompt_application_agent', return_value=SimpleNamespace(id='a', name='a', version='1')), patch(
                'advisor.pipeline.response_client', return_value=client), patch.dict('os.environ', {'AZURE_AI_PROJECT_ENDPOINT': 'https://example.invalid/project'}), self.assertRaises(AppError):
            pipeline.ground('owner', job['id'])
        self.assertEqual(state['job']['stage'], 'uncertain')
        self.assertEqual(state['job']['response_id'], 'test-response')
        backend.release.assert_not_called();backend.reserve.assert_called_once()


class RecordedObservationRecovery(SimpleTestCase):
    def observation(self, job):
        return {'job': job['id'], 'response_id': job['response_id'], 'effect': job['effect'], 'observed': [
            {'action': 'GET existing Foundry response only, same own QA project', 'status': 404}]}

    def test_recording_prior404_does_not_query_provider_or_change_ledger(self):
        job = pending_job();backend, state = fixture(job)
        backend.get.side_effect = lambda *args: backend.job(job['id'], 'owner')
        ledger = {'active_job': job['id'], 'model_calls': 5, 'searches': 3,
                  'reservations': [{'id': job['effect'], 'models': 5, 'searches': 3}]}
        backend.ledger.return_value = (copy.deepcopy(ledger), 'etag')
        with patch('scripts.recover.response_client', side_effect=AssertionError('unexpected provider query')):
            result = record_observed_failure(backend, job['id'], self.observation(job), 'test-only-hash', 'Recorded development observation')
            again = record_observed_failure(backend, job['id'], self.observation(job), 'test-only-hash', 'Recorded development observation')
        self.assertEqual(result['stage'], 'recovery_required')
        self.assertEqual(result, again)
        backend.save_job.assert_called_once();backend.reserve.assert_not_called();backend.release.assert_not_called()

    def test_other_response_cannot_be_used_as_recovery_proof(self):
        job = pending_job();backend, _ = fixture(job);backend.get.return_value = (job, 'etag')
        observation = self.observation(job);observation['response_id'] = 'other-response'
        with self.assertRaises(ValueError):record_observed_failure(backend, job['id'], observation, 'test-hash', 'Development observation')
        backend.save_job.assert_not_called()
