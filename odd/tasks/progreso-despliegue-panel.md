# Progreso del despliegue en el panel

Objetivo: mientras el despliegue está en curso, el desplegable del navbar muestra una
barra `progress progress-sm` de Tabler —en lugar de la palabra "Desplegando…"— y debajo
la acción que se está ejecutando; la barra se llena con el progreso real de `deploy.sh`.

Rama: `feat/progreso-despliegue`. Estrategia de entrega: `ask-on-risk` (2 work units).
Runner: `python manage.py test` (Django) + `make test-deploy` (scripts de deploy).

## Specs

- **S1** — Al pulsar "Actualizar ahora", mientras el estado es `running`, el desplegable
  muestra una barra `progress progress-sm` en lugar del texto "Desplegando…", y debajo de
  la barra la acción que se está ejecutando. Texto del pedido: "cundo se le da al boton
  actualizar cuno este haciendo el despliege muestra progress-sm en ves de la palabra
  desplegando y debajo la accion que se ba realizando".
- **S2** — La barra se llena con el progreso real del despliegue: `deploy/deploy.sh`
  publica el paso en curso en `/var/lib/webcmp/deploy-progress.json` y el panel lo traduce
  a etiqueta y porcentaje. Texto del pedido: "el progress-sm debe ir llenandose con el
  progreso".
- **S3** — Sin archivo de progreso (ventana entre `running` y el primer paso del script,
  archivo ausente o ilegible) la barra es indeterminada y la acción dice
  "Preparando el despliegue…". Nunca 0% congelado ni barra vacía.
- **S4** — Fuera de `running` (`available`, `failed`, `ok`, `up_to_date`, `unknown`) el
  bloque de progreso no se muestra: queda el mensaje de siempre.
- **S5** — Frontera de confianza intacta: el archivo de progreso lo escribe otro usuario
  (root). El panel solo acepta una clave de paso de una lista cerrada y calcula etiqueta y
  porcentaje del lado del servidor; nada del archivo llega crudo al navegador.
- **S6** — Durante el rollback de un despliegue fallido la barra vuelve a indeterminada con
  la acción "Revirtiendo el código", en vez de quedarse en el último porcentaje.
- **S7** — `deploy/README-deploy.md` documenta el archivo nuevo (dueño/modo), el
  comportamiento del panel y qué mirar cuando el progreso no avanza.
- **S8** — Pruebas: `apps/dashboard/tests/test_update.py` cubre el contrato del progreso y
  `deploy/tests/test_deploy_sh_progress.sh` cubre el escritor del script. Texto del pedido:
  "deves usar tabler".

## Tasks

| ID | Tarea | Specs | Ruta | Estado | Commit |
|---|---|---|---|---|---|
| T1 | `deploy.sh`: constante `PROGRESS_FILE`, función `progress()` best-effort (nunca aborta el deploy) y una llamada por sección + rollback | S2, S3, S6 | inline | hecho | |
| T2 | `autodeploy.sh`: borra el progreso del intento anterior antes de escribir `running` | S3 | inline | hecho | |
| T3 | `settings.UPDATE_PROGRESS_FILE` + `update.py`: pasos con etiqueta/peso, `progress()` saneado y `progress` en `build_state()` | S2, S3, S5 | inline | hecho | |
| T4 | `templates/includes/dashboard/update_navbar.html`: bloque `progress progress-sm` (barra + línea de acción) oculto por defecto | S1 | inline | hecho | |
| T5 | `static/dist/js/dashboard-update.js`: pintar barra, porcentaje y acción; indeterminada sin porcentaje; ocultar fuera de `running` | S1, S3, S4, S6 | inline | hecho | |
| T6 | Tests Django del progreso (paso válido, desconocido, ausente, fuera de `running`, rollback, no se reenvía texto crudo, markup de Tabler) | S5, S8 | inline | hecho | |
| T7 | Test de shell del escritor + alta en `test_shell_scripts.sh` | S8 | inline | hecho | |
| T8 | `deploy/README-deploy.md`: archivo, permisos, comportamiento y diagnóstico | S7 | inline | hecho | |

## Log

- **L1 (2026-11-11)** — "cundo se le da al boton actualizar cuno este haciendo el
  despliege muestra progress-sm en ves de la palabra desplegando y debajo la accion que se
  ba realizando el progress-sm debe ir llenandose con el progreso"
- **L2 (2026-11-11)** — "deves usar tabler"
- **L3 (decisiones, 2026-11-11)** — El progreso es real, no simulado: el único proceso que
  sabe en qué paso va el despliegue es `deploy.sh`, así que publica la clave del paso en
  `/var/lib/webcmp/deploy-progress.json` (mismo directorio 2775 `deploy:webcmp` que ya
  comparten el panel y el ejecutor). El porcentaje y la etiqueta los calcula Django desde
  una tabla cerrada —igual que `RUN_MESSAGES`— para no abrir un canal de texto desde un
  archivo escrito por otro usuario hacia el DOM. La barra es `progress progress-sm` de
  Tabler con `progress-bar-indeterminate` cuando no hay porcentaje (preparación y
  rollback), así que "llenándose" y "sin dato" se distinguen de verdad.
