# 030b — avisos que faltan (baja, pendientes, envío, cliente, contratos)

Objetivo: cerrar los seis hechos que hoy no dejan rastro ni llegan a quien
deberían. Rama: `feat/030-boletin-y-suscripcion` (sigue la de 030).
Delivery: mismos PR a `dev`, en commits revisables por hecho.
Runner: `python manage.py test` (Django unittest).
Spec de referencia: `openspec/changes/030-boletin-y-notificaciones-de-clientes/`.

## Specs

- **S1.** Al completar una baja, el suscriptor recibe un **correo transaccional
  de confirmación** con enlace a `/suscripcion/` para volver a darse de alta
  (que exige doble opt-in). Se encola con `send_email_task`, no se envía en
  línea. Token inexistente o segunda visita al mismo enlace **no** reenvían.
- **S2.** `mail_send`, después de encolar el envío a la audiencia, deja un aviso
  **INFO** a los admins con el número de destinatarios y el objeto publicado;
  `dedupe_key` por `(source, pk, 'sent')`. El fallo sin destinatarios sigue
  avisando como hoy: hoy solo deja rastro el fracaso, y un envío que salió bien
  es justo el que nadie puede reconstruir.
- **S3.** `Subscriber.reminder_sent_at` (nullable). Una tarea Huey diaria
  **reenvía una sola vez** la confirmación a quien sigue pendiente y cuyo último
  envío tiene más de `NEWSLETTER_REMINDER_HOURS` (48 h), y **borra** los
  pendientes (`confirmed_at IS NULL`) creados hace más de
  `NEWSLETTER_PENDING_RETENTION_DAYS` (7 días), **salvo los que se dieron de baja
  antes de confirmar**: una fila inactiva es un «no» registrado, no un alta
  olvidada. Un suscriptor confirmado no se borra nunca; uno pendiente no
  consintió nada, así que no hay rastro que conservar.
- **S4.** Desactivar un cliente (`Customer`, baja lógica) que todavía tiene
  suscripciones activas deja un aviso **WARNING** a los admins con el cliente y
  cuántas quedan vivas. No es un eco de la acción: la pantalla no muestra que se
  está dejando de cobrar un servicio que sigue en pie.
- **S5.** Anular una `ServiceSubscription` envía al cliente un **correo** de
  aviso, solo si su usuario tiene dirección. Los otros dos cambios de estado ya
  escriben al cliente por su propio camino —`pending` manda la factura, `paid`
  manda el certificado—, así que repetirlos sería mandar dos correos del mismo
  hecho.
- **S6.** Una tarea Huey diaria avisa a los admins de los contratos cuyo
  `end_date` (derivada, filtrada en Python) cae dentro de los próximos
  `CONTRACT_EXPIRY_WARNING_DAYS` (30) días y que **no** tienen un contrato más
  nuevo que cubra esa fecha. `Certificate` queda fuera: el cliente se queda con
  sus certificados para siempre una vez entregados, no vencen.
- **S7.** Los avisos nuevos no duplican: cada uno usa `make_dedupe_key` con las
  partes del hecho, nunca con la fecha de ejecución, y los recordatorios que
  deben volver usan `repeat_after`. La fecha de **fin** de un contrato sí entra
  en la clave: no es cuándo corrió la tarea, es la identidad del hecho —un
  contrato extendido es otro aviso—.

## Assumptions

- **A1.** «Recordatorio de no confirmados» se dispara una sola vez (48 h) y la
  limpieza a los 7 días. No se reenvía más de una vez: quien no confirmó en tres
  días no va a confirmar por insistencia, y cada reenvío es correo no solicitado.
- **A2.** «Cancelación de cliente» es la baja lógica de `Customer`, no la de su
  suscripción (esa es S5). El aviso va al admin porque el riesgo es de
  facturación, no de comunicación al cliente.
- **A3.** «Cambio de estado» que necesita correo es solo la anulación: los otros
  ya están cubiertos por la factura y el certificado.

## Tasks

- [x] **T1** S1 · correo de baja · inline · `83d9585`. Pruebas: 23 en
      `apps.core.tests.test_subscriptions`, con `huey.immediate` para que
      `mail.outbox` vea lo encolado.
- [x] **T2** S2 · aviso de publicación enviada · inline · este commit. Pruebas:
      `MailSendSuccessNotificationTests` en `apps.core.tests.test_audiences`.
- [x] **T3** S3 · `reminder_sent_at` + migración + tarea Huey · inline · este
      commit. `core.0005` solo añade una columna nullable, sin reescritura de
      tabla en producción. Pruebas: `PendingSubscriberMaintenanceTests` (9) y la
      tarea añadida a `EXPECTED_TASKS` de `test_huey_registry`.
- [x] **T4** S4 · aviso al desactivar cliente con servicios vivos · inline · este
      commit. `notificar_cliente_desactivado` en `apps/commercial/notifications.py`
      con `source='commercial'` (fuente interna, no `'subscription'`). Pruebas:
      `ClienteDesactivadoTests` (5).
- [x] **T5** S5 · correo al cliente por anulación · inline · este commit.
      `apps/commercial/emails.py` + `templates/emails/suscripcion_anulada.html`.
      Pruebas: `SubscriptionCancelEmailTests` (3).
- [x] **T6** S6 · aviso de contratos por vencer · inline · este commit.
      `CONTRACT_EXPIRY_WARNING_DAYS=30` en `config/settings/base.py`, tarea
      `avisar_contratos_por_vencer` a las 13 UTC. Pruebas:
      `ContratosPorVencerTests` (7) y la tarea añadida a `EXPECTED_TASKS`.
- [x] **T7** Verificación: `manage.py check` sin problemas, `makemigrations
      --check --dry-run` sin cambios, `ruff check`/`format --check` en verde,
      apps tocadas (939) y suite completa (1693) en verde · inline · `3bb8bf2`

## Log

- **L1** (2026-11-08) Continuación pedida por el usuario («continua») sobre el
  plan de las seis notificaciones registrado en la memoria de reanudación del
  cambio 030. Las decisiones ya cerradas se respetan: la baja sí manda correo y
  el vencimiento es solo de contratos, con los certificados fuera. Los tres
  puntos que el registro no detallaba se documentan como A1–A3 en vez de
  quedar como decisión implícita en el código.
- **L2** (2026-11-08) T1 y T2 cerradas con pruebas verdes. En T1 el aviso se
  encola solo si la baja cambió algo, y el pie común omite el enlace de baja
  cuando no recibe `unsubscribe_url`: ofrecer «darse de baja» a quien acaba de
  darse de baja sería el defecto más visible de ese correo. En T2 la
  clave de deduplicación lleva el `pk` del objeto y `sent`, así que reenviar el
  mismo objeto sin leer no duplica el aviso.
- **L3** (2026-11-08) T3 cerrada. El recordatorio reusa el token vigente y solo
  lo reemite si ya venció: invalidar el enlace del correo original a las 48 h
  habría dejado «enlace no válido» a quien lo abriera después del recordatorio.
  La limpieza filtra `active=True` además de `confirmed_at IS NULL`; una fila
  inactiva sin confirmar es una baja pedida antes de confirmar, y borrarla
  contradiría la razón por la que `unsubscribe` no borra. La tarea corre a las
  4:30 UTC —medianoche en La Habana— y limpia antes de recordar.
- **L4** (2026-11-08) T4 cerrada. El aviso cuenta las suscripciones con
  `record_active=True` antes de la baja lógica del cliente (que no las toca) y
  se omite cuando no queda ninguna viva: un aviso que dice «0» es el eco que la
  S4 no quiere. La fuente es `commercial`, no `subscription`, porque es
  información de facturación para el admin y no un mensaje al cliente.
- **L5** (2026-11-08) T5 cerrada. El correo sale solo cuando la anulación se
  aplicó: una anulación rechazada por factura emitida o pago aprobado no avisa de
  nada, porque no hubo hecho. El encolado va dentro de `try/except` en la vista
  —la fila ya cambió y un 500 no la devuelve— y la plantilla reusa la cabecera y
  el pie comunes de `templates/emails/includes/`.
- **L6** (2026-11-08) T6 cerrada. El filtro de vencimiento va en Python porque
  `end_date` es derivada, y la renovación se detecta por `start_date` —no por
  `pk`—: otro contrato activo del mismo cliente, más nuevo, que cubra la fecha
  de fin. `Certificate` no entra, como pediste: entregado es para siempre. La
  fecha de fin sí va en la clave de deduplicación porque identifica el hecho.
- **L7** (2026-11-09) El revisor de `gga` encontró tres cosas en T6 y dos se
  arreglaron aquí: el comentario de cabecera decía «las 13 UTC son las 9 de la
  mañana» y eso solo vale en horario de verano (en invierno son las 8), y la
  comprobación de renovación pedía los contratos del cliente dentro del bucle,
  una consulta por contrato en una tarea diaria. Ahora se cargan todos de una
  vez y se agrupan en memoria.
- **L8** (2026-11-09) Hallazgo pendiente, fuera de T6: `SubscriptionForm` declara
  `start_date` como `forms.DateField` pero `ServiceSubscription.start_date` es un
  `DateTimeField`, así que guardar la solicitud escribe una fecha ingenua y
  Django emite un `RuntimeWarning`. No cambia el resultado, pero conviene
  decidir si el campo del modelo debe ser fecha o el formulario datetime.
- **L9** (2026-11-09) Empate de `start_date` en la detección de renovación: se
  aplicó una condición mejor que la acordada. `start_date >=` arreglaba la
  renovación firmada el mismo día, pero creaba un agujero peor: dos contratos
  con el mismo inicio y la misma vigencia cubren el mismo rango, así que cada
  uno suprimiría al otro y no avisaría ninguno. La condición queda en «otro
  contrato del mismo cliente que cubre este fin y se extiende más allá»
  (`otro.cubre(fin) and otro.end_date > fin`): resuelve el empate y no esconde
  dos vencimientos reales. Dos pruebas nuevas: la renovación del mismo día no
  avisa, y dos contratos idénticos avisan los dos.
- **L10** (2026-11-09) T7 encontró dos fallos que no eran del código de las
  tareas sino de la prueba: `test_operator_logs` y `SubscriptionCancelViewTests`
  anulan una suscripción, y desde T5 eso encola un correo. Con la cola real la
  tarea quedaba pendiente en el `huey.db` compartido y rompía
  `test_send_test_invoice`, que afirma que el camino por defecto no deja nada
  encolado. Se corrigió en los dos tests que provocan el efecto, no en el test
  que lo detecta: ponen `huey.immediate = True` y lo restauran.
- **L11** (2026-11-09) Auditoría de los 102 tests añadidos por la rama (tests
  nuevos de 030 y 030b). No se encontró ninguno que sobre por las razones que
  importan —tautológico, duplicado exacto, prueba de framework o camino
  muerto—. Los dos pares que parecían duplicados prueban capas distintas:
  `test_cambiar_el_correo_mueve_la_suscripcion` existe una vez sobre
  `move_subscription()` y otra sobre el `ProfileForm`; y las dos
  `test_sin_la_casilla_no_se_envia_correo` cubren `Warning` y `WeatherReport`,
  que son modelos distintos. Se conservan todos.
- **L12** (2026-11-09) Recorte pedido por el usuario: se eliminan las tres
  pruebas de plantilla de `apps/home/tests/test_newsletter.py` que solo
  afirmaban decoración —`test_la_pagina_de_confirmacion_usa_el_mismo_layout_que_la_baja`,
  `test_la_pagina_de_baja_usa_el_layout_sin_menus` y
  `test_la_pagina_de_baja_invalida_usa_el_layout_sin_menus`—. Añadían la ruta
  del webp, la ausencia de navbar y el texto de éxito; la conducta (200, fila
  confirmada, baja idempotente, «Enlace no válido») sigue cubierta por
  `test_confirmar_con_token_valido_confirma`,
  `test_baja_funciona_sin_login_y_es_idempotente` y
  `test_baja_con_token_inexistente_no_da_404`. Lo que deja de cubrirse es el
  copy de éxito y que esas páginas no muestren navegación.
  Se conservan, en cambio, las pruebas de plantilla que prueban un contrato
  funcional y no decoración: el formulario real del landing (T7),
  `data-emergency-banner`/`data-warning-id` (lo que lee el JS del botón) y la
  carga de `emergency-banner.js`.
- **L13** (2026-11-09) T11 decidió dónde nacen los avisos de cliente. Factura
  emitida: en la facturación por lote (`process_batch_invoice`) y en la manual
  (`InvoiceCreateView.form_valid`), no dentro de
  `generate_invoice_pdf_and_email_task`, porque esa tarea también corre para
  re-renderizar un PDF y el aviso debe existir aunque el correo falle. Cambio de
  estado: `paid` al aprobar el pago y `cancelled` al anular. `requested` no avisa
  (lo provoca el cliente) y `pending` no avisa (llega con la factura); avisar de
  ambos repetiría el mismo hecho.
- **L14** (2026-11-09) T12: la preferencia es **opt-out** (sin fila = activado)
  y cada canal lee su propio booleano: `notify_audience` consulta `in_app` en una
  sola query para toda la tanda y `mail_send` consulta `muted_email_addresses`
  antes de encolar. El filtro de `notify_audience` **no toca a los superusuarios**:
  una fila de preferencia es del cliente y no puede silenciar al que administra.
  Si todos los suscriptores de un envío apagaron el correo, no se avisa al admin:
  no es un fallo operativo, es un consentimiento.
  Migración `core.0006_notificationpreference` (tabla nueva, sin datos previos).
  La UI para mover los interruptores no está en la lista de tareas de 030; queda
  fuera de este PR y se reporta al cierre.
