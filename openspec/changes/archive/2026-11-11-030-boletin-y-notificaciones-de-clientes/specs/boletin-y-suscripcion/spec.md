# boletin-y-suscripcion Specification

Capability: `boletin-y-suscripcion` (consentimiento de correo y audiencia de los envíos masivos)

## Purpose

Correo consentido en el portal del Centro Meteorológico Provincial Camagüey. El
consentimiento tiene una sola puerta de escritura (`apps/core/subscriptions.py`),
la audiencia de los envíos masivos se resuelve por política y no a mano, y la
baja deja el mismo estado en el correo y en el perfil.

## Requirements

### Requirement: CONSENTIMIENTO-PUERTA-UNICA

El consentimiento de correo MUST escribirse solo a través de
`apps/core/subscriptions.py` (`subscribe`, `confirm`, `unsubscribe`,
`set_consent`, `move_subscription`). Ninguna vista, señal ni comando SHALL crear
o modificar `Subscriber` o `Subscription` por su cuenta.

#### Scenario: Una vista nueva intenta escribir el modelo directamente

- **GIVEN** una vista que crea `Subscription` sin llamar al servicio
- **WHEN** se revisa el cambio
- **THEN** se rechaza: el consentimiento dejaría de tener un solo significado

#### Scenario: Dos formas de la misma dirección

- **GIVEN** que el correo `Juan@X.cu` ya está registrado
- **WHEN** se solicita el alta con `juan@x.cu`
- **THEN** se reutiliza la misma fila de `Subscriber` y no se crea una segunda

### Requirement: ALTA-DOBLE-OPT-IN

El alta del boletín MUST ser un `POST` con CSRF a `/suscripcion/`
(`home:newsletter_subscribe`), MUST validar el correo en el servidor y MUST
limitar los intentos por IP. La respuesta SHALL ser la misma para un correo
nuevo, uno ya confirmado y uno pendiente.

#### Scenario: Correo nuevo, existente o pendiente

- **GIVEN** un visitante que envía un correo válido
- **WHEN** la petición llega al servidor
- **THEN** recibe «Si el correo es válido, recibirá un mensaje para confirmar la
  suscripción…» en los tres casos, y solo se encola el correo de confirmación
  cuando hace falta confirmar

#### Scenario: El correo no es válido

- **GIVEN** una petición directa que se salta la validación del navegador
- **WHEN** el correo no cumple el formato
- **THEN** el servidor responde «Escriba un correo electrónico válido.» y no se
  crea ningún suscriptor

#### Scenario: Demasiados intentos desde una IP

- **GIVEN** una misma IP que supera `NEWSLETTER_SUBSCRIBE_LIMIT` (5) altas en
  una hora
- **WHEN** envía otra
- **THEN** recibe «Demasiados intentos desde su conexión…» y no se escribe nada

#### Scenario: Un GET al endpoint de alta

- **GIVEN** un visitante que abre la URL del alta en el navegador
- **WHEN** la petición no es `POST`
- **THEN** se le redirige al landing y no se crea nada

### Requirement: CONFIRMACION-TOKEN

El enlace de confirmación MUST llevar un token de un solo uso con caducidad de
`NEWSLETTER_CONFIRMATION_TTL_HOURS` (72 h por defecto). Al confirmar SHALL
anularse el token y activarse la suscripción. Un token usado o vencido SHALL
responder una página con el motivo, nunca un 404.

#### Scenario: Token vencido

- **GIVEN** un token emitido hace más de 72 h
- **WHEN** la persona abre el enlace
- **THEN** ve la página de confirmación con el aviso de caducidad y la
  suscripción sigue sin confirmar

#### Scenario: Segundo clic en el mismo enlace

- **GIVEN** un token ya usado
- **WHEN** se vuelve a abrir
- **THEN** la página explica que el enlace no es válido y no falla con 404

#### Scenario: La dirección pertenece a una cuenta del sitio

- **GIVEN** un usuario registrado con la misma dirección que confirma desde el
  landing
- **WHEN** la confirmación termina
- **THEN** la casilla «recibir novedades» de su perfil queda marcada

### Requirement: BAJA-IDEMPOTENTE

La baja MUST ser un `GET` público a `/suscripcion/baja/<token>/` que ponga
`active=False`, borre la pertenencia a la lista principal y desmarque la casilla
del perfil. La operación SHALL ser idempotente y SHALL enviar el correo de baja
solo cuando cambió algo.

#### Scenario: Baja repetida

- **GIVEN** una persona ya dada de baja
- **WHEN** vuelve a abrir el enlace de baja
- **THEN** la página responde lo mismo y no se reenvía ningún correo

#### Scenario: Guardar el perfil después de la baja

- **GIVEN** una baja que desmarcó la casilla del perfil
- **WHEN** la persona guarda su perfil
- **THEN** no vuelve a quedar suscrita sin pedirlo

### Requirement: PERFIL-SIN-DOBLE-OPT-IN

La casilla `Profile.newsletter` MUST aplicarse sin doble opt-in, porque la
persona está autenticada. Desmarcarla SHALL quitar la pertenencia a la lista
principal; un cambio de correo SHALL trasladar la suscripción a la dirección
nueva.

#### Scenario: Marcar la casilla en el perfil

- **GIVEN** un usuario autenticado sin suscripción
- **WHEN** marca «recibir novedades» y guarda
- **THEN** queda como `Subscriber` confirmado y activo, sin correo de
  confirmación

#### Scenario: Desmarcar sin haber estado nunca en la lista

- **GIVEN** un usuario que nunca se suscribió
- **WHEN** guarda el perfil con la casilla desmarcada
- **THEN** no se crea ninguna fila de suscriptor

#### Scenario: Cambio de dirección

- **GIVEN** un suscriptor confirmado que cambia su correo en el perfil
- **WHEN** guarda el formulario
- **THEN** no queda ninguna fila con la dirección anterior y la nueva hereda la
  suscripción

### Requirement: AUDIENCIA-AUTOMATICA

Los envíos masivos MUST resolverse con `resolve_audience()`: suscriptores
activos y confirmados de la lista marcada como principal
(`EmailRecipientList.is_default`), ordenados por correo. El operador SHALL NOT
elegir lista en ningún formulario. Como máximo una lista MAY ser la principal.

#### Scenario: No hay lista principal marcada

- **GIVEN** una instalación sin `is_default=True`
- **WHEN** se resuelve la audiencia
- **THEN** el resultado es vacío y quien llama decide qué hacer, sin excepción

#### Scenario: Dos listas marcadas como principales

- **GIVEN** dos listas con `is_default=True`
- **WHEN** se guarda la segunda
- **THEN** la base lo rechaza por la restricción única parcial

#### Scenario: Un correo por destinatario

- **GIVEN** una audiencia de tres suscriptores
- **WHEN** se envía un aviso
- **THEN** salen tres correos, cada uno con el enlace de baja de su destinatario

### Requirement: ENVIO-EXPLICITO

`WarningForm` y `WeatherReportForm` MUST exponer una casilla `send_email` con el
número real de destinatarios en el texto de ayuda. El envío SHALL depender de esa
casilla y no de un diff de campos. Si la audiencia resuelve vacía SHALL
registrarse un `logger.warning` y avisarse a los superusuarios, sin `return`
mudo.

#### Scenario: Aviso nuevo

- **GIVEN** un operador que crea un aviso
- **WHEN** abre el formulario
- **THEN** la casilla de envío aparece marcada

#### Scenario: Aviso editado

- **GIVEN** un operador que edita un aviso ya publicado
- **WHEN** abre el formulario
- **THEN** la casilla de envío aparece desmarcada

#### Scenario: Sin suscriptores confirmados

- **GIVEN** una lista principal sin suscriptores confirmados
- **WHEN** se publica un aviso con la casilla marcada
- **THEN** no sale ningún correo, queda un `logger.warning` y los superusuarios
  reciben un aviso una sola vez por objeto

### Requirement: RECORDATORIO-Y-PODA

Un alta sin confirmar MUST recibir como máximo un recordatorio, pasadas
`NEWSLETTER_REMINDER_HOURS` (48 h). Las altas nunca confirmadas SHALL borrarse
pasados `NEWSLETTER_PENDING_RETENTION_DAYS` (7 días). Un suscriptor confirmado
SHALL NOT borrarse nunca, ni siquiera dado de baja.

#### Scenario: La tarea diaria corre dos noches seguidas

- **GIVEN** un alta pendiente que ya recibió su recordatorio
- **WHEN** la tarea vuelve a correr
- **THEN** no se encola un segundo recordatorio

#### Scenario: Baja pedida antes de confirmar

- **GIVEN** un alta pendiente que el visitante dio de baja
- **WHEN** corre el recordatorio
- **THEN** no se le escribe: pidió no recibir nada

#### Scenario: Alta abandonada

- **GIVEN** un alta sin confirmar de hace más de 7 días
- **WHEN** corre la poda
- **THEN** la fila se borra, porque nunca hubo consentimiento que conservar

### Requirement: LANDING-FORMULARIO-REAL

El bloque «Manténgase informado» del landing MUST ser un `<form>` con `action`,
`method="post"`, `{% csrf_token %}` y `name="email"`, y SHALL mostrar el éxito o
el error en la misma página.

#### Scenario: Visitante envía su correo desde el landing

- **GIVEN** el landing público
- **WHEN** el visitante escribe un correo y pulsa «Suscribirse»
- **THEN** el navegador envía un `POST` real y la página muestra el mensaje del
  servidor
