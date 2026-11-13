# 034-sondeo-api-externa — propuesta

## Motivación

Las vistas `SoundingView` y `SoundingExportView` consultan la API externa de modelos
(`https://modelo.cmw.insmet.cu/api/sounding/`) para obtener los datos del sondeo, pero
generan el Skew-T + hodógrafo **en el servidor** con MetPy + matplotlib
(`apps/home/data/plot_generators.py`). Eso arrastra dos dependencias pesadas
(`MetPy`, `matplotlib`) y un render de ~1 s por petición en el proceso web de gunicorn.

La API externa de modelos queda como responsable de entregar el gráfico, igual que
`MapaView` (imágenes de `apimet.cmw.insmet.cu`) y `MeteogramView` (datos de
`modelo.cmw.insmet.cu/api/meteogram/`). El portal solo reenvía la petición.

## Solución

- `SoundingView` pasa a ser un proxy: reenvía `datetime_init`, `lat`, `long`, `t_index` a
  la API externa y devuelve `plot_image` en base64 (contrato que ya consume
  `static/dist/js/sounding.js`). Acepta que la API responda el PNG en crudo o un sobre JSON.
- `SoundingExportView` reenvía también `fmt` y sirve el archivo de la API como descarga.
- Se elimina del repositorio el generador local (`plot_generators.py` y su test) y sus
  dependencias. Una copia queda sin versionar en
  `openspec/changes/archive/034-sondeo-api-externa/legacy/` para reutilizarla al escribir la API.
- `modelo/descargar_gif/` se comenta, como el resto de las rutas de `/modelo/`.

## Criterios de aceptación

- Con la API externa respondiendo, `POST /modelo/sounding/` devuelve 200 y `plot_image`
  en base64; no se importa matplotlib en `apps/home/views/modelos/views.py`.
- Sin conexión a la API externa, ambas vistas responden 502 con mensaje al usuario.
- `GET /modelo/sounding/export/pdf/` entrega un PDF como adjunto; `fmt` fuera de
  `{png, pdf, svg}` responde 400 sin llamar a la API.
- `requirements/base.txt` sin `MetPy` ni `matplotlib`; la suite completa sigue en verde.

## Fuera de alcance

- Escribir la API externa (vive fuera de este repositorio).
- Reactivar las rutas de `/modelo/`: la API externa está caída; las líneas quedan comentadas.
- Limpiar `MPLCONFIGDIR` de `deploy/`: queda inerte y no rompe nada.
