#!/usr/bin/env bash
#
# Instala las piezas del boton "Aplicar cambios" del panel de entorno
# (Configuracion -> Entorno del servidor, change 033-entorno-servidor).
#
# Por que existe si `install.sh` ya las instala: `install.sh` es un instalador
# completo — pips, migraciones, .env, preguntas — y no se vuelve a correr en un
# servidor que ya esta sirviendo. Las unidades systemd, en cambio, NO viajan con
# el deploy: `deploy.sh` actualiza el checkout y reinicia los servicios, y a
# proposito no escribe en /etc/systemd/system, porque el checkout lo escribe el
# usuario del sitio y un unit escrito ahi seria un archivo root-ejecutable bajo
# su control.
#
# De esa division sale el agujero que este script cierra: un servidor se
# actualiza a un commit que depende de una unidad que nunca se instalo. El deploy
# reinicia, el panel dice "actualizado", y el boton "Aplicar cambios" escribe su
# peticion en un directorio que nadie vigila. `deploy.sh` ahora se niega a
# desplegar en ese estado y manda aca.
#
# Es idempotente y no interactivo. No toca el .env, ni la base, ni el venv, ni el
# codigo: instala tres archivos, recarga systemd y habilita el .path.
#
# Uso:
#   sudo bash deploy/install-apply-units.sh
#   sudo bash deploy/install-apply-units.sh --help
#
# Variables (mismos nombres y defaults que install.sh y deploy.env):
#   APP_DIR, CONFIG_DIR, CONFIG_FILE, SERVICE_USER, SERVICE_GROUP,
#   DEPLOY_LOGIN_USER, GUNICORN_UNIT, HUEY_UNIT, APPLY_UNIT, APPLY_SCRIPT,
#   APPLY_STATE_DIR, SYSTEMCTL, UNIT_DIR, DRY_RUN
#
set -Eeuo pipefail

CONFIG_DIR=${CONFIG_DIR:-/etc/webcmp}
CONFIG=${CONFIG:-$CONFIG_DIR/deploy.env}
# Un `source` de un archivo que no existe es un error con `set -e`, y este script
# tiene que poder correr en un servidor donde deploy.env no esta (instalacion a
# mano). Se carga solo si se puede leer.
if [ -r "$CONFIG" ]; then
    # shellcheck disable=SC1090
    source "$CONFIG"
fi

: "${APP_DIR:=/srv/webcmp}"
# `deploy.env` nombra DEPLOY_USER al dueno del checkout, que es el mismo usuario
# del servicio. install.sh los distingue porque tambien crea al usuario de
# despliegue; aca alcanza con no confundir el grupo del servicio.
: "${SERVICE_USER:=${DEPLOY_USER:-webcmp}}"
: "${SERVICE_GROUP:=$SERVICE_USER}"
: "${DEPLOY_LOGIN_USER:=deploy}"
: "${GUNICORN_UNIT:=webcmp}"
: "${HUEY_UNIT:=webcmp-huey}"
: "${APPLY_UNIT:=webcmp-apply}"
: "${APPLY_SCRIPT:=/usr/local/sbin/webcmp-apply-env}"
: "${APPLY_STATE_DIR:=/var/lib/webcmp}"
: "${SYSTEMCTL:=systemctl}"
: "${DRY_RUN:=0}"
: "${UNIT_DIR:=/etc/systemd/system}"

REQUEST_FILE=$APPLY_STATE_DIR/apply-request

# >>> TEST-BLOCK: unit-files
log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Una peticion en disco se dispara en el instante en que el .path se habilita:
# PathExists= reacciona a "el archivo esta". Habilitar con una peticion vieja
# reinicia el sitio sin que nadie lo haya pedido ahora y fuera de la ventana de
# mantenimiento, asi que se aborta y se pide la decision explicita.
refuse_pending_request() {
    [ -e "$REQUEST_FILE" ] || return 0
    printf '\033[1;31mERROR:\033[0m Hay una peticion de reinicio sin recoger en %s.\n' \
        "$REQUEST_FILE" >&2
    printf 'Habilitar el .path ahora dispararia ese reinicio de inmediato, fuera de la
ventana de mantenimiento. Si es lo que queres, borrala y volve a correr:
  sudo rm -f %s\n' "$REQUEST_FILE" >&2
    return 1
}

# Renderiza `src` en `dest` sustituyendo pares `viejo<>nuevo`.
#
# Las rutas de /srv/webcmp y el nombre del ejecutor estan escritos en los unit a
# proposito (en un unit, `APP_DIR=/srv/webcmp` no es una clave valida de systemd),
# asi que un checkout o un ejecutor en otra ruta necesitan reescribir el archivo.
# El reemplazo es literal —el patron va entre comillas en `${var//patron/texto}`—,
# no un regex: `webcmp` como patron tambien matchearia `webcmp-huey`.
render_unit() {
    local src=$1 dest=$2 line out pair old new
    shift 2
    : > "$dest"
    while IFS= read -r line || [ -n "$line" ]; do
        out=$line
        for pair in "$@"; do
            old=${pair%%<>*}
            new=${pair#*<>}
            out=${out//"$old"/"$new"}
        done
        printf '%s\n' "$out" >> "$dest"
    done < "$src"
}
# <<< TEST-BLOCK: unit-files

# Todo lo que muta el sistema pasa por aca: con DRY_RUN se imprime y no se
# ejecuta, que es lo unico que hace util el modo en un servidor real.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

# `install`, pero solo si el destino no es ya identico. Reescribir un archivo
# identico cambia su mtime sin motivo y hace que "no cambio nada" sea imposible
# de comprobar desde la salida.
install_if_changed() {
    local src=$1 dest=$2 mode=$3 owner=${4:-root} group=${5:-root}
    # `10#` quita los ceros de la izquierda: se pide `0755` y `stat -c %a`
    # devuelve `755`, asi que sin esto la comparacion nunca puede ser cierta.
    if [ -f "$dest" ] && cmp -s "$src" "$dest" \
        && [ "$(stat -c '%U:%G %a' "$dest")" = "$owner:$group $((10#$mode))" ]
    then
        printf '  %s ya esta al dia (%s:%s %s)\n' "$dest" "$owner" "$group" "$((10#$mode))"
        return 0
    fi
    run install -o "$owner" -g "$group" -m "$mode" "$src" "$dest"
}

ensure_dir() {
    local dir=$1 mode=$2 owner=$3 group=$4
    if [ -d "$dir" ] && [ "$(stat -c '%U:%G %a' "$dir")" = "$owner:$group $((10#$mode))" ]
    then
        printf '  %s ya esta al dia (%s:%s %s)\n' "$dir" "$owner" "$group" "$((10#$mode))"
        return 0
    fi
    run install -d -o "$owner" -g "$group" -m "$mode" "$dir"
}

# Renderiza un unit del checkout y lo instala con su modo definitivo.
install_unit() {
    local src=$1 dest=$2 tmp
    shift 2
    tmp=$(mktemp)
    render_unit "$src" "$tmp" "$@"
    install_if_changed "$tmp" "$dest" 0644
    rm -f "$tmp"
}

usage() {
    cat <<'EOF'
Instala las piezas del boton "Aplicar cambios" del panel de entorno.

  sudo bash deploy/install-apply-units.sh

Instala /usr/local/sbin/webcmp-apply-env y las unidades webcmp-apply.service y
webcmp-apply.path, recarga systemd y habilita el .path. No toca el .env, la base,
el venv ni el codigo. Idempotente: una segunda corrida no reescribe nada.

Variables: APP_DIR, CONFIG_FILE, SERVICE_GROUP, DEPLOY_LOGIN_USER, GUNICORN_UNIT,
HUEY_UNIT, APPLY_UNIT, APPLY_SCRIPT, APPLY_STATE_DIR, DRY_RUN=1.
EOF
}

if [ "${1:-}" = '--help' ] || [ "${1:-}" = '-h' ]; then
    usage
    exit 0
fi
[ $# -eq 0 ] || die "Opcion desconocida: $1 (--help para el uso)."

[ "$(id -u)" -eq 0 ] || die "Este script necesita root: escribe en /etc/systemd/system y habilita unidades."

# >>> TEST-BLOCK: install-apply-units
#
# El trabajo privilegiado, en una funcion, para que las pruebas lo puedan
# ejercitar de punta a punta con un arbol temporal y un `systemctl` de mentira.
install_apply_units() {
    local piece

    # Los nombres de ORIGEN son los del repositorio y no cambian: `install.sh`
    # renderiza `deploy/systemd/webcmp-apply.service` a
    # `/etc/systemd/system/$APPLY_UNIT.service`. Buscar `$APPLY_UNIT.service`
    # dentro del checkout fallaria con cualquier nombre configurado.
    for piece in "$APP_DIR/deploy/apply-env.sh" \
        "$APP_DIR/deploy/systemd/webcmp-apply.service" \
        "$APP_DIR/deploy/systemd/webcmp-apply.path"
    do
        [ -f "$piece" ] \
            || die "No existe $piece. Corre el script desde el checkout (APP_DIR=$APP_DIR)."
    done

    refuse_pending_request || exit 1

    log "Boton 'Aplicar cambios' del panel (ejecutor: root)"

    log 'Directorio de estado'
    # 2775 y no 0755: el proceso web (grupo $SERVICE_GROUP) tiene que poder crear
    # la peticion, y el ejecutor escribe el estado en el mismo directorio.
    ensure_dir "$APPLY_STATE_DIR" 2775 "$DEPLOY_LOGIN_USER" "$SERVICE_GROUP"

    log 'Ejecutor'
    # root:root en /usr/local/sbin, NUNCA dentro del checkout: ese directorio lo
    # escribe el usuario del sitio, y un script que corre como root y vive en un
    # directorio escribible por el proceso web es la escalada mas directa que
    # existe.
    install_if_changed "$APP_DIR/deploy/apply-env.sh" "$APPLY_SCRIPT" 0755 root root

    log 'Unidades systemd'
    # Los servicios que se reinician estan escritos en el unit y no salen del
    # archivo de peticion: el ejecutor corre como root y no puede aceptar ordenes.
    install_unit "$APP_DIR/deploy/systemd/webcmp-apply.service" \
        "$UNIT_DIR/$APPLY_UNIT.service" \
        "/srv/webcmp<>$APP_DIR" \
        "ExecStart=/usr/local/sbin/webcmp-apply-env<>ExecStart=$APPLY_SCRIPT" \
        "Group=webcmp<>Group=$SERVICE_GROUP" \
        "UNITS=webcmp.service webcmp-huey.service<>UNITS=$GUNICORN_UNIT.service $HUEY_UNIT.service"

    install_unit "$APP_DIR/deploy/systemd/webcmp-apply.path" \
        "$UNIT_DIR/$APPLY_UNIT.path" \
        "/srv/webcmp<>$APP_DIR" \
        "/var/lib/webcmp/apply-request<>$REQUEST_FILE" \
        "Unit=webcmp-apply.service<>Unit=$APPLY_UNIT.service"

    run "$SYSTEMCTL" daemon-reload
    # `enable --now` sobre el .path y no sobre el .service: el servicio se dispara
    # cuando aparece la peticion, y arrancarlo ahora reiniciaria el sitio sin que
    # nadie lo haya pedido.
    run "$SYSTEMCTL" enable --now "$APPLY_UNIT.path"

    if [ "$DRY_RUN" -eq 0 ]; then
        "$SYSTEMCTL" is-active --quiet "$APPLY_UNIT.path" \
            || die "El .path no quedo activo. Revisa: $SYSTEMCTL status $APPLY_UNIT.path"
    fi

    log 'Listo'
    printf '  peticion: %s\n' "$REQUEST_FILE"
    printf '  ejecutor: %s (usuario root)\n' "$APPLY_SCRIPT"
    printf '  estado:   %s/apply-status.json\n' "$APPLY_STATE_DIR"
    printf '  logs:     journalctl -u %s\n' "$APPLY_UNIT"
}
# <<< TEST-BLOCK: install-apply-units

install_apply_units
