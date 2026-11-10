"""Operator recovery of one existing effect; never starts or retries inference."""
import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from advisor.errors import AppError, ResponseLookupError
from advisor.integrations import prompt_project, response_client, read_response
from advisor.pipeline import apply_grounding_result
from advisor.recovery import mark_lookup_failure
from advisor.repository import AzureStore


def reason_text(value):
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 500:
        raise ValueError('Describe el motivo del operador (8–500 caracteres).')
    return value.strip()


def record_observed_failure(backend, job_id, observation, source_hash, reason):
    reason = reason_text(reason)
    job, etag = backend.get('jobs', job_id)
    if not job or job.get('grounding_api') != 'responses' or job.get('effect') != job_id + ':grounding':
        raise ValueError('No es una operación Responses de grounding conservada.')
    if (observation.get('job') != job_id or observation.get('response_id') != job.get('response_id') or
            observation.get('effect') != job.get('effect') or not any(
                row.get('status') == 404 and ((row.get('operation') == 'responses.retrieve' and row.get('same_project') is True) or
                    row.get('action') == 'GET existing Foundry response only, same own QA project')
                for row in observation.get('observed', []) if isinstance(row, dict))):
        raise ValueError('La observación404 no coincide con el ID y efecto originales.')
    if job.get('recovery', {}).get('observation', {}).get('source_sha256') == source_hash:
        return job
    if job.get('stage') not in ('grounding_running', 'uncertain', 'recovery_required') or job.get('grounding'):
        raise ValueError('El estado cambió o ya tiene resultado; conserva esa evidencia.')
    before, _ = backend.ledger()
    if before.get('active_job') != job_id or not any(r['id'] == job['effect'] for r in before['reservations']):
        raise ValueError('La operación no conserva su bloqueo y reserva; concilia antes de escribir.')
    result = mark_lookup_failure(job, {'provider_status': 404, 'error_type': 'NotFoundError',
        'request_id': None}, observation={'source_sha256': source_hash, 'reason': reason,
                                        'kind': 'existing_recorded_read_error'})
    backend.save_job(result, etag)
    after, _ = backend.ledger()
    if after != before:
        raise ValueError('El ledger cambió concurrentemente; no se ha restablecido ni liberado.')
    return result


def recheck_existing(backend, job_id, reason):
    reason = reason_text(reason)
    job, etag = backend.get('jobs', job_id)
    if not job or not job.get('recovery', {}).get('blocked') or job.get('effect') != job_id + ':grounding':
        raise ValueError('No hay una recuperación bloqueada de este grounding.')
    ledger, _ = backend.ledger()
    if ledger.get('active_job') != job_id:
        raise ValueError('El bloqueo original no coincide.')
    try:
        # Explicit operator-only ONE read after a material provider/context change.
        # No loops, inference, reserve(), cancellation or automatic retry here.
        result = read_response(None if job.get('response_snapshot') else response_client(prompt_project()), job)
    except ResponseLookupError as exc:
        job = mark_lookup_failure(job, exc.diagnostic, observation={'kind': 'explicit_operator_recheck', 'reason': reason})
        backend.save_job(job, etag)
        raise
    except AppError as exc:
        if exc.code != 'grounding_incomplete':
            raise
        job.update(stage='grounding_failed', error={'code': exc.code, 'message': str(exc)})
        job.pop('recovery', None)
        backend.save_job(job, etag)
        backend.release(job_id)
        return job
    if result is None:
        job.update(stage='grounding_running')
        job.pop('recovery', None);job.pop('error', None)
        backend.save_job(job, etag)
        return job
    job = apply_grounding_result(job, result)
    backend.save_job(job, etag)
    backend.release(job_id)
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='Nombre público del Storage propio autorizado.')
    parser.add_argument('--job', required=True)
    parser.add_argument('--reason', required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--record-observed-failure', type=Path)
    action.add_argument('--recheck', action='store_true')
    parser.add_argument('--project-endpoint')
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9]{3,24}', args.account):
        raise ValueError('Cuenta Storage no válida.')
    job_id = str(uuid.UUID(args.job))
    os.environ['AZURE_STORAGE_ACCOUNT'] = args.account
    os.environ['AZURE_CREDENTIAL_MODE'] = 'cli'
    os.environ['AZURE_DEV_USER_AGENT'] = 'microsoft_foundry_skill'
    backend = AzureStore()
    if args.record_observed_failure:
        data = args.record_observed_failure.read_bytes()
        if len(data) > 65536:
            raise ValueError('La observación supera64KiB.')
        job = record_observed_failure(backend, job_id, json.loads(data), hashlib.sha256(data).hexdigest(), args.reason)
    else:
        parsed = urlsplit(args.project_endpoint or '')
        if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.services.ai.azure.com') or
                not re.fullmatch('/api/projects/[A-Za-z0-9_-]+', parsed.path) or
                parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError('Usa el endpoint HTTPS público del proyecto original.')
        os.environ['AZURE_AI_PROJECT_ENDPOINT'] = args.project_endpoint
        job = recheck_existing(backend, job_id, args.reason)
    print(json.dumps({'id': job['id'], 'stage': job['stage'], 'response_id': job.get('response_id'),
                      'effect': job.get('effect'), 'inferenceSent': False, 'reservationsReleased': False}))


if __name__ == '__main__':
    try:
        main()
    except AppError as exc:
        print(json.dumps({'code': exc.code, 'error': str(exc), 'inferenceSent': False}));sys.exit(1)
    except Exception as exc:
        print(json.dumps({'errorType': type(exc).__name__, 'error': 'No se ha confirmado la conciliación; conserva IDs y reservas.'}));sys.exit(1)
