# Sondeo (Skew-T) servido por la API externa de modelos

Rama: `feat/034-sondeo-api-externa` · Entrega: PR único a `dev` · Runner: `python manage.py test`
OpenSpec: `openspec/changes/034-sondeo-api-externa/`

## Specs

S1 — `SoundingView.post` DEBE dejar de generar el gráfico localmente (matplotlib/MetPy) y
reenviar la petición a `https://modelo.cmw.insmet.cu/api/sounding/` con `datetime_init`,
`lat`, `long` y `t_index` (este último con base 0, como hasta ahora).

S2 — La respuesta JSON de `SoundingView` DEBE conservar el contrato que consume
`static/dist/js/sounding.js`: `{"status": "success", "plot_image": "<base64 png>", "datetime": ..., "params": ...}`.
La API externa puede responder el PNG en crudo (`Content-Type: image/*`) o un sobre JSON con
`plot_image`/`image` en base64 o data URI; la vista normaliza ambos a base64.

S3 — Errores de `SoundingView`: formulario inválido → 400 con `errors`; upstream 404 →
404 con mensaje de «sin datos»; fallo de conexión o respuesta inválida → 502 con `message`.

S4 — `SoundingExportView` DEBE obtener el archivo de la API externa (reenviando `fmt`) y
servirlo como descarga con el `Content-Type` del formato y
`Content-Disposition: attachment; filename="sounding_<stamp>.<fmt>"`. Formato fuera de
`{png, pdf, svg}` → 400 sin llamar a la API.

S5 — El código local que deja de usarse (`apps/home/data/plot_generators.py` y
`apps/home/tests/test_skewt_export.py`) DEBE salir del repositorio versionado y conservarse
sin versionar en `openspec/changes/archive/034-sondeo-api-externa/legacy/` para reutilizarlo
al escribir la API externa. `MetPy` y `matplotlib` salen de `requirements/base.txt`
(`numpy` se queda: lo usa `apps/api/data/Descodificador.py`).

S6 — Las rutas `modelo/sounding/` y `modelo/sounding/export/<fmt>/` siguen comentadas en
`apps/home/urls.py` porque la API externa está caída; `modelo/descargar_gif/` se comenta como
las demás y su referencia `{% url 'home:models_download_gif' %}` en `maps.html` también.

## Tasks

| ID | Specs | Ruta | Estado | Commit |
|----|-------|------|--------|--------|
| T1 | S1-S4 | inline | hecho | HEAD de la rama |
| T2 | S5 | inline | hecho | HEAD de la rama |
| T3 | S6 | inline | hecho | HEAD de la rama |

## Log

L1 (2026-11-13): «cambia SoundingView, SoundingExportView a un API externa del mismo mode como estan MapaView y MeteogramView, comenta DescargarGifView como las demas. Primero reviza si hay que hacer pull, utiliza MPC cedegraph»

L2 (2026-11-13): aclaración del usuario: «esta es la url modelo.cmw.insmet.cu/api/sounding/ que debe debolber el grafico Haroa esta caido por eso estan comentadas las urls, el codigo que se deja de usar al migrar al api externa guardalo sin vercionar para reutilosarlo y escribir el api(esto no se aqui)».

L3: `dev` estaba limpia y sincronizada con `origin/dev`; no hizo falta pull. Rutas de modelos
(mapas, meteogram, sounding, sounding/export) ya estaban comentadas desde `f7d15864` (#170);
solo `modelo/descargar_gif/` seguía activa.

L4 — evidencia (2026-11-13):
- RED observado antes de implementar: `apps.home.tests.test_sounding_api` → 8 fallos + 1 error
  (las vistas seguían con `generate_skewt`/500 en vez de 404/502).
- GREEN: `python manage.py test apps.home` → 269 tests OK.
- `python manage.py check` → sin issues. `ruff check apps/home config` → limpio.
  `ruff format` aplicado. `djlint --check maps.html` → 0 archivos a actualizar.
- Differential check de rutas (base `dev` vs rama, mismo script sobre `git archive dev`):
  328 → 327 rutas; única diferencia `models_download_gif :: modelo/descargar_gif/`, la que se
  pidió desactivar.
- Suite completa: `python manage.py test` → 1936 tests OK (246 s). Commit en `feat/034-sondeo-api-externa`; los
  hooks del repo (ruff, djlint, detect-secrets, review de GGA) pasaron.
