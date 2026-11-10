class AppError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


class ResponseLookupError(AppError):
    """A failed read is not evidence that the inference failed or cost nothing."""
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        missing = diagnostic.get('provider_status') == 404
        message = ('Foundry no encuentra la respuesta conservada.' if missing else
                   'Foundry no confirmó la lectura de la respuesta conservada.')
        super().__init__('grounding_response_unavailable', message +
            ' El resultado sigue incierto. Se conservan el ID, el bloqueo y la reserva. '
            'El operador debe conciliar esta operación; no vuelvas a enviar la búsqueda.', 409 if missing else 503)
