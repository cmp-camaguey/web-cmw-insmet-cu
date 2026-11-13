# 034-sondeo-api-externa — tareas

## Fase 1 — Vistas proxy

- [x] Helper `fetch_sounding_plot` con el contrato de respuesta (crudo o JSON base64).
- [x] `SoundingView.post` sin generación local; conserva `plot_image`, `datetime` y `params`.
- [x] `SoundingExportView` reenvía `fmt` y sirve el archivo como adjunto.
- [x] Pruebas: JSON base64, PNG en crudo, 404, 502 por conexión, 400 de formato, adjunto PDF.

## Fase 2 — Retiro del generador local

- [x] Copia sin versionar de `plot_generators.py` y `test_skewt_export.py` en el archivo local.
- [x] Borrar ambos del repositorio.
- [x] Quitar `MetPy` y `matplotlib` de `requirements/base.txt`.

## Fase 3 — Rutas y plantillas

- [x] Comentar `modelo/descargar_gif/` y su import en `apps/home/urls.py`.
- [x] Comentar la referencia `{% url 'home:models_download_gif' %}` en `maps.html`.
- [x] Nota junto a las rutas de sondeo: se reactivan cuando la API responda.

## Verificación

- [x] `python manage.py check`
- [x] `python manage.py test apps.home`
- [x] `python manage.py test` (suite completa)
- [x] `ruff check` y `djlint` sobre lo tocado
