# 035 — Observaciones manuales

Objetivo: permitir que cada estación capture a mano sus observaciones sinópticas e intermedias, genere el telegrama SYNOP que consume el mapa y quede registro de quién lo hizo, con visibilidad restringida por estación.
Rama: `feat/035-observaciones-manuales` (desde `dev`). Entrega: commits locales en la rama, sin push.
Runner: `python manage.py test apps.meteo apps.core.tests.test_env_config apps.api`.

## Specs

- **S1** — `Configuración → Entorno del servidor → Observaciones (FTP)` gana el interruptor **Observaciones automáticas por FTP** (`OBS_AUTOMATIC`). Activado: el mapa obtiene las observaciones del FTP, como hoy. Desactivado: el mapa usa solo las observaciones cargadas desde el panel. Como el resto del panel, el cambio aplica al reiniciar y se marca `Pendiente de reinicio`.
- **S2** — Nueva sección **Observaciones** en el dashboard, con entrada propia en el menú lateral: **Registro** (`/meteo/observaciones/`, name `meteo:observacion_list`) y **Nueva observación** (`/meteo/observaciones/crear/`).
- **S3** — Formulario que captura los datos según el tipo de observación: **Sinóptica** (horas 00/06/12/18; incluye temperatura máxima, mínima y precipitación de 24 h) o **Intermedia** (horas 03/09/15/21; sin esos grupos). El tipo y la hora deben corresponderse; el servidor lo valida.
- **S4** — Al guardar, la observación genera el telegrama `AAXX` con el mismo formato que decodifica `Descodificador` y lo escribe en `media/obs` como `SM{NN}.{HH}` (sinóptica) o `SI{NN}.{HH}` (intermedia), que es el archivo que lee el mapa de observaciones. El día embebido sigue la regla única de `SynopDay.synop_expected_obs_date`.
- **S5** — Cada estación se conforma con jefe, revisor y observadores mediante grupos de Django `Estacion <número> - <rol>` (`Jefe`, `Revisor`, `Observador`), administrables con la pantalla de Grupos existente. Un usuario perteneciente a una estación **no puede ver ni gestionar** las observaciones de otra; el superusuario ve todas.
- **S6** — Los roles se traducen en permisos sobre el modelo `Observation`: observador `view_observation` + `add_observation`; revisor suma `change_observation`; jefe suma `delete_observation`. La restricción de estación se aplica además en el queryset y en el formulario (la estación se elige solo entre las propias).
- **S7** — El registro muestra, por observación, la estación, el tipo, el día y la hora, la temperatura, la precipitación de 3 h, **quién** la realizó y cuándo se actualizó, más una vista de detalle con todos los datos ingresados y el telegrama generado.
- **S8** — Crear, editar y eliminar solo se permite con el modo manual activo (`OBS_AUTOMATIC` apagado). En modo automático las observaciones son del FTP: la sección queda en consulta y muestra un aviso; no se reescriben archivos que pertenecen a la descarga.
- **S9** — Alta, edición y eliminación registran `ActivityLog` con usuario, IP y estación/hora; el autor original se conserva al editar. Eliminar una observación elimina también su telegrama.
- **S10** — El comando `create_observation_groups` crea los 18 grupos (6 estaciones × 3 roles) y les asigna los permisos de S6. Correrlo dos veces no duplica ni quita permisos agregados a mano.
- **S11** — Pruebas: codificador (ida y vuelta por `Descodificador`, SM/SI), formulario (hora/tipo, campos obligatorios por tipo, unicidad estación/día/hora), alcance por estación, permisos por rol, escritura y borrado del archivo, campo nuevo del panel de entorno y modo manual en `FileObs`/API.

## Tasks

- [x] T1 — `apps/api/data/SynopTelegram.py`: codificador puro y nombres de archivo. (S4) — inline
- [x] T2 — Modelo `Observation` + migración + permisos custom. (S3, S6) — inline
- [x] T3 — `apps/meteo/station_roles.py` + comando `create_observation_groups`. (S5, S6, S10) — inline
- [x] T4 — `apps/meteo/services/observations.py`: telegrama desde el modelo y escritura/borrado en `media/obs`. (S4, S9) — inline
- [x] T5 — `apps/meteo/forms/observation.py`: campos, tipos, alcance por estación y unicidad. (S3, S5) — inline
- [x] T6 — Vistas list/detail/create/update/delete + URLs. (S2, S6, S8, S9) — inline
- [x] T7 — Templates de registro, detalle y formulario + entrada de menú. (S2, S7) — inline
- [x] T8 — `OBS_AUTOMATIC` en settings, catálogo del panel, `FileObs` y API. (S1, S8) — inline
- [x] T9 — Pruebas. (S11) — inline
- [x] T10 — Docs OpenSpec 035. (todas) — inline

## Log

- **L1 (original)** — "Cambio grande en las observaciones meteorologicas, agragar en configuracion
Entorno del servidor Observaciones (FTP) si las obserbaciones son automaticas via ftp (ya implementado) o se introducen manuales (1: Agregar nueva seccion al dashboard de Observaciones. 2: Formulario para igresar los datos los cuales corespondan a el tipo de obcervacion sinoptica o intermedia para generar el archivo telegrama corespondiente que se usara pera el mapa de observaciones. 3: Cada estacion esta conformada por un jefe de estacion, un revisor y los observadores los que pertenecen a una estacion no pueden gestionar otra. 4: en el dashboard en la seccion de obvservaciones deve quedar u registro que quien realiza una observacion y los datos ingresados visibilidad con la restrigcion de estacion una estacion no puede ver los datos de otra)"
- **L2 (decisión, 2026-11-13)** — Asignación de personal: **grupos de Django existentes**, un grupo por estación y rol. El vínculo se resuelve por el nombre del grupo `Estacion <número> - <rol>`; se provee un comando para crearlos con sus permisos.
- **L3 (decisión, 2026-11-13)** — Los roles se traducen en permisos del modelo (observador alta/lectura, revisor corrección, jefe eliminación) en lugar de una pantalla de personal nueva.
- **L4 (decisión, 2026-11-13)** — El formulario no pide día: lo calcula `synop_expected_obs_date` al guardar, para que el telegrama pase la validación del mapa sin divergir de la regla compartida.
- **L5 (decisión, 2026-11-13)** — En modo automático la sección es de consulta: escribir archivos que luego sobrescribe la descarga FTP produciría un mapa que cambia solo. Alta/edición/eliminación exigen modo manual.
- **L6 (supuesto)** — El revisor corrige (no aprueba): no se agrega un flujo de aprobación porque el pedido no lo menciona. Si se necesita, se modela como estado y se reabre esta feature.
- **L7 (corrección, 2026-11-13)** — `SingleObjectMixin` filtra por `pk` aunque el kwarg de la URL se llame `uuid`, así que detalle y edición respondían 404. Se resolvió con `StationScopedMixin.get_object()`, que busca por `uuid` dentro del queryset de las estaciones del usuario.
- **L8 (corrección, 2026-11-13)** — `ObservationForm` leía `self.instance` antes de `super().__init__()`. Se movió la asignación después; los usuarios de prueba llevan nombre y correo para que `CheckUserProfileMiddleware` no los desvíe al formulario de perfil.
- **L9 (pruebas, 2026-11-13)** — `python manage.py test apps.meteo.tests.test_observations`: 26 pruebas, OK. `python manage.py test`: 1962 pruebas, OK. `manage.py check`, `makemigrations --check --dry-run`, `ruff check`, `ruff format --check` y `djlint --check` limpios.
- **L10 (entrega, 2026-11-13)** — Cuatro commits locales en la rama: datos/dominio, interfaz y configuración, pruebas y documentación, y pruebas de alcance cruzado. Sin push.
- **L11 (verificación, 2026-11-13)** — El verificador independiente no pudo correr (límite de uso del proveedor), así que las sondas se corrieron en línea: ida y vuelta de los 8 horarios por `Descodificador` (temperatura, rocío, estación, viento y precipitación correctos); nombres de archivo idénticos a la implementación base para las 6 estaciones × 8 horarios; con `OBS_AUTOMATIC=True` las ramas nuevas de `FileObs` y `StationObservationView` reducen a la lógica base.
- **L12 (RDD, 2026-11-13)** — `gentle-ai review assess` sobre `e102377..HEAD`: riesgo `medium`, `review_due: false`, motivo `under_budget`. Sin revisión nativa.
- **L13 (pruebas, 2026-11-13)** — 28 pruebas en `apps.meteo.tests.test_observations`, OK. Se cubrieron dos huecos: edición y borrado por `uuid` de otra estación responden 404, y un envío rechazado no crea registro ni escribe telegrama.
