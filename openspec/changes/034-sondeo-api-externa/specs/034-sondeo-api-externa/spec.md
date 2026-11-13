# 034-sondeo-api-externa — requisitos

## S1 — Sondeo servido por la API externa

`SoundingView.post` DEBE reenviar la petición a `https://modelo.cmw.insmet.cu/api/sounding/`
con `datetime_init`, `lat`, `long` del municipio seleccionado y `t_index` en base 0.

- Aceptación: con la API respondiendo, `POST /modelo/sounding/` devuelve 200 y **no** se
  importa `matplotlib` ni `apps.home.data.plot_generators` desde la vista.

## S2 — Contrato del gráfico

La respuesta DEBE conservar `{"status": "success", "plot_image": "<base64>", "datetime": ...,
"params": ...}` que consume `static/dist/js/sounding.js`, tanto si la API responde el PNG en
crudo como si responde un sobre JSON con `plot_image`/`image`.

- Aceptación: PNG en crudo y base64 en JSON producen el mismo `plot_image`.

## S3 — Errores del sondeo

Formulario inválido → 400 con `errors`. API 404 → 404 con mensaje de «sin datos». Sin
conexión, 5xx o respuesta sin gráfico → 502 con `message`.

- Aceptación: cada caso se prueba con la API simulada.

## S4 — Exportación

`SoundingExportView` DEBE pedir el archivo a la API externa reenviando `fmt` y servirlo con
`Content-Type` del formato y `Content-Disposition: attachment; filename="sounding_<stamp>.<fmt>"`.
`fmt` fuera de `{png, pdf, svg}` → 400 sin llamar a la API.

- Aceptación: `fmt=pdf` entrega `application/pdf` como adjunto; `fmt=gif` responde 400.

## S5 — Retiro del generador local

`apps/home/data/plot_generators.py` y `apps/home/tests/test_skewt_export.py` DEBEN salir del
repositorio, con copia sin versionar para reutilizarse al escribir la API externa.
`MetPy` y `matplotlib` DEBEN salir de `requirements/base.txt`.

- Aceptación: `git ls-files` no los lista; la copia local existe; la suite pasa sin ellos.

## S6 — Rutas de `/modelo/`

Las rutas de sondeo SIGUEN comentadas (la API externa está caída) y `modelo/descargar_gif/`
se comenta como las demás, igual que su referencia en `maps.html`.

- Aceptación: ninguna ruta de `/modelo/` está activa y `manage.py check` no reporta errores
  de plantilla.
