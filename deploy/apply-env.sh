#!/usr/bin/env bash
#
# Ejecutor del boton "Aplicar cambios" del panel de entorno
# (change 033-entorno-servidor).
#
# Lo llama systemd cuando aparece /var/lib/webcmp/apply-request, que el panel
# acaba de escribir. El proceso web (`webcmp`, shell /bin/false) no puede
# reiniciar nada y no debe poder: `systemctl` desde ahi seria darle root a la
# aplicacion. Por eso escribe un archivo y se desentiende.
#
# Decision que importa: el contenido del archivo de peticion NO se usa como
# destino ni como comando. Solo se copia el nombre de quien lo pidio, y saneado.
# Los servicios que se reinician estan escritos aca, asi que un proceso web
# comprometido consigue, como maximo, reiniciar los dos servicios que ya estaban
# declarados — algo que de todos modos puede lograr dejando caer el proceso.
#
# Estado que escribe (JSON) para que el panel lo muestre:
#   {"status": "running|ok|failed", "requested_by": "<nombre>",
#    "started_at": "<iso>", "finished_at": "<iso|null>", "error": "<texto>"}
#
set -Eeuo pipefail

readonly STATUS_FILE=${STATUS_FILE:-/var/lib/webcmp/apply-status.json}
readonly REQUEST_FILE=${REQUEST_FILE:-/var/lib/webcmp/apply-request}
# El lock vive junto al estado y no en /var/lock por la misma razon que en
# autodeploy.sh: /run/lock es root:root 0755 y este script no necesita escribir
# ahi para nada.
readonly LOCKFILE=${LOCKFILE:-/var/lib/webcmp/apply.lock}
readonly SYSTEMCTL=${SYSTEMCTL:-systemctl}
# Los servicios se reinician SIEMPRE juntos: Huey manda correos y genera PDF con
# la configuracion que leyo al arrancar, asi que reiniciar solo el sitio deja al
# worker con la vieja. `UNITS` existe para las pruebas, no para el servidor.
readonly UNITS_RAW=${UNITS:-webcmp.service webcmp-huey.service}
read -r -a UNITS <<<"$UNITS_RAW"

log() { printf '%s apply-env: %s\n' "$(date -Is)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# Instante en que este intento empezo. Va en las TRES escrituras de estado para
# que el panel distinga un reinicio en curso de uno que quedo huerfano porque
# systemd mato la unidad antes de escribir el final (`RUN_STALE_SECONDS` en
# apps/core/env_apply.py).
STARTED_AT=$(date -Is)

# >>> TEST-BLOCK: apply-status-json
# Solo caracteres que no pueden romper el JSON. El nombre de usuario de Django
# ya esta limitado a [\w.@+-], pero el archivo de peticion lo escribe el proceso
# web y aca no se confia en su contenido: una comilla sin sanear dejaria el JSON
# invalido y el panel mostraria "sin confirmar" para siempre.
json_safe() {
    printf '%s' "$1" | tr -cd 'A-Za-z0-9 ._,:@+-' | cut -c1-150
}

# Escritura atomica: el panel lee este archivo en cada visita y no puede
# encontrarse con un JSON a medias.
write_status() {
    local status=$1 error=${2:-} tmp
    tmp=$(mktemp "${STATUS_FILE}.XXXXXX")
    printf '{"status": "%s", "requested_by": "%s", "started_at": "%s", "finished_at": %s, "error": "%s"}\n' \
        "$status" \
        "$REQUESTED_BY" \
        "$STARTED_AT" \
        "$([ "$status" = running ] && printf 'null' || printf '"%s"' "$(date -Is)")" \
        "$(json_safe "$error")" \
        > "$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "$STATUS_FILE"
}
# <<< TEST-BLOCK: apply-status-json

# La peticion se consume ANTES del lock, por la misma razon que en autodeploy.sh:
# `PathExists=` solo reacciona a la transicion ausente->presente, asi que un
# archivo que quede en disco no vuelve a disparar nada y, con el `O_EXCL` del
# panel, bloquearia todas las peticiones siguientes hasta que alguien lo borre a
# mano.
REQUESTED_BY=''
if [ -e "$REQUEST_FILE" ]; then
    # >>> TEST-BLOCK: apply-request-reader
    REQUESTED_BY=$(sed -n \
        's/.*"requested_by"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$REQUEST_FILE" 2>/dev/null | head -n1)
    REQUESTED_BY=$(json_safe "$REQUESTED_BY")
    # <<< TEST-BLOCK: apply-request-reader
    log "Peticion recibida de: ${REQUESTED_BY:-desconocido}"
    rm -f "$REQUEST_FILE"
else
    log "Sin peticion en $REQUEST_FILE; se aplica igual."
fi

# Un solo ejecutor. Si ya hay uno reiniciando, esta peticion se descarta: el
# panel sigue en `running` por el otro y el resultado seria el mismo. Sale 0 a
# proposito, para systemd esto no es un fallo.
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    log "Ya hay un reinicio en curso ($LOCKFILE). Peticion descartada."
    exit 0
fi

if ! command -v "$SYSTEMCTL" >/dev/null 2>&1; then
    if [ -d "$(dirname "$STATUS_FILE")" ]; then
        write_status failed "no se encontro $SYSTEMCTL"
    fi
    die "No se encontro $SYSTEMCTL."
fi
[ -d "$(dirname "$STATUS_FILE")" ] \
    || die "No existe $(dirname "$STATUS_FILE"): corre deploy/install.sh."

write_status running
log "Reiniciando: ${UNITS[*]}"

# `restart` y no `reload`: Gunicorn re-forkea sus workers desde un master que ya
# tiene el codigo y el entorno viejos, asi que un reload no cambia el valor de
# ninguna variable. Huey no tiene reload en absoluto.
if ! "$SYSTEMCTL" restart "${UNITS[@]}"; then
    write_status failed 'systemctl restart devolvio un error'
    die "El reinicio fallo. Ver: journalctl -u webcmp-apply"
fi

write_status ok
log "Reinicio OK: ${UNITS[*]}"
