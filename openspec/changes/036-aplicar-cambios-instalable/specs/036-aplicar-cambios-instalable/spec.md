# 036-aplicar-cambios-instalable — requisitos

## S1 — Instalador de las piezas de "Aplicar cambios"

`deploy/install-apply-units.sh` DEBE instalar, como root y en una sola corrida,
las tres piezas del botón: `/usr/local/sbin/webcmp-apply-env` (`root:root 0755`),
`webcmp-apply.service` y `webcmp-apply.path` (`root:root 0644`), recargar
`systemd` y habilitar el `.path`.

- DEBE ser idempotente: una segunda corrida no reescribe ningún archivo y lo
  informa.
- DEBE renderizar el unit desde `APP_DIR`, `APPLY_SCRIPT`, `SERVICE_GROUP`,
  `GUNICORN_UNIT`, `HUEY_UNIT` y `APPLY_UNIT`, sustituyendo de forma literal.
- DEBE aceptar `--help` sin root, y `DRY_RUN=1` para mostrar los comandos sin
  ejecutarlos.
- NO DEBE tocar el `.env`, la base, el venv, el código ni `install.sh`.
- Aceptación: `bash deploy/tests/run.sh` en verde; la ayuda nombra las piezas y
  advierte que no toca el `.env`.

## S2 — Negarse a habilitar el `.path` con una petición pendiente

Si existe `$APPLY_STATE_DIR/apply-request`, el instalador DEBE abortar antes de
habilitar nada, nombrar la ruta del archivo y decir cómo destrabarlo.

- Motivo: `PathExists=` dispara ese reinicio en el instante en que la unidad
  queda activa, fuera de la ventana de mantenimiento.
- Aceptación: con el archivo presente el script sale con código 1 y no invoca
  `systemctl enable`; sin él, sigue.

## S3 — `deploy.sh` se niega a desplegar sin las unidades que el commit pide

Cada `deploy/systemd/*.service` y `*.path` del commit DEBE existir en
`/etc/systemd/system` (con el nombre renderizado, si `install.sh` puede
cambiarlo) antes de instalar dependencias o migrar. Si falta alguna, el deploy
DEBE abortar listándolas y nombrando el instalador.

- La comprobación DEBE correr después de `git checkout` y antes de
  `pip install`.
- `--check` DEBE reportar lo mismo sin desplegar.
- El deploy NO DEBE instalar las unidades.
- Aceptación: `deploy/tests/test_deploy_sh_units.sh` cubre faltantes, nombres
  renderizados y la posición del guard.

## S4 — El panel avisa de una petición que nadie recogió

Pasados `PENDING_GRACE_SECONDS` (30 s) desde la escritura de
`apply-request`, la página **Entorno del servidor** DEBE mostrar un aviso que
diga que la petición quedó sin recoger, con la hora, el autor si se puede leer y
qué revisar en el servidor.

- NO DEBE mostrar el aviso dentro del margen: la página se repinta justo después
  de crear el archivo.
- DEBE mostrarlo aunque el archivo esté vacío o con JSON inválido.
- NO DEBE reemplazar el estado del último reinicio: son hechos distintos.
- Aceptación: pruebas de `pending_request` y de las dos vistas en
  `apps/core/tests/test_env_config.py`.

## S5 — Pruebas
La suite DEBE cubrir el render literal, la negativa con petición pendiente, la
instalación real (solo como root), `missing_units` con árboles temporales,
`ensure_apply_path` y el aviso del panel.

- Aceptación: `make test-deploy` y `python manage.py test apps.core` en verde.

## S7 — El deploy deja el botón operativo

Cuando el `.path` ya está instalado, el deploy DEBE habilitarlo y arrancarlo
después de reiniciar los servicios y antes del health check, y DEBE abortar si no
queda activo.

- Si existe una petición en disco, DEBE descartarla informándolo: el reinicio del
  propio deploy la cumple.
- Si la unidad no está instalada, NO DEBE hacer nada (el guard de S3 ya abortó).
- `--check` DEBE reportar el `.path` instalado pero no habilitado como falla.
- Motivo: el operador aplica el arreglo con el botón **Actualizar**; un deploy
  que termina "ok" con el botón muerto es el defecto original.
- Aceptación: `deploy/tests/test_deploy_sh_units.sh` cubre los cinco caminos.

## S6 — Documentación

`deploy/README-deploy.md` DEBE explicar cómo instalar o reparar estas piezas sin
volver a correr `install.sh`, y la tabla de estados DEBE incluir el caso "sin
recoger". `deploy/deploy.env.example` DEBE documentar `AUTODEPLOY_UNIT` y
`APPLY_UNIT`.

- Aceptación: ningún documento manda correr `install.sh` para arreglar el botón.
