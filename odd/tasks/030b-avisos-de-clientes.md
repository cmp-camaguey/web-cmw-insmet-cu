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
  partes del hecho, nunca con la fecha, y los recordatorios que deben volver usan
  `repeat_after`.

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
- [ ] **T4** S4 · aviso al desactivar cliente con servicios vivos · inline
- [ ] **T5** S5 · correo al cliente por anulación · inline
- [ ] **T6** S6 · aviso de contratos por vencer · inline
- [ ] **T7** Verificación: `manage.py check`, `makemigrations --check --dry-run`,
      pruebas de las apps tocadas y suite completa · inline

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
