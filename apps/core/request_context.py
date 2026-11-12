"""El request de la petición en curso, para quien no lo tiene a mano.

`log_action` acepta el request como parámetro opcional, pero 70 llamadores no lo
pasan: el operador actuaba desde una vista y la fila quedaba sin IP ni
User-Agent. Pedirle a cada vista que repita `request=self.request` no escala: la
próxima vista que se escriba lo olvida otra vez y la tabla de auditoría vuelve a
llenarse de guiones.

`ContextVar` y no `threading.local` porque el contexto viaja con la tarea
asíncrona y con el hilo: un `threading.local` se queda con el valor del hilo que
atendió la petición anterior cuando el servidor reutiliza el worker.
"""

from contextvars import ContextVar

_current_request = ContextVar('current_request', default=None)


def set_current_request(request):
    """Deja el request vigente y devuelve el token para restaurarlo."""
    return _current_request.set(request)


def reset_current_request(token):
    """Restaura el valor anterior. Se llama siempre, en un `finally`."""
    _current_request.reset(token)


def current_request():
    """El request en curso, o `None` fuera de una petición."""
    return _current_request.get()
