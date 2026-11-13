# 034-sondeo-api-externa — diseño

## Contrato con la API externa

`GET https://modelo.cmw.insmet.cu/api/sounding/?datetime_init=YYYYMMDDHH&lat=..&long=..&t_index=..[&fmt=png|pdf|svg]`

La vista acepta dos respuestas equivalentes, para no atar el portal a una sola forma:

1. El gráfico en crudo: `Content-Type: image/png` (o `application/pdf`, `image/svg+xml`)
   con el archivo en el cuerpo.
2. Un sobre JSON con el gráfico en base64:
   `{"status": "success", "plot_image": "<base64|data URI>", "datetime": "..."}`
   (también se acepta la clave `image`).

`200` sin `plot_image` se trata como respuesta inválida (502), no como «sin datos».
`404` se traduce a «no hay datos para los parámetros seleccionados».

## Flujo

```
sounding.js  --POST-->  SoundingView  --GET-->  modelo.cmw.insmet.cu/api/sounding/
             <--JSON--                <-------  (gráfico en crudo o en base64)
             {plot_image: b64}
```

`SoundingView` reencoda siempre a base64 para conservar el contrato del JS; así el frontend
no cambia y da igual en qué forma responda la API.

`SoundingExportView` usa el mismo helper (`fetch_sounding_plot`) y sirve los bytes como
adjunto; el `Content-Type` sale de la lista blanca `{png, pdf, svg}` ya validada.

## Errores

`fetch_sounding_plot` levanta `SoundingAPIError(message, status)`; las vistas solo traducen:

| Situación | status | Mensaje |
|---|---|---|
| Sin conexión / timeout | 502 | No se pudo conectar con la API de sondeo en este momento. |
| 404 de la API | 404 | No hay datos de sondeo disponibles para los parámetros seleccionados. |
| 5xx u otro HTTP | 502 | La API de sondeo no respondió correctamente. |
| JSON sin gráfico o base64 inválido | 502 | La API de sondeo no devolvió ningún gráfico. / ... no es válida. |

Formulario inválido sigue devolviendo 400 con `errors` (comportamiento previo).

## Código que sale del repositorio

| Archivo | Motivo |
|---|---|
| `apps/home/data/plot_generators.py` | Generación local con MetPy + matplotlib; la hace la API. |
| `apps/home/tests/test_skewt_export.py` | Prueba el generador que se va. |

Copia sin versionar en `openspec/changes/archive/034-sondeo-api-externa/legacy/` (ese
directorio ya está en `.gitignore`). Ahí queda también el contrato de datos que espera
`generate_skewt_file` (`p, T, Td, u, v, z` con `value`/`unit`) para reutilizarlo al
escribir la API externa.

## Dependencias

Salen `MetPy==1.7.1` y `matplotlib==3.11.2` de `requirements/base.txt`. `numpy` se queda:
lo usa `apps/api/data/Descodificador.py`.

## Riesgos

- **Item 5 (dependencias)**: quitar MetPy/matplotlib cambia el entorno de despliegue. Se
  verifica con `manage.py check`, la suite completa y `pip install --dry-run` cuando haya red.
- **Contrato desconocido**: la API está caída y no se pudo inspeccionar. Por eso se aceptan
  las dos formas y se documenta el contrato en esta propuesta; el helper tolera ambas.
- Las rutas siguen comentadas hasta que la API responda: el código migrado no se ejerce en
  producción todavía y eso es deliberado.
