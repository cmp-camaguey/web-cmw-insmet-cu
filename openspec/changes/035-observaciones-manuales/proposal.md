# 035-observaciones-manuales — propuesta

> **Estado: aprobada el 2026-11-13.** El usuario pidió dos formas de alimentar
> las observaciones (FTP o manuales), una sección de Observaciones en el
> dashboard con formulario por tipo de observación, telegrama para el mapa y
> personal por estación con visibilidad restringida.

## 1. Motivación

Hoy el mapa de observaciones solo conoce el camino automático: `FileObs` baja
por FTPS el archivo `SM{NN}.{HH}` / `SI{NN}.{HH}` de `/Reportes Procesados` y
`GetData` lo decodifica para la API. Sin FTPS no hay observaciones, y no existe
manera de cargar a mano la observación de una hora que la descarga no trajo.

Además, el personal de cada estación (jefe, revisor, observadores) no existe en
el sistema: cualquier usuario con permisos de meteorología ve y gestiona todas
las estaciones por igual.

## 2. Solución

- **Interruptor de modo** en `Configuración → Entorno del servidor →
  Observaciones (FTP)`: `OBS_AUTOMATIC`. Encendido, el mapa usa el FTP
  (comportamiento actual). Apagado, el mapa usa únicamente los telegramas
  generados desde el panel.
- **Sección Observaciones** en el dashboard con el registro y el alta.
- **Formulario por tipo**: sinóptica (00/06/12/18, con Tx/Tn y precipitación de
  24 h) o intermedia (03/09/15/21, sin esos grupos). Al guardar se codifica el
  telegrama `AAXX` y se escribe en `media/obs` con el nombre que espera el mapa.
- **Personal por estación** con los grupos de Django existentes
  `Estacion <número> - <rol>`; el rol se traduce en permisos del modelo y el
  alcance por estación se aplica en querysets y formularios.
- **Registro**: autor, fecha de actualización, datos ingresados y telegrama
  generado, visibles solo para los miembros de esa estación.

## 3. Alcance

Dentro:

- `apps/api/data/SynopTelegram.py` (codificador y nombres de archivo).
- `apps/meteo/models.py` (`Observation`), migración, formulario, vistas,
  URLs, servicio de escritura y plantillas.
- `apps/meteo/station_roles.py`, comando `create_observation_groups`.
- `OBS_AUTOMATIC` en `config/settings/base.py`, catálogo del panel, `FileObs` y
  API, con entrada de menú y pruebas.

Fuera:

- Flujo de aprobación de observaciones (el revisor corrige; no aprueba).
- Edición del día de la observación: lo calcula `synop_expected_obs_date`.
- Sincronización entre el FTP y las observaciones manuales: cada modo es dueño
  de los archivos de su ciclo.
- Rediseño del mapa público.
