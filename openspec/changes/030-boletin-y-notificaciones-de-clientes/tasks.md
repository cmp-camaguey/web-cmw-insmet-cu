# 030-boletin-y-notificaciones-de-clientes — tareas

> PR A (correo) = T1–T9. PR B (in-app) = T10–T13, y solo empieza cuando A está
> mergeado. Cada tarea deja sus pruebas con el código, no en una tarea aparte.

## PR A — boletín y suscripción (correo)

- [x] **T1. Modelos y migración.** `Subscriber`, `Subscription`, `Newsletter` y
      `EmailRecipientList.is_default` en `apps/core/models.py`; migración en
      cuatro pasos con migración de datos desde `EmailRecipient` y borrado del
      modelo viejo. `default_permissions = ()` + los cuatro permisos custom.
      *Verificación:* `makemigrations --check --dry-run` en verde; `migrate`
      sobre base vacía y sobre base con filas.

- [x] **T2. Admin.** Registrar los tres modelos, con `is_default` editable y
      solo una lista marcada. Quitar `EmailRecipient` del admin.
      *Verificación:* `manage.py check`; prueba de que marcar una segunda lista
      como principal falla.

- [x] **T3. Servicio de consentimiento.** `apps/core/subscriptions.py`
      (`subscribe`, `confirm`, `unsubscribe`, `set_consent`,
      `move_subscription`). Ninguna otra capa escribe los modelos.
      *Verificación:* pruebas de alta, confirmación, token de un solo uso,
      caducidad, baja idempotente y movimiento entre direcciones.

- [x] **T4. Signal del perfil y cambio de correo.** `apps/user_auth/signals.py`
      usa el servicio; `ProfileForm.save` mueve la suscripción. Se elimina la
      búsqueda por `name='newsletter'`.
      *Verificación:* marcar/desmarcar la casilla, y cambiar el correo no deja
      fila con la dirección anterior.

- [x] **T4b. Audiencia automática y casilla de envío.** Se retira la FK
      `email_recipient_list` de `Warning` y `WeatherReport`. `mail_send` resuelve
      la lista principal sin intervención del operador y envía **un correo por
      destinatario** con su enlace de baja. `WarningForm`/`WeatherReportForm`
      ganan `send_email` (aviso nuevo marcado, aviso editado y pronóstico
      desmarcados) con el número de destinatarios en el texto de ayuda.
      *Verificación:* `apps.meteo.tests.test_send_email_toggle`,
      `apps.core.tests.test_audiences`, y las vistas sin la casilla no envían.

- [x] **T5. Política de audiencia.** `apps/core/audiences.py` y `mail_send`
      resolviendo por política; lista vacía deja `logger.warning` y aviso al
      admin en vez de `return` mudo.
      *Verificación:* prueba por cada tipo de aviso (warning, reporte) y del
      camino vacío.

- [x] **T6. Endpoint público.** `apps/home/views/boletin/` con suscribir (POST,
      CSRF, rate limit por IP, validación de servidor), confirmar y baja; URLs
      en `apps/home/urls.py`.
      *Verificación:* pruebas de validación, CSRF, límite por IP, token usado y
      respuesta que no filtra existencia.

- [x] **T7. Landing operativo.** `<form>` real en
      `apps/home/templates/pages/home/landing.html` con `action`, `method`,
      `name` y `{% csrf_token %}`; mensajes de éxito y error en la misma página.
      *Verificación:* `djlint . --reformat --check` y `djlint . --lint`.

- [x] **T8. (Descartada) Boletín como entidad.** El operador no crea ni elige
      boletines: lo que se envía son avisos, pronósticos y novedades, y la
      audiencia es automática (lista principal + externos). Se eliminaron el
      modelo `Newsletter`, sus vistas, formulario, comando y plantillas de
      correo; `core.0002` ya no crea la tabla. La creación manual quedó fuera de
      alcance por decisión del usuario.

- [x] **T9. Verificación y cierre del PR A.** `python manage.py check` sin
      problemas; `python manage.py test` completo en verde (1662 pruebas);
      `ruff check` y `ruff format --check` en verde; `djlint . --lint` 0
      errores; `makemigrations --check --dry-run` sin cambios.

## PR B — avisos in-app para clientes

- [ ] **T10. Capa de política.** `notify_audience` con audiencia declarada por
      `source`; `notify_superusers` pasa a ser un caso.
      *Verificación:* una fuente interna no alcanza a un no-superusuario.

- [ ] **T11. Fuentes de cliente.** Factura emitida y cambio de estado de
      suscripción, con `dedupe_key` estable.
      *Verificación:* el cliente ve su aviso; el admin sigue viendo el suyo.

- [ ] **T12. Preferencias.** `NotificationPreference` con opt-out por defecto y
      dos interruptores independientes (in-app / correo).
      *Verificación:* apagar in-app no apaga el correo y viceversa.

- [ ] **T13. Verificación y cierre del PR B.** Misma batería que T9 más las
      pruebas de aislamiento entre roles.
