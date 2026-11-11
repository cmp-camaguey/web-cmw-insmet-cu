# 030 — boletín y suscripción (PR A)

Objetivo: que el formulario del landing suscriba de verdad, con doble opt-in y
baja, y que `mail_send` deje de fallar en silencio cuando no hay destinatarios.
Rama: `feat/030-boletin-y-suscripcion` (desde `dev`).
Delivery: PR a `dev`; PR B (in-app) va aparte.
Runner: `python manage.py test` (Django unittest).
Spec de referencia: `openspec/changes/030-boletin-y-notificaciones-de-clientes/`.

## Specs

- **S1.** `Subscriber` guarda la persona y su consentimiento: `email` único,
  `source` (`landing`/`registro`/`perfil`/`admin`), `confirmed_at`,
  `confirmation_token` de un solo uso, `unsubscribe_token` permanente y
  `active`.
- **S2.** `Subscription` guarda la pertenencia: `unique_together
  ('subscriber', 'recipient_list')`. La misma dirección puede estar en dos
  listas sin duplicar la persona.
- **S3.** Como máximo una `EmailRecipientList` tiene `is_default=True`; es la
  audiencia automática de avisos, pronósticos y boletín. Ningún código busca la
  lista por `name='newsletter'`.
- **S4.** Las filas existentes de `EmailRecipient` se migran como confirmadas,
  `source='admin'`, `confirmed_at=created_at`, y el modelo viejo se borra.
- **S5.** Escribir un correo en el landing y pulsar "Suscribirse" crea una
  suscripción **pendiente** y envía un correo de confirmación; sin confirmar no
  recibe ningún envío.
- **S6.** El enlace de confirmación es de un solo uso y caduca a las 72 h.
- **S7.** Un correo vacío o inválido falla **visiblemente** en el servidor, no
  solo en el navegador.
- **S8.** Cada correo masivo lleva un enlace de baja que funciona sin login y
  corta los envíos siguientes. La baja es idempotente y no borra la fila.
- **S9.** `Profile.newsletter = False` deja de recibir el boletín en el envío
  siguiente; `True` lo reactiva sin doble opt-in (usuario autenticado).
- **S10.** Cambiar el correo del perfil **mueve** la suscripción: no queda una
  fila con la dirección anterior.
- **S11.** La audiencia es **automática**: todos los suscriptores confirmados y
  activos de la lista principal, sin que el operador elija lista en ningún
  formulario. Las direcciones externas que deben recibir avisos (instituciones,
  prensa) se cargan en esa misma lista con `source='admin'`. `mail_send` sin
  destinatarios deja rastro (log + aviso al admin), no un `return` mudo.
- **S14.** El envío es un acto explícito: cada publicación tiene una casilla
  «Enviar por correo» con el número de destinatarios a la vista. Un aviso nuevo
  arranca marcado; un aviso editado y un pronóstico (crear o editar) arrancan
  desmarcados. Sin marcar no sale ningún correo.
- **S15.** Cada correo masivo se envía **uno por destinatario**, con su propio
  enlace de baja: ningún suscriptor ve la dirección de otro.
- **S12.** El endpoint público tiene límite por IP (`5/hour`) y no revela si una
  dirección ya estaba en la lista.
- **S13.** Una edición de boletín es una entidad con asunto, cuerpo y estado
  `draft`/`sending`/`sent`; se envía por comando o pantalla, con vista previa,
  confirmación, lotes de 50 y ventana horaria.

## Tasks

- [x] **T1** Modelos + migración de datos + admin · S1–S4 · inline
- [x] **T2** `apps/core/subscriptions.py` · S5–S10 · inline
- [x] **T3** Signal del perfil y cambio de correo · S9–S10 · inline
- [x] **T4** `apps/core/audiences.py` + `mail_send` · S11, S15 · inline
- [x] **T4b** Casilla «Enviar por correo» y retiro de la FK de lista en
      `Warning`/`WeatherReport` · S14 · inline
- [x] **T5** Endpoint público (suscribir, confirmar, baja) · S5–S8, S12 · inline
- [x] **T6** Landing operativo · S5, S7 · inline
- [x] **T7** `Newsletter` + comando + pantalla · S13 · inline
- [x] **T8** Verificación completa · S1–S15 · inline

## Log

- **L1** (2026-11-07) Pedido original: notificaciones para clientes, aviso por
  correo a quien habilite "recibir novedades" en su perfil, formulario del
  landing operativo, y listas de correo que no se elijan a mano. Registrado en
  `proposal.md` (PR #149).
- **L2** (2026-11-07) "Sí, cierra las 9 y escribe el diseño (Recomendado)".
  Las nueve decisiones de §6 quedan cerradas con los valores propuestos y se
  escriben `design.md` y `tasks.md`.
- **L3** (2026-11-07) Cambio de requisito, verbatim: «para los avisos la idea es
  que tampoco el operador tenga que seleccionar la lista de correo, es decir la
  idea es que se haga automatico en la lista van todos los que quieran recivir
  actualizaciones por correo o el newsleter mas algunos email que no tienen
  porque estar en el sitio refistrados pero si se necesita enviarles el correo
  de los avisos, lo que prefiero que esto se haga automatico no que el operador
  tenga que seleccionar la lista de correo manualmente, otra cosa se necesita
  tambien la opcion de darse de baja del newsleter, analiza bien esto y no me
  des la razon porque si, mejoremos esta parte si es necesario, asi brindamos la
  mejor experiencia tanto de operadores, usuarios clientes etc».
  Consecuencia: la FK `email_recipient_list` se retira de `Warning` y
  `WeatherReport`, la audiencia pasa a ser única y automática (S11) y el envío
  se vuelve explícito con una casilla (S14) para no convertir cada guardado en
  un correo masivo. La baja ya estaba en S8 y ahora viaja en todos los correos.
- **L4** (2026-11-07) Pregunta del usuario: ¿la casilla arranca marcada o
  desmarcada? Decisión: depende de la publicación y del momento. Un aviso nuevo
  sí (nace para comunicarse); un aviso editado no (reenviar por una errata es
  peor que no reenviar); un pronóstico nunca (se corrige varias veces al día).

- **L5** (2026-11-07) Pruebas en verde para T1–T6: `test_subscriptions`,
  `test_audiences`, `test_email_recipient_views`, `test_site_url`,
  `test_newsletter`, `test_warning_views`, `test_weather_report_views` y
  `test_send_email_toggle` (47 + 5). Gotcha corregido: `Warning.uuid` es PK con
  `default=uuid4`, así que una instancia nueva ya trae pk y `pk is not None`
  miente al decidir si es edición; se usa `instance._state.adding`.

- **L6** (2026-11-07) Dos bugs reportados en la base real:
  1. «Un aviso se publicó sin destinatarios de correo» con un único superusuario
     que tenía el boletín marcado. Causa: la base no tenía **ninguna**
     `EmailRecipientList`, y `0002` solo marcaba la lista `newsletter` si ya
     existía. `resolve_audience` devolvía cero y el signal del perfil no tenía
     dónde suscribir. Corregido con `core.0003`, que crea la lista principal y
     da de alta a los perfiles con `newsletter=True`.
  2. El enlace «Ver detalle» abría `xdg-open`. Causa: la notificación guardaba
     el **nombre** de la URL (`home:warnings_tropical_cyclone`) en `url`; el
     navegador lo lee como esquema propio y delega en el sistema. `mail_send`
     guarda ahora una ruta (`reverse`) y `Notification.link` resuelve las filas
     viejas al vuelo.

- **L7** (2026-11-07) Aclaración del usuario: la BD de producción está vacía
  mientras se prepara el contenedor, así que el backfill de `0003` no inscribe a
  nadie real. Se mantiene tal cual. Las migraciones no transportan datos entre
  entornos: cada una corre contra la base donde se aplica, y ninguna contiene
  correos hardcodeados.

- **L8** (2026-11-09) Sincronización con `dev`/`main`. `main` ya corre el
  rediseño público de orl92 (#178), la limpieza de estáticos (#174) y el deploy
  con respaldo y comprobación de migraciones. Se **fusionó** `origin/dev` en
  lugar de rebasar: los 32 commits de 030 estaban revisados y reescribirlos no
  aporta nada. Respaldo en `backup/030-antes-de-merge-dev` (10f5043).
  Conflictos: `landing.html` y `test_landing.py` se quedaron en la versión de la
  rama (diseño de orl92 **con** el formulario real); `test_activity_log.py`
  conserva las dos partes (ventana de día local de `dev` + contexto de petición,
  usuario borrado e IP de la rama). «Modelos» sigue oculto en
  `menu-list.html` y el layout del landing mantiene `severe_only=True`.
  Verificado: `manage.py check`, `makemigrations --check --dry-run`, suite
  completa (1719 pruebas) y `make test-deploy` (107/107).

- **L9** (2026-11-09) Revisión visual del panel y del landing público. Cinco
  defectos y su causa:
  1. El registro de actividad mostraba `str(obj)` en ISO con microsegundos
     (`2026-11-09 13:43:04.827701+00:00`). `Warning`, `WeatherReport` y
     `ForecastExtendedDay` formatean ahora en hora local (`d/m/Y H:M`) o solo
     día, y dicen `sin fecha` si no hay valor.
  2. La franja de emergencia mostraba el aviso más grave aunque el visitante lo
     hubiera cerrado, y la memoria de un solo cierre tapaba avisos nuevos.
     Pasa a una lista de cierres con tope de diez, migrando el formato anterior;
     el servidor entrega los avisos vigentes y el navegador elige el primero no
     cerrado (ver commit `b942674`).
  3. La tabla de auditoría cambiaba de aspecto tras «Limpiar»: el refresco AJAX
     usa `rows.add()`, que crea celdas nuevas desde el `innerHTML` y pierde las
     clases del `<td>`; `text-nowrap` sobrevivía en la carga inicial y no en el
     refresco. Se quita la clase y ambos estados quedan iguales (el que ya se
     veía tras «Limpiar»).
  4. Las franjas animadas del landing y de las páginas de institución se
     habían reescrito con un include y CSS propios que perdían el movimiento
     original. Se revierte al marcado y al CSS de Tabler que ya existían: los
     archivos `section_waves.html` y `landing-waves.css` se eliminan y los
     templates vuelven sin diferencias contra el commit anterior.
  5. En tema oscuro los párrafos de las tarjetas se leían como secundarios y
     los iconos de «Servicios y herramientas» perdían su azul. Se fija el
     blanco atenuado de los párrafos y un azul claro para icono y recuadro.
  Además, la lista de notificaciones pasa a tiempo relativo y a un layout que
  en móvil baja los botones a su propia línea, con el icono a 1.5rem alineado
  al título. Verificado: `apps.dashboard` (111), `test_activity_log` +
  `test_model_strings` (28), `djlint` y capturas reales en 500px y 1280px.

- **L10** (2026-11-11) Cierre de T8 (verificación completa S1–S15) sobre
  `feat/030-boletin-y-suscripcion` con los tres arreglos visuales de L9 ya
  commiteados (`c4bf151`, `66dfe65`, `8119bd7`):
  - `python manage.py check` sin problemas y `makemigrations --check --dry-run`
    sin cambios.
  - Suite completa: **1734 pruebas OK** (456 s), sin `EXPECTED_FAILURE` ni
    saltos nuevos.
  - `ruff check .` y `ruff format --check .` en verde (407 archivos formateados);
    `djlint --reformat --check` sin cambios y `djlint --lint` con 0 errores en
    180 plantillas.
  - La rama queda 67 commits por delante de `dev`, con el árbol limpio. El PR a
    `dev` sigue pendiente de decisión del usuario.

- **L11** (2026-11-11) CI puso en rojo cuatro pruebas de fecha local; el verde
  local de L10 era falso. `Tests (Django)` falló con `18:41 != 17:41`,
  `22:00 != 21:00` y `09:43 != 08:43`: exactamente una hora de diferencia.
  Causa: las pruebas fijaban la hora de pared de `America/Havana` como número,
  y la regla de horario de verano de Cuba depende de la versión de `tzdata`
  instalada. El runner resolvía UTC-5 para el 8 de noviembre y el equipo de
  desarrollo UTC-4, así que la suite pasaba en un sitio y fallaba en el otro.
  No era un defecto del producto —la conversión es correcta en ambos— sino una
  prueba que medía el huso de Cuba en vez de la conversión.
  Arreglo: las cuatro pruebas fijan una zona sin horario de verano
  (`timezone.override` con UTC-5) y comprueban conversión y formato, que es lo
  que el código promete. Archivos: `apps/core/tests/test_templatetags.py`,
  `apps/core/tests/test_activity_log.py`, `apps/meteo/tests/test_model_strings.py`.
  Verificado en los dos extremos —`TZ=UTC` y la zona del equipo— 41 pruebas OK
  en cada corrida; después, suite completa **1734 OK** (458 s) sobre la rama ya
  fusionada con `dev` (#185 y #186), `ruff check` y `ruff format --check` en
  verde. Lección: una prueba que depende de la base de datos de husos no mide el
  código, mide el entorno.
