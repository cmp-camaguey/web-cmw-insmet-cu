# 035-observaciones-manuales — requisitos

## S1 — Interruptor de modo en el panel

`Configuración → Entorno del servidor → Observaciones (FTP)` DEBE incluir el
interruptor **Observaciones automáticas por FTP** (`OBS_AUTOMATIC`), editable y
sujeto a la regla general del panel: se escribe en el archivo de entorno y
aplica al reiniciar, marcándose como pendiente mientras difiera.

- Aceptación: el campo aparece en la tarjeta `Observaciones (FTP)` y guardarlo
  agrega o cambia `OBS_AUTOMATIC` sin tocar el resto del archivo.

## S2 — Sección Observaciones

El dashboard DEBE exponer una sección **Observaciones** con entrada propia en el
menú lateral, registro en `/meteo/observaciones/` (`meteo:observacion_list`) y
alta en `/meteo/observaciones/crear/` (`meteo:observacion_create`).

- Aceptación: el usuario con `meteo.view_observation` ve la entrada y la lista;
  sin el permiso recibe 403 y no ve la entrada.

## S3 — Formulario por tipo de observación

El formulario DEBE capturar una observación **Sinóptica** (horas 00/06/12/18,
con temperatura máxima, mínima y precipitación de 24 h) o **Intermedia**
(03/09/15/21, sin esos tres datos).

- La hora DEBE pertenecer al tipo elegido; una combinación inválida no guarda.
- En sinóptica, máxima y mínima son obligatorias y la mínima no puede superar a
  la máxima.

## S4 — Telegrama y archivo del mapa

Al guardar, el sistema DEBE generar un telegrama `AAXX` decodificable por
`Descodificador` y escribirlo en `MEDIA_ROOT/obs` como `SM{NN}.{HH}` o
`SI{NN}.{HH}` según el tipo.

- El día embebido DEBE ser el de `synop_expected_obs_date(hour)`.
- Aceptación: la API `station/observation/<hora>/<estación>/` devuelve los
  valores ingresados para el ciclo vigente.

## S5 — Personal y visibilidad por estación

La pertenencia DEBE resolverse con grupos de Django `Estacion <número> - <rol>`
(`Jefe`, `Revisor`, `Observador`).

- Un usuario DEBE ver solo las observaciones de las estaciones a las que
  pertenece; el superusuario ve todas.
- El detalle, la edición y la eliminación de una observación de otra estación
  DEBEN responder 404.
- El formulario DEBE ofrecer solo las estaciones propias.

## S6 — Roles y permisos

Los grupos DEBEN otorgar permisos del modelo `Observation`: observador
`view_observation` y `add_observation`; revisor además `change_observation`;
jefe además `delete_observation`. El comando `create_observation_groups` DEBE
crear los 18 grupos y agregar los permisos sin quitarlos.

## S7 — Registro de autoría

El registro DEBE mostrar por observación la estación, el tipo, el día, la hora,
la temperatura, la precipitación de 3 h, el usuario que la realizó y la última
actualización. El detalle DEBE mostrar todos los datos y el telegrama generado.

## S8 — Modo automático en consulta

Con `OBS_AUTOMATIC` encendido, la sección NO DEBE permitir crear, editar ni
eliminar; DEBE mostrar un aviso de que las observaciones llegan por FTP.

## S9 — Auditoría y archivos

Alta, edición y eliminación DEBEN registrar `ActivityLog` con usuario, IP y la
estación/hora afectada. Eliminar DEBE borrar también el telegrama. Editar DEBE
conservar el autor original y reescribir el telegrama.

## S10 — Pruebas

La suite DEBE cubrir codificador (ida y vuelta por `Descodificador`, ambos
tipos), formulario, unicidad, alcance por estación, permisos por rol, escritura
y borrado del archivo, el campo del panel y el modo manual en `FileObs`/API.
