# 032-horario-y-region — tareas

## Fase 1 — Modelo y migración

- [x] Campos `timezone_mode`, `utc_offset_hours` y `time_format` en `SiteConfiguration`.
- [x] Métodos `get_timezone()` y `get_time_format()`.
- [x] Migración `0003_*` versionada (posterior a `0002` de deuda de permisos/uuid).
- [x] Pruebas de modelo.

## Fase 2 — Configuración del dashboard

- [x] Campos de horario integrados en `SiteConfigurationForm`, junto a tema e identidad.
- [x] Tarjeta **Horario y región** dentro de la página Configuración → Sitio (`core:site_configuration`).
- [x] Sin entrada de menú ni URL propias: se eliminaron `TimezoneConfigurationUpdateView`, `core:timezone_configuration` y `pages/core/site/timezone.html`.
- [x] Pruebas de formulario, vista y plantilla.

## Fase 3 — Aplicación en el portal

- [x] `SiteTimezoneMiddleware` registrado en `MIDDLEWARE`.
- [x] Filtros `local_time`, `local_datetime`, `local_datetime_seconds`.
- [x] Plantillas de hora migradas a los filtros nuevos.
- [x] Pruebas de middleware y filtros.

## Fase 4 — Verificación

- [x] `python manage.py check` — sin problemas.
- [x] `python manage.py makemigrations --check --dry-run` — sin cambios.
- [x] `python manage.py test apps.core` — 418 pruebas OK.
- [x] `python manage.py test` — 1624 pruebas OK.
- [x] `djlint` sobre las plantillas tocadas — 0 errores, 0 archivos por reformatear.
- [x] `ruff check` / `ruff format --check` sobre los Python tocados — limpio.
- [x] Commit (sin push).

## Notas de alcance

- El formato 12/24 aplica a la **visualización**. Los datepickers de Tempus
  Dominus siguen en 12 h con AM/PM.
- Las tareas Huey y los PDF encolados siguen usando `settings.TIME_ZONE`.

## Historial

- **2026-11-12** — «fusiona confuguracion horario y region con la de sitios en la
  misma pagina manteniendo los estilis de formularios y botones de configuracion
  de sitio». Se integra el horario en la página **Configuración → Sitio**; la
  sección independiente se elimina. S1 reescrita; Fase 2 reabierta y cerrada de
  nuevo.
