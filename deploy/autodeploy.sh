#!/usr/bin/env bash
#
# Ejecutor del boton "Actualizar" del panel (change 025-update-web-deploy).
#
# Lo llama systemd como el usuario `deploy` cuando aparece
# /var/lib/webcmp/deploy-request. Este usuario tiene UNA sola regla de sudoers
# (/usr/local/sbin/webcmp-deploy) y este script no pide nada mas: el proceso web
# (`webcmp`) escribe un archivo y jamas escala privilegios.
#
# Decision que importa: el contenido del archivo de peticion NO se usa como
# destino. Solo avisa "hay una peticion". El commit se resuelve aca contra
# refs/heads/main, asi que aunque el proceso web este comprometido y escriba
# cualquier cosa, lo maximo que consigue es desplegar lo que ya esta en main.
#
# Estado que escribe (JSON) para que el panel lo muestre:
#   {"status": "running|ok|failed", "sha": "<sha|null>", "finished_at": "<iso|null>"}
#
set -Eeuo pipefail

readonly REPO_URL=${REPO_URL:-https://github.com/cmp-camaguey/web-cmw-insmet-cu.git}
readonly BRANCH_REF=${BRANCH_REF:-refs/heads/main}
readonly STATUS_FILE=${STATUS_FILE:-/var/lib/webcmp/deploy-status.json}
readonly REQUEST_FILE=${REQUEST_FILE:-/var/lib/webcmp/deploy-request}
readonly DEPLOY_SCRIPT=${DEPLOY_SCRIPT:-/usr/local/sbin/webcmp-deploy}
# El lock vive en el MISMO directorio que el estado, y no en /var/lock: este
# script corre como el usuario de despliegue, no como root, y /run/lock es
# root:root 0755 — crear el archivo ahi falla con "Permission denied" antes de
# cualquier otra cosa. `deploy.sh` si puede usar /var/lock porque lo invoca sudo.
readonly LOCKFILE=${LOCKFILE:-/var/lib/webcmp/autodeploy.lock}

log() { printf '%s autodeploy: %s\n' "$(date -Is)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# Instante en que este intento empezo. Va en las TRES escrituras de estado, no
# solo en `running`: asi el panel puede distinguir un despliegue en curso de uno
# que quedo huerfano porque systemd mato la unidad antes de escribir el final
# (`RUN_STALE_SECONDS` en apps/dashboard/views/dashboard/update.py).
STARTED_AT=$(date -Is)

# Escritura atomica: el panel lee este archivo en cada visita al dashboard y no
# puede encontrarse con un JSON a medias.
write_status() {
    local status=$1 sha=${2:-} tmp
    tmp=$(mktemp "${STATUS_FILE}.XXXXXX")
    printf '{"status": "%s", "sha": %s, "started_at": "%s", "finished_at": %s}\n' \
        "$status" \
        "$([ -n "$sha" ] && printf '"%s"' "$sha" || printf 'null')" \
        "$STARTED_AT" \
        "$([ "$status" = running ] && printf 'null' || printf '"%s"' "$(date -Is)")" \
        > "$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "$STATUS_FILE"
}

# La peticion se consume ANTES de tomar el lock, y esto es deliberado.
#
# Si se consumiera despues, un segundo disparo mientras el primero despliega
# moriria en el `flock` y dejaria el archivo en disco. Como `PathExists=` solo
# reacciona a la transicion ausente->presente, ese archivo no volveria a disparar
# nada y, con el `O_EXCL` del panel, bloquearia TODAS las peticiones siguientes
# hasta que alguien lo borrara a mano. Un boton pegado sin un error visible.
if [ -e "$REQUEST_FILE" ]; then
    log "Peticion recibida: $(head -c 500 "$REQUEST_FILE" 2>/dev/null | tr -d '\n')"
    rm -f "$REQUEST_FILE"
else
    log "Sin peticion en $REQUEST_FILE; se comprueba igual."
fi

# Un solo ejecutor. Si ya hay uno desplegando, la peticion se descarta: el panel
# sigue en `running` por el otro y el commit a desplegar es el mismo, asi que
# encolar un segundo despliegue solo agrega tiempo sin cambiar el resultado.
# Sale 0 a proposito: para systemd esto no es un fallo.
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    log "Ya hay un ejecutor corriendo ($LOCKFILE). Peticion descartada."
    exit 0
fi

command -v git >/dev/null 2>&1 || die "git no esta instalado."
[ -x "$DEPLOY_SCRIPT" ] || die "No existe $DEPLOY_SCRIPT."
[ -d "$(dirname "$STATUS_FILE")" ] \
    || die "No existe $(dirname "$STATUS_FILE"): corre deploy/install.sh."

# GIT_TERMINAL_PROMPT=0: sin terminal, un pedido de credenciales colgaria la
# unidad hasta el TimeoutStartSec.
# El `|| true` no sobra: con `set -e` y `pipefail`, un `git ls-remote` que falle
# mata el script en la asignacion, sin log y sin estado escrito, y el panel se
# queda en "desplegando" para siempre. Aca se quiere llegar al `die` de abajo.
target=$(GIT_TERMINAL_PROMPT=0 git ls-remote "$REPO_URL" "$BRANCH_REF" \
    | awk 'NR==1 {print $1}') || true
[[ "$target" =~ ^[0-9a-f]{40}$ ]] || die "No se pudo resolver $BRANCH_REF en $REPO_URL."

write_status running "$target"
log "Desplegando $target"

# `sudo -n`: nunca preguntar. El sudoers de deploy cubre exactamente este
# comando; si faltara, esto falla en vez de quedarse esperando una contrasena
# que nadie va a escribir.
if sudo -n "$DEPLOY_SCRIPT" "$target"; then
    write_status ok "$target"
    log "Despliegue OK: $target"
else
    write_status failed "$target"
    die "El despliegue fallo. Ver: journalctl -u webcmp-autodeploy"
fi
