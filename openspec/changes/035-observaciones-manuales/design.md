# 035-observaciones-manuales — diseño

## 1. Modo de observaciones

`OBS_AUTOMATIC` (bool, default `True`) se declara en `config/settings/base.py` y
se agrega al catálogo `EDITABLE_FIELDS` del grupo `ftp`, como primer campo de la
tarjeta. El panel ya sabe escribir el archivo de entorno y marcar el valor como
`Pendiente de reinicio`; Django lo lee al arrancar, así que el interruptor no
tiene un camino de aplicación en caliente distinto al resto.

Consumo:

- `FileObs.descargar_archivos_por_hora`: el atajo local se activa con
  `OBS_LOCAL_ONLY` **o** con `OBS_AUTOMATIC` apagado. En modo manual no hay
  tráfico FTPS: el archivo o está en `media/obs` o no existe.
- `StationObservationView`: la autogeneración del simulador sigue exigiendo
  `OBS_LOCAL_ONLY` y además `OBS_AUTOMATIC`; en modo manual no se inventan datos.

Con el modo automático encendido, el archivo del FTP y el escrito por el panel
disputarían el mismo nombre. Por eso crear, editar y eliminar exigen el modo
manual; la sección queda en consulta y muestra un aviso.

## 2. Modelo `Observation` (`apps/meteo`)

Una fila por estación, día y hora (`UniqueConstraint`). Guarda los valores ya
decodificados (unidades físicas y códigos de clave) y el telegrama generado.

- Identidad: `uuid`, `station`, `observation_type` (`synoptic`/`intermediate`),
  `hour`, `obs_date`, `user` (autor), `created_at`, `updated_at`.
- Grupo `iR iX h VV`: `cloud_base_height` (h), `visibility` (VV). `iR=iX=1`
  fijos (estación dotada de personal, precipitación en sección 1).
- Grupo `Nddff`: `cloud_amount` (N, octas), `wind_direction` (dd),
  `wind_speed` (ff, m/s).
- Temperaturas: `temperature`, `dew_point` en °C con un decimal.
- Presión: `station_pressure`, `sea_pressure` en hPa con un decimal;
  `pressure_tendency` (a, 0–8) y `pressure_change` (ppp, hPa).
- Precipitación: `precipitation_3h` (mm) con `precipitation_3h_trace`;
  `precipitation_24h` (mm, opcional) con `precipitation_24h_trace`.
- Tiempo: `present_weather` (ww), `past_weather` (W1), `past_weather_2` (W2).
- Nubes: `low_cloud_type`, `medium_cloud_type`, `high_cloud_type` (CL/CM/CH).
- Solo sinóptica: `max_temperature`, `min_temperature`, `precipitation_24h`.

`default_permissions = ()` y los cuatro permisos en español, como el resto del
proyecto.

## 3. Codificador (`apps/api/data/SynopTelegram.py`)

Módulo puro (sin Django) que reproduce el formato que ya decodifica
`Descodificador`:

```
AAXX YYGG1
IIiii 11hVV Nddff 1SnTTT 2SnTdTdTd 3PPPP 4PPPP 5appp 6RRRtR 7wwW1W2 8NhCLCMCH 333 30///
<sesión 2>
NNNN
```

- `iR=iX=1` deja el grupo `6RRRtR` en el índice 8, `7wwW1W2` en el 9 y
  `8NhCLCMCH` en el 10, que es lo que lee el decodificador.
- Sinóptica: sesión 2 con `10SnTxTxTx 20SnTnTnTn 30/// 56909 58012 81826=` y,
  si hay dato, `7RRRR`. Intermedia: `56900 81825=`.
- Precipitación: `990` = traza, `000` = sin precipitación, resto en décimas.
- Nombre: `SM{NN}.{HH}` para 00/06/12/18 y `SI{NN}.{HH}` para 03/09/15/21, con
  `NN = station_number[2:]`. `SynopSimulator.filename_for` delega en esta regla
  para no tener dos.

El día embebido lo dicta `SynopDay.synop_expected_obs_date(hour)`, la regla que
usa la validación de `GetData`; el formulario no lo pide.

## 4. Personal por estación

`apps/meteo/station_roles.py` resuelve la pertenencia leyendo los grupos del
usuario con el patrón `Estacion <5 dígitos> - <Jefe|Revisor|Observador>`.

| Grupo | Permisos asignados |
|---|---|
| `Estacion N - Observador` | `view_observation`, `add_observation` |
| `Estacion N - Revisor` | + `change_observation` |
| `Estacion N - Jefe` | + `delete_observation` |

El comando `create_observation_groups` crea los 18 grupos y **agrega** los
permisos (no reemplaza), para no borrar concesiones hechas a mano.

Alcance: superusuario ve todas las estaciones; el resto, solo las de sus grupos.
El formulario limita las estaciones ofrecidas y las vistas de detalle, edición y
eliminación devuelven 404 fuera del alcance.

## 5. Escritura del telegrama

`apps/meteo/services/observations.py` arma el telegrama desde el modelo y lo
escribe en `MEDIA_ROOT/obs` de forma directa y atómica (`write_text` sobre el
nombre convencional). Editar reescribe el archivo; eliminar lo borra junto con
la fila. Todo dentro del ciclo de la vista, sin tarea asíncrona, porque el mapa
debe ver el dato en la siguiente petición.

El alta usa el formulario; la edición bloquea estación, tipo y hora (corrige
valores, no mueve la observación de horario) y conserva el autor original.

## 6. Registro y auditoría

La lista muestra estación, tipo, día, hora, temperatura, precipitación de 3 h,
autor y última actualización. El detalle muestra todos los campos y el telegrama
generado. Alta, edición y eliminación usan `log_action`, que escribe el log de
Django y `ActivityLog` con IP y User-Agent.

## 7. Pruebas

- Codificador: sinóptica e intermedia decodifican por `Descodificador` a los
  valores ingresados; sesión 2 distinta por tipo; nombres de archivo.
- Formulario: hora fuera del tipo, campos obligatorios por tipo, unicidad
  estación/día/hora, estaciones limitadas a la pertenencia.
- Vistas: alcance por estación (lista, detalle 404), permisos por rol,
  escritura y borrado del archivo, autor registrado, bloqueo en modo automático.
- Configuración: `OBS_AUTOMATIC` en el catálogo; `FileObs` no toca el FTP y la
  API no autogenera en modo manual.
