# 030-boletin-y-notificaciones-de-clientes — diseño

> Estado: **diseño aprobado**. Las nueve decisiones de `proposal.md` §6 quedaron
> cerradas el 2026-11-07. El cambio se parte en dos PRs encadenados: **A**
> (boletín, suscripción y correo) y **B** (avisos in-app para clientes). Este
> documento cubre A completo y define la capa de política que B consume.

## 1. Decisiones cerradas

| # | Pregunta de §6 | Decisión |
|---|---|---|
| 1 | ¿Doble opt-in en el landing? | **Sí.** Sin confirmar no se envía nada. |
| 2 | ¿Esquema de suscriptores? | **`Subscriber` (persona + consentimiento) y `Subscription` (pertenencia a lista), separados.** |
| 3 | ¿Qué pasa con las filas de `EmailRecipient` existentes? | Se migran como **confirmadas**, `source='admin'`, `confirmed_at=created_at`. |
| 4 | ¿Quién envía el boletín y cómo? | **Comando de gestión y pantalla** con vista previa y confirmación. Envío manual por edición; nada automático. |
| 5 | ¿Qué eventos generan aviso in-app a un cliente? | **Factura emitida** y **cambio de estado de su suscripción**. Nada interno. |
| 6 | ¿Puede el cliente apagar los avisos in-app? | Sí, y **son dos interruptores**: in-app y correo se apagan por separado. |
| 7 | ¿Baja de un suscriptor sin cuenta? | **`active=False`**, nunca borrar la fila. |
| 8 | ¿Límites de envío? | Lotes de **50** con pausa entre lotes, **ventana horaria laboral**, y si el SMTP rechaza se apoya en `send_email_task` + aviso al admin. |
| 9 | ¿El boletín es entidad propia? | **Sí**: asunto, cuerpo, estado borrador/enviado, historial de qué se mandó y cuándo. |
| 10 | ¿El operador elige lista en los formularios de avisos y pronósticos? | **No.** La audiencia es **una sola y automática**: los suscriptores confirmados y activos de la lista principal. Las direcciones externas (instituciones, prensa) se cargan en esa misma lista con `source='admin'`. Se retira la FK `email_recipient_list` de `Warning` y `WeatherReport`. |
| 11 | Si la audiencia es automática, ¿cuándo sale el correo? | **Acto explícito**: cada publicación tiene una casilla «Enviar por correo» con el número de destinatarios a la vista. Aviso nuevo → marcada; aviso editado → desmarcada; pronóstico (crear o editar) → desmarcada. Sin marcar no sale nada. Un envío automático en cada guardado sería spam. |
| 12 | ¿Un correo con todos en `to:` o uno por destinatario? | **Uno por destinatario.** Cada uno lleva su propio enlace de baja y ninguno ve la dirección de los demás. |

## 2. Modelo de datos

Todo vive en `apps/core/models.py`, junto a `EmailRecipientList`, porque el
consentimiento y las listas son infraestructura compartida por `meteo`,
`commercial` y `user_auth`.

### 2.1 `Subscriber` — la persona y su consentimiento

| Campo | Tipo | Notas |
|---|---|---|
| `uuid` | UUIDField pk | convención del proyecto |
| `email` | EmailField, **unique** | normalizado a minúsculas en `save()` |
| `created_at` | DateTimeField auto_now_add | alta |
| `source` | CharField(choices) | `landing` / `registro` / `perfil` / `admin` |
| `confirmed_at` | DateTimeField null | `null` = pendiente de confirmar |
| `confirmation_token` | UUIDField null, unique | un solo uso; se anula al confirmar |
| `confirmation_sent_at` | DateTimeField null | para caducidad y reenvío |
| `unsubscribe_token` | UUIDField unique, default uuid4 | permanente, sin caducidad |
| `active` | BooleanField default True | la baja lo pone en `False` |

`email` sigue siendo único, pero ahora **en la persona**, no en la pertenencia:
la misma dirección puede estar en varias listas sin duplicar la fila.

Regla: un `Subscriber` confirmado nunca se borra. `active=False` conserva a
quién se le escribió, que es justo lo que evita volver a importunarlo.

### 2.2 `Subscription` — pertenencia a una lista

| Campo | Tipo | Notas |
|---|---|---|
| `uuid` | UUIDField pk | |
| `subscriber` | FK `Subscriber`, `related_name='subscriptions'` | `on_delete=CASCADE` |
| `recipient_list` | FK `EmailRecipientList`, `related_name='subscriptions'` | `on_delete=CASCADE` |
| `created_at` | DateTimeField auto_now_add | |
| `source` | CharField(choices) | de dónde vino **esta** pertenencia |

`unique_together = ('subscriber', 'recipient_list')`. Quitar la pertenencia sí
borra la fila: el consentimiento de la persona sobrevive en `Subscriber`.

### 2.3 `EmailRecipientList.is_default`

Booleano con `UniqueConstraint(fields=['is_default'], condition=Q(is_default=True))`:
**como máximo una** lista puede ser la principal. Sustituye a
`EmailRecipientList.objects.get(name='newsletter')`, que se rompía en silencio al
renombrar la lista.

Es la **audiencia automática de todo**: avisos, pronósticos y boletín. El
operador no elige lista en ningún formulario (decisión 10). Una lista
secundaria existe para agrupaciones internas y **no** recibe envíos automáticos.

### 2.4 `Newsletter` — la edición enviada

| Campo | Tipo | Notas |
|---|---|---|
| `uuid` | UUIDField pk | |
| `subject` | CharField(255) | asunto del correo |
| `body` | TextField | HTML del cuerpo, renderizado en la plantilla base de correo |
| `status` | CharField(choices) | `draft` / `sending` / `sent` |
| `created_by` | FK `User` null | quién la redactó |
| `created_at` / `sent_at` | DateTimeField | |
| `recipients_count` | PositiveIntegerField default 0 | cuántos correos se encolaron |

Estados: `draft` se edita; `sending` está en curso (evita doble envío por doble
clic); `sent` es terminal. No hay borrador editable después de `sending`.

### 2.5 Migración de datos

`apps/core/migrations/0002_*.py` en cuatro pasos:

1. Crear `Subscriber`, `Subscription`, `Newsletter` y el campo `is_default`.
2. Para cada `EmailRecipient`: `Subscriber.objects.get_or_create(email=...)` con
   `confirmed_at=created_at`, `source='admin'`; y `Subscription` con la lista de
   la fila.
3. Marcar `is_default=True` en la lista cuyo `name == 'newsletter'`, si existe.
4. Borrar el modelo `EmailRecipient`.

Es reversible en la parte de esquema; el `reverse` recrea `EmailRecipient` desde
`Subscription` (una fila por pertenencia) y es aceptable porque no hay pérdida:
la dirección vive en `Subscriber`.

`apps/core/migrations/0003_ensure_default_list.py` cierra el hueco que dejó
`0002`: en una base sin listas, la audiencia quedaba vacía para siempre y el
perfil con `newsletter=True` no servía de nada. La migración crea la lista
principal si no existe y da de alta como confirmados a los perfiles que ya
habían aceptado.

### 2.6 Lo que se toca fuera de `core`

| Archivo | Cambio |
|---|---|
| `apps/user_auth/signals.py` | deja de escribir filas; llama a `subscriptions.set_consent(...)` |
| `apps/core/forms/email_recipients.py` | el formset escribe `Subscription` resolviendo el correo a `Subscriber` |
| `apps/core/utils.py` (`mail_send`) | resuelve audiencia por política, registra y avisa si queda vacía |
| `apps/core/admin.py` | registra los tres modelos nuevos; `EmailRecipient` desaparece |
| `apps/meteo/models.py` | se retira `email_recipient_list` de `Warning` y `WeatherReport` |
| `apps/meteo/forms/warning.py` | `send_email` marcado al crear, desmarcado al editar; fuera `has_changes` |
| `apps/meteo/forms/weather_report.py` | `send_email` siempre desmarcado |
| `apps/meteo/views/warning.py`, `weather_report.py` | el envío depende de `form.cleaned_data['send_email']` |
| `templates/emails/alert_notification.html` | pie con el enlace de baja individual |

## 3. Consentimiento — un solo camino

`apps/core/subscriptions.py` es la única puerta. Ninguna vista, signal o comando
escribe `Subscriber` ni `Subscription` por su cuenta.

```python
def subscribe(email, *, source, recipient_list=None): ...
def confirm(token): ...
def unsubscribe(token): ...
def set_consent(user, enabled, *, source): ...
def move_subscription(old_email, new_email): ...
```

`subscribe` crea o actualiza y devuelve `(subscriber, created)`; `confirm` confirma
y anula el token; `unsubscribe` pone `active=False` y es idempotente;
`set_consent` atiende al perfil y al registro; `move_subscription` traslada el
consentimiento al cambiar de correo.

### 3.1 Landing (doble opt-in)

1. `POST /boletin/suscribir/` valida el correo en servidor (no solo en el
   navegador) y llama a `subscribe(email, source='landing')`.
2. Si el suscriptor ya está confirmado, **no** se reenvía nada y la respuesta es
   la misma: «revise su correo». No se revela si la dirección existía (evita
   enumeración).
3. Si es nuevo o está pendiente, se genera `confirmation_token`, se encola el
   correo de confirmación y se guarda `confirmation_sent_at`.
4. `GET /boletin/confirmar/<token>/` confirma, anula el token y responde una
   página propia. Un token usado o vencido responde la misma página con el
   mensaje de caducidad, nunca un 404.

Caducidad del token: **72 h** (`NEWSLETTER_CONFIRMATION_TTL_HOURS`, configurable).
Reenviar el formulario con el mismo correo pendiente regenera el token y vuelve a
enviar (con el rate limit de §5 por delante).

### 3.2 Perfil y registro

`Profile.newsletter` sigue siendo la casilla que ve el usuario, pero el signal
deja de crear filas a mano:

- `newsletter=True` → `set_consent(user, True, source='perfil'|'registro')`.
  El consentimiento del perfil **no exige doble opt-in**: el usuario está
  autenticado y su correo ya es de confianza. `confirmed_at` se marca al crear.
- `newsletter=False` → quita la `Subscription` del boletín. `Subscriber` queda
  con su historial.

### 3.3 Cambio de correo

`ProfileForm.save` llama a `move_subscription(old_email, new_email)` después de
guardar el usuario: mueve la `Subscription` y actualiza `Subscriber.email`. No
queda fila con la dirección anterior, que es el defecto de §4.3 de la propuesta.

### 3.4 Baja

`GET /boletin/baja/<token>/` llama a `unsubscribe(token)`: `active=False`,
idempotente (segunda visita responde lo mismo), sin login. Todos los correos
masivos llevan el enlace, incluido el de confirmación.

## 4. Resolución de destinatarios

`apps/core/audiences.py`:

```python
def resolve_audience() -> list[Subscriber]
def audience_size() -> int
```

Sin parámetros y sin mapa por `source`: **hay una sola audiencia**. Devuelve los
`Subscriber` con `active=True`, `confirmed_at__isnull=False` y pertenencia
(`Subscription`) a la lista con `is_default=True`, ordenados por `email`.

Devuelve objetos `Subscriber` y no direcciones porque cada correo necesita su
propio enlace de baja (decisión 12): un `to:` con todos expondría las
direcciones entre sí.

`mail_send` deja de hacer `return` mudo. Si la resolución da cero suscriptores:

1. registra `logger.warning` con el modelo, el pk y el `source`;
2. llama a `notify_superusers(source='email', severity=WARNING, ...)` con la
   clave de deduplicación del objeto, para que reintentar no duplique.

El envío es un acto explícito (decisión 11). La casilla `send_email` vive en
`WarningForm` y `WeatherReportForm`; las vistas consultan
`form.cleaned_data['send_email']` y no un diff de campos. `has_changes`
desapareció: era un disparador implícito que con audiencia automática se vuelve
spam.

Valores iniciales:

| Formulario | Crear | Editar |
|---|---|---|
| `WarningForm` | marcado | desmarcado |
| `WeatherReportForm` | desmarcado | desmarcado |

El texto de ayuda muestra el número real de destinatarios
(`audience_size()`), porque sin elección de lista la casilla es el único freno
visible.

## 5. Endpoint público y límites

- Vista Django normal (`apps/home/views/boletin/`), CSRF obligatorio, `POST`
  únicamente para suscribir.
- Rate limit por IP en cache: `NEWSLETTER_SUBSCRIBE_RATE = 5/hour`, clave
  `newsletter:subscribe:<ip>`. Usa `django.core.cache`, que en producción es
  Redis y en dev LocMem; el límite es best-effort por proceso cuando no hay
  Redis, y eso se documenta en el propio módulo.
- Respuesta idéntica para correo nuevo, existente o pendiente: no filtra qué
  direcciones están en la lista.
- El enlace de baja viaja en el pie de **todos** los correos masivos.

## 6. Boletín como entidad

- Pantalla de administración: listado de ediciones, crear/editar borrador, vista
  previa (render del correo en un `<iframe srcdoc>`) y botón **Enviar** con
  confirmación explícita.
- Comando equivalente: `python manage.py send_newsletter <uuid> [--dry-run]`.
  `--dry-run` imprime el número de destinatarios y no encola nada.
- Una edición enviada es **historial**: no se edita ni se borra. La vista y el
  comando lo impiden por estado, no por permiso.
- Envío: marca `sending`, resuelve la audiencia con `resolve_audience()` —la
  misma de avisos y pronósticos—, encola en lotes de 50 con `send_email_task` y
  `recipients_count`, y marca `sent`. Un segundo envío de la misma edición se
  rechaza por estado.
- Cada correo del boletín también va **uno por destinatario**, con su enlace de
  baja (decisión 12).
- Ventana horaria: `NEWSLETTER_SEND_WINDOW = (8, 18)` hora de `America/Havana`.
  Fuera de ventana el comando falla con un mensaje claro; la pantalla deshabilita
  el botón.

## 7. Avisos in-app para clientes (PR B)

### 7.1 Capa de política

`notify_superusers` se conserva y pasa a ser un caso de
`apps/core/notifications.py::notify_audience(...)`. Cada `source` declara su
audiencia:

| `source` | Audiencia permitida |
|---|---|
| `huey`, `deploy`, `email`, `system` | solo superusuarios |
| `invoice` | superusuarios **y** el `Customer.user` de la factura |
| `subscription` | superusuarios **y** el `Customer.user` de la suscripción |

La regla se aplica en código: `notify_audience` filtra el conjunto de
destinatarios contra la audiencia declarada antes de escribir. Una fuente
interna no puede alcanzar a un no-superusuario ni por error de llamada.

### 7.2 Preferencias

`NotificationPreference(user, source, in_app, email)` con `unique_together
(user, source)`. Sin fila = activado (opt-out, no opt-in). Solo existen
preferencias para fuentes de cliente; el admin no las tiene.

### 7.3 UI por rol

El icono de la fase 3 de 026 ya está en `dev`. Cambia solo la consulta:
`visible_for(user)` no distingue rol (ya filtra por `recipient`), y las rutas de
administración siguen exigiendo permiso. Un cliente ve su lista y nada más.

## 8. Configuración nueva

| Ajuste | Default | Uso |
|---|---|---|
| `NEWSLETTER_CONFIRMATION_TTL_HOURS` | `72` | caducidad del token |
| `NEWSLETTER_SUBSCRIBE_RATE` | `5/hour` | límite por IP |
| `NEWSLETTER_BATCH_SIZE` | `50` | tamaño de lote de envío |
| `NEWSLETTER_SEND_WINDOW` | `8-18` | ventana horaria |

## 9. Pruebas

| Área | Qué se prueba |
|---|---|
| Consentimiento | alta desde landing, doble opt-in, token de un solo uso, caducidad, reenvío |
| Baja | idempotencia, `active=False`, no borra, corta envíos siguientes |
| Perfil | casilla marcada/desmarcada, `source` correcto, cambio de correo mueve la fila |
| Duplicados | mismo correo en dos listas; `newsletter=True` que ya no miente |
| Audiencia | resolución por política, lista vacía deja log + aviso al admin, nunca mudo |
| Endpoint | validación de servidor, CSRF, rate limit, respuesta que no filtra existencia |
| Boletín | `draft` no envía, `sending` no reenvía, lote de 50, ventana horaria, `--dry-run` |
| Aislamiento | una fuente interna no alcanza a un no-superusuario; rutas de admin exigen permiso |

## 10. Riesgos

1. **Migración de datos irreversible en la práctica.** Se migran filas reales de
   listas del Centro. Mitigación: `reverse` reconstruye `EmailRecipient` desde
   `Subscription`, y el paso 2 no borra nada hasta que el 4 corre.
2. **Envío masivo accidental.** Mitigación: estado `sending`, confirmación
   explícita, `--dry-run`, ventana horaria y lotes.
3. **Límite por IP sin Redis.** En dev es por proceso; se documenta como
   best-effort y no se promete más de lo que hace.
4. **`mail_send` es compartido por avisos y reportes.** Cambiar su resolución de
   audiencia toca 10 call sites. Mitigación: la FK explícita sigue mandando; solo
   cambia el camino vacío, y hay prueba por cada tipo de aviso.

## 11. Orden de implementación (PR A)

1. Modelos + migración de datos + admin.
2. `apps/core/subscriptions.py` con sus pruebas.
3. Signal del perfil y cambio de correo.
4. `apps/core/audiences.py` y el arreglo de `mail_send`.
5. Endpoint público, confirmación y baja en `apps/home`.
6. Formulario real en el landing.
7. `Newsletter`, comando y pantalla de envío.
8. Verificación completa y cierre.

El **PR B** arranca después, sobre la capa de política de §7.
