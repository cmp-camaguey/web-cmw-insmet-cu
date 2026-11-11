# 034 — Monitoreo del sitio en el dashboard

Objetivo: una sección nueva en el dashboard que muestre, en tiempo real, el uso de
recursos del servidor y la salud del sitio (HTTP, Web Vitals, tráfico y errores JS),
con datos reales medidos con `psutil`, middleware de petición y un beacon RUM propio.
Branch: `feat/034-monitoreo-sitio`. Delivery: `ask-on-risk`. Runner: `python manage.py test`.

## Specs

- **S1 — Recursos reales del servidor.** La sección muestra uso de CPU (global y por
  núcleo), memoria RAM, swap, disco (uso y E/S), red (bytes enviados/recibidos), carga
  media, uptime del host y del proceso. Fuente: `psutil`. Si `psutil` no puede leer una
  métrica, el campo se muestra como `—` y no rompe la página.
- **S2 — Métricas HTTP reales.** Cada petición servida se registra con código de estado,
  duración en ms, método y ruta, salvo `/static/`, `/media/`, el beacon RUM y el endpoint
  JSON del propio panel. El registro nunca puede romper la petición (fallo silencioso).
- **S3 — Disponibilidad e incidentes.** Uptime 24 h = porcentaje de peticiones registradas
  que **no** devolvieron 5xx, con mínimo de muestra (si hay menos de 20 peticiones se
  muestra `—`). Incidente activo = racha de 5xx en curso; su duración se mide desde la
  primera respuesta 5xx de la racha.
- **S4 — Códigos de estado.** Conteo y distribución de 2xx, 3xx, 4xx y 5xx en la ventana
  visible, más tasa de error (%).
- **S5 — Tiempo de respuesta y TTFB.** Media, p50, p95 y máximo del tiempo de respuesta
  del servidor. Se rotula explícitamente "TTFB del servidor": mide desde que entra la
  petición hasta que el backend entrega la respuesta, **no** incluye red ni navegador.
- **S6 — Concurrencia.** Peticiones en vuelo en el último minuto calculadas por solape de
  intervalos (`inicio`, `inicio + duración`), acotado a las últimas 1 000 filas.
- **S7 — Pageviews y tráfico en vivo.** Visitantes activos (5 y 30 min), sesiones activas,
  pageviews por minuto, páginas más activas, fuentes de tráfico (referrer), dispositivo y
  navegador. Todo desde el beacon; sin beacon no hay datos y se muestra estado vacío.
- **S8 — Core Web Vitals.** LCP, FCP, INP y CLS con p75 de las últimas 24 h y clasificación
  por umbrales oficiales: LCP ≤ 2,5 s bueno / ≤ 4 s mejorar / > 4 s pobre; FCP ≤ 1,8 s /
  ≤ 3 s / > 3 s; INP ≤ 200 ms / ≤ 500 ms / > 500 ms; CLS ≤ 0,1 / ≤ 0,25 / > 0,25.
- **S9 — Errores JavaScript.** Errores no controlados (`error` y `unhandledrejection`) con
  mensaje, archivo, línea, ruta y fecha; se listan los últimos con su tasa por 100 pageviews.
- **S10 — Peso de página y recursos lentos.** El beacon envía KB totales transferidos
  (`transferSize` de resource timing) y cantidad de recursos que tardaron más de 500 ms.
  Se muestra la media de KB y el conteo de recursos lentos.
- **S11 — Sección del dashboard.** Ruta `/dashboard/monitoring/`, nombre `dashboard:monitoring`,
  sólo superusuarios (misma regla que "Monitoreo de tareas"), con auto-refresh cada 5 s por
  `fetch` al endpoint `/dashboard/monitoring/data/` y botón de pausa. El primer render es
  del servidor; el JS sólo actualiza valores.
- **S12 — Retención.** Tarea periódica diaria de Huey y comando de gestión
  `prune_monitoring` que borran `RequestMetric` y datos RUM más viejos que
  `MONITORING_RETENTION_DAYS` (30 por defecto).
- **S13 — Beacon sin build.** `static/dist/js/rum-beacon.js` en JS vanilla, incluido desde
  `templates/includes/base/scripts.html`, con `navigator.sendBeacon`, id de sesión en
  `localStorage` con TTL de 30 min (sobrevive a la navegación entre páginas y pestañas),
  payload máximo 8 KB, throttling por sesión y respeto a `MONITORING_ENABLED`.
- **S14 — Interruptor.** `MONITORING_ENABLED` (por defecto `True`). Apagado: el middleware
  no registra, el beacon no se inyecta y la página sigue mostrando recursos del servidor
  con un aviso de que la captura está desactivada.

## Tasks

| ID | Specs | Ruta | Estado | Commit |
|----|-------|------|--------|--------|
| T1 | S1-S10, S14 | inline | [x] | — |
| T2 | S11 | inline | [x] | — |
| T3 | S12, S13 | inline | [x] | — |
| T4 | S1-S14 | inline | [x] | — |

## Log

### L1 — Pedido original del usuario

> nueva seccion en el dashboard de Monitoreo en el cual se muestren en tiempo real es uso
> de recursos del servidor mas estos parametros 1. Disponibilidad y salud del sistema ...
> (lista completa de 4 capas: disponibilidad/uptime, códigos HTTP, tiempo de respuesta,
> TTFB, incidentes activos; Core Web Vitals LCP/CLS/INP/FCP y carga completa; tráfico en
> vivo con visitantes activos, pageviews, páginas top, referrers, mapa geográfico,
> dispositivo, eventos y sesiones; métricas técnicas con tasa de errores, errores JS,
> peticiones concurrentes y peso de página). Usar en MPC tabler para construir todos los
> elemento, usar mcp codegraph.

### L2 — Decisión de alcance (usuario)

Alcance elegido: **"Servidor real + RUM mínimo"**. Incluye recursos reales (psutil),
métricas HTTP reales por middleware, y beacon RUM ligero (pageviews, sesiones, páginas
top, referrer, dispositivo/navegador, LCP/CLS/INP/FCP, errores JS, peso de página).
**Fuera de alcance:** mapa geográfico y eventos personalizados. No se inventan datos:
sin beacon, las tarjetas RUM muestran estado vacío.

### L3 — Decisiones técnicas

- App nueva `apps/monitoring`: modelos RUM + métricas de petición, servicios de lectura,
  middleware, endpoint público del beacon y poda. El panel vive en `apps/dashboard`.
- Una fila por petición (`RequestMetric`) en lugar de contadores en caché: los contadores
  en memoria se fragmentan entre workers de gunicorn y mentirían. Retención + poda
  acotan el costo.
- `psutil==7.1.3` en `requirements/base.txt`: rueda `abi3` manylinux, no compila.
- Gráficos: SVG generados por JS vanilla. ApexCharts (lo que usa Tabler) no está
  vendoreado y el proyecto prohíbe build/npm.
- Sin mapa geográfico ni GeoIP: requiere base externa y actualización mensual.

### L4 — Primera corrida de tests: 6 fallos y su causa raíz

- Los tres fallos de "captura apagada" (middleware escribe, beacon se inyecta, aviso
  ausente) no eran del código: `.env` tiene `DEBUG=True`, así que `manage.py test` corre
  bajo el perfil `dev` y el `MONITORING_ENABLED = False` de `testing.py` nunca se aplicaba.
  El interruptor se apaga ahora en `base.py` cuando `'test'` está en `sys.argv`; los tests
  del monitoreo lo reactivan con `override_settings(MONITORING_ENABLED=True)`.
- `new_sessions` contaba sesiones viejas como nuevas: filtraba la ventana **antes** del
  `Min`, así que la primera visita de una sesión con historia quedaba recortada al primer
  pageview dentro de la ventana. Ahora el `Min` se calcula sobre toda la sesión, acotado
  con un subquery a las sesiones vistas en la ventana. El test usaba una primera visita
  de hace 20 min para una ventana de 30: se movió a 45 min.
- `pageview_series`: el test creaba el pageview hace 1 min y afirmaba el último bloque.
  Era un error del test; ahora crea el pageview en el minuto en curso. Además la
  truncación se fija en UTC (`tzinfo=UTC`) en las tres series, porque la zona del sitio
  puede tener desfase de minutos y correría los buckets fuera de la rejilla.
- `test_staff_sin_superusuario_no_entra` recibía 302: `CheckUserProfileMiddleware`
  redirigía al alta de perfil porque el usuario de prueba no tenía nombre ni apellido.
  El usuario de prueba ahora tiene el perfil completo; el 403 de la vista es lo que se
  mide.
- `apps/core/tests/test_huey_registry.py` enumera las tareas registradas: se agregó
  `apps.monitoring.tasks.podar_monitoreo`, que es contrato real del consumer.
- Evidencia: `apps.monitoring` 63/63 en verde; `manage.py check` y
  `makemigrations --check --dry-run` limpios; ruff y djlint sin hallazgos.
