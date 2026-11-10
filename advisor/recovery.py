"""Persist lookup uncertainty without changing effect identity, locks or budgets."""
import copy
import time


def mark_lookup_failure(job, diagnostic, *, observation=None):
    result = copy.deepcopy(job)
    result['uncertain_stage'] = result.get('uncertain_stage', result['stage'])
    result['stage'] = 'recovery_required'
    result['error'] = {'code': 'grounding_response_unavailable',
        'message': 'No se ha podido recuperar la respuesta de IA. Su resultado sigue incierto; '
                   'el ID, el bloqueo y la reserva se conservan. El operador debe conciliarla. '
                   'Recargar este informe no repite la consulta ni la búsqueda.'}
    recovery = dict(diagnostic, blocked=True, response_id=job['response_id'],
                    conversation_id=job.get('conversation_id'), effect=job.get('effect'),
                    checked_at=int(time.time()))
    if observation:
        recovery['observation'] = observation
    result['recovery'] = recovery
    result.setdefault('recovery_history', []).append(copy.deepcopy(recovery))
    return result
