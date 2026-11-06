#!/usr/bin/env bash
# Despliegue de web-cmw-insmet-cu en el servidor de produccion.
#
# Se ejecuta EN el servidor, como root, y es la fuente unica de verdad del
# procedimiento. El workflow .github/workflows/deploy-production.yml lo invoca
# por SSH; el
# operador tambien puede correrlo a mano cuando Actions no llega o cuando hay que
# volver atras sin pasar por un PR.
#
#   sudo /usr/local/sbin/webcmp-deploy <commit-sha> [ref-de-respaldo]
#   sudo /usr/local/sbin/webcmp-deploy --configure   # (re)escribe /etc/webcmp/deploy.env
#   sudo /usr/local/sbin/webcmp-deploy --check        # valida la config, no despliega
#
# Que hace, en este orden (el orden ES la garantia):
#   1. lock, para que dos despliegues no se pisen
#   2. git checkout del SHA exacto
#   3. pip install requirements/prod.txt
#   4. gate 1: makemigrations --check (el modelo cambio y no hay migracion)
#   5. gate 2: check --deploy con la configuracion REAL de produccion
#   6. migrate  (antes de que el codigo nuevo atienda requests)
#   7. collectstatic (antes de que el codigo nuevo sirva HTML que los referencia)
#   8. restart de webcmp y webcmp-huey (reload NO sirve: ver nota de Gunicorn)
#   9. health check contra el reverse proxy que corresponda (ver HEALTHCHECK_*)
#
# Si algo falla despues del punto 6, se revierte el codigo y se reinicia. La
# base de datos NO se revierte sola: Django no genera migraciones inversas y una
# operacion destructiva (DROP COLUMN) no tiene hacia atras. El script lo dice por
# pantalla y quien lee la salida sabe que tiene que mirar la base a mano.
#
# INSTALACION (una vez, y de ahi en adelante el archivo vive FUERA del checkout):
#   sudo install -o root -g root -m 0755 deploy/deploy.sh /usr/local/sbin/webcmp-deploy
#
# Fuera del checkout a proposito: este script corre como root y el checkout es
# escribible por el usuario de servicio. Si el script se ejecutara desde
# /srv/webcmp/deploy/deploy.sh con sudo, bastaria editar el archivo para obtener
# root: es una escalada de privilegios trivial. En /usr/local/sbin, propiedad de
# root, el dueño de la clave de despliegue no puede escribirlo.

set -Eeuo pipefail

readonly LOCKFILE=/var/lock/webcmp-deploy.lock
readonly CONFIG=/etc/webcmp/deploy.env
# Commit que quedo sirviendo, para el panel (change 025-update-web-deploy).
# Lo escribe este script y no el panel: el proceso web no puede saber con
# certeza que commit se aplico, solo lo sabe el que lo aplico.
readonly DEPLOYED_STATE=/etc/webcmp/deployed

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mAVISO:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,36p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

# TODO git corre como DEPLOY_USER, nunca como root.
#
# El checkout es de webcmp y este script corre como root. Git >= 2.35.2 se niega a
# trabajar sobre un repo de otro usuario ("detected dubious ownership") por una
# buena razon: un repo escribible por otro usuario puede tener un .git/config que
# ejecute comandos. El sintoma es un "fatal: dubious ownership" en el primer
# `git rev-parse`, antes de tocar nada.
#
# Correr git con el dueno del checkout es lo que pedia el bug de entrada. La
# alternativa, agregar /srv/webcmp a safe.directory de root, teaches a root a
# confiar en un repo que webcmp controla: lo mismo que hace el check, pero
# permanente y global.
gitapp() {
    sudo -u "$DEPLOY_USER" git -C "$APP_DIR" "$@"
}

# --------------------------------------------------------------------------
# Modos: deploy (por defecto, el camino de CI) | --configure | --check
# --------------------------------------------------------------------------
#
# Se resuelve ANTES de sourcear la config y antes de todo preflight, y por una
# razon que no es de estilo: los modos --configure y --check tienen que poder
# correr sobre una configuracion INCOMPLETA. Un deploy que valida su propia
# configuracion antes de dejar arreglarla deja al operador sin salida: si
# --check exigiera que el venv exista, no habria forma de preparar el servidor
# desde cero con este script.
#
# `shift` despues de cada opcion es lo que mantiene intacto el contrato de
# argumentos que el workflow ya depende: `webcmp-deploy <sha> [ref-de-respaldo]`.
MODE=deploy
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)     usage ;;
        --configure)   MODE=configure; shift; break ;;
        --check)       MODE=check; shift; break ;;
        *)             break ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "Este script necesita root (systemctl, systemd-run, $CONFIG)."

# --------------------------------------------------------------------------
# Preguntas
# --------------------------------------------------------------------------
#
# Las prompts viven DUPLICADAS en install.sh y no se comparten por `source`, y
# la duplicacion es deliberada. install.sh esta en el checkout, y el checkout es
# exactamente lo que este script actualiza con `git checkout --force`: sourcear
# el instalador desde aca ejecutaria, como root, codigo recien bajadod de un
# push. El instalador es la fuente de prompts, pero no puede ser una dependencia
# de tiempo de ejecucion del deploy.
#
# Reglas de las tres funciones, iguales en ambos scripts:
#   - contestar vacio = conservar el default
#   - la validacion vive en una funcion aparte, para que el mensaje de error
#     pueda ser especifico del campo en vez de "entrada invalida"
#   - reintentos acotados y despues die: un bucle infinito de repregunta en un
#     deploy SSH deja al workflow colgado hasta que vence su timeout
MAX_PROMPT_TRIES=3

valid_hostname() {
    # Un `server_name` de Nginx o un ALLOWED_HOSTS con espacios, dos puntos o
    # barras convierte el health check en algo que no se puede parsear y el
    # `source` de deploy.env en algo que puede inyectar codigo. Se acota aca,
    # una vez, en vez de confiar en que el operador no escriba eso.
    [ -n "$1" ] || return 1
    case "$1" in
        *[!a-zA-Z0-9.-]*) return 1 ;;
        -* | .* | *..*) return 1 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$'
}

valid_abs_path() {
    [ -n "$1" ] || return 1
    case "$1" in
        /*) ;;
        *) return 1 ;;
    esac
    case "$1" in
        *[[:space:]]* | *'"'* | *"'"* | *'$'* | *'`'*) return 1 ;;
    esac
    return 0
}

valid_url() {
    # Solo esquema http/https y un host no vacio. La validacion REAL (que curl
    # pueda usarlo) la hace el health check; esta es la que evita que un valor
    # con espacio o sin esquema llegue a curl y produzca un error que no dice
    # "tu HEALTHCHECK_URL esta mal".
    local value=$1 rest
    case "$value" in
        http://* | https://*) ;;
        *) return 1 ;;
    esac
    case "$value" in
        *[[:space:]]* | *'"'* | *'$'* | *'`'*) return 1 ;;
    esac
    rest=${value#*://}
    [ -n "${rest%%/*}" ]
}

valid_socket_path() {
    valid_abs_path "$1"
}

# Formato de `curl --resolve`: host:port:ip, o la palabra `none` para decir
# "sal por DNS de verdad". Un valor mal formado aqui no lo detecta curl con un
# error util: `--resolve` se parsea al abrir la conexion y un valor raro produce
# un fallo de red que se lee como "el sitio esta caido".
valid_resolve() {
    [ "$1" = none ] && return 0
    printf '%s' "$1" | grep -Eq '^[a-zA-Z0-9*._-]+:[0-9]{1,5}:[0-9a-fA-F.:*]+$'
}

valid_unit_name() {
    [ -n "$1" ] || return 1
    case "$1" in
        *[!a-zA-Z0-9._@-]*) return 1 ;;
    esac
    return 0
}

# ask <NOMBRE_VAR> <default> <pregunta> [validador]
#
# Devuelve el valor por stdout y escribe TODO lo demas en stderr. La separacion
# no es cosmetica: el llamador hace `X=$(ask ...)`, asi que cualquier texto por
# stdout que no sea la respuesta terminaria dentro del valor, y un "[dry-run]"
# impreso a stdout se comeria el dominio.
ask() {
    local var_name=$1 default=$2 question=$3 validator=${4:-}
    local answer='' tries=0
    while :; do
        printf '%s [%s]: ' "$question" "$default" >&2
        IFS= read -r answer || die "EOF leyendo la respuesta de $var_name."
        answer=${answer:-$default}
        if [ -z "$validator" ] || "$validator" "$answer" >/dev/null 2>&1; then
            printf '%s' "$answer"
            return 0
        fi
        tries=$((tries + 1))
        printf '  Valor invalido para %s.\n' "$var_name" >&2
        if [ "$tries" -ge "$MAX_PROMPT_TRIES" ]; then
            printf '  %s intentos agotados.\n' "$MAX_PROMPT_TRIES" >&2
            return 1
        fi
        printf '  Reintentos restantes: %s\n' "$((MAX_PROMPT_TRIES - tries))" >&2
    done
}

# ask_yes_no <default si|no> <pregunta>
#
# Acepta s/si/yes/y, n/no. El default se imprime explicito para que una
# respuesta vacia sea una decision y no un descuido del operador.
#
# Reintentos ACOTADOS, y una respuesta que no sea si/no NO se asume como el
# default: un `--configure` que reescriba /etc/webcmp/deploy.env porque alguien
# escribio "dale" en vez de "si" falla en silencio, y el deploy que sigue
# despues usa una config que nadie eligio.
ask_yes_no() {
    local default=$1 question=$2 answer='' other tries=0
    other=$([ "$default" = si ] && echo no || echo si)
    while :; do
        printf '%s [%s/%s] (vacio = %s): ' "$question" "$default" "$other" "$default" >&2
        IFS= read -r answer || die "EOF leyendo la respuesta."
        answer=${answer:-$default}
        case "$answer" in
            s | S | si | SI | yes | y | Y) return 0 ;;
            n | N | no | NO) return 1 ;;
        esac
        tries=$((tries + 1))
        printf '  Se entiende si/no.\n' >&2
        if [ "$tries" -ge "$MAX_PROMPT_TRIES" ]; then
            printf '  %s intentos agotados: no se escribe nada.\n' "$MAX_PROMPT_TRIES" >&2
            return 1
        fi
    done
}

# --------------------------------------------------------------------------
# Escritura de archivos KEY=VALUE
# --------------------------------------------------------------------------
#
# El valor viaja por el ENTORNO y no por `-v` de awk a proposito: awk procesa
# escapes en el valor de `-v`, asi que una contrasena con `\n`, `\t` o `\1`
# llegaria al archivo como dos caracteres. Por ENVIRON el valor sale literal,
# que es lo unico aceptable para un archivo que contiene contrasenas.
env_set() {
    local file=$1 key=$2 value=$3 tmp
    [ -f "$file" ] || : > "$file"
    tmp=$(mktemp)
    EV_KEY=$key EV_VALUE=$value awk '
        BEGIN { key = ENVIRON["EV_KEY"]; value = ENVIRON["EV_VALUE"]; seen = 0 }
        index($0, key "=") == 1 { print key "=" value; seen = 1; next }
        { print }
        END { if (!seen) print key "=" value }
    ' "$file" > "$tmp" || { rm -f "$tmp"; return 1; }
    cat "$tmp" > "$file"
    rm -f "$tmp"
}

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

# OJO: en modo --configure NO se exige que el archivo exista, porque este modo
# existe justamente para CREARLO. El guard de mas abajo mataba el flag en un host
# limpio con `No existe /etc/webcmp/deploy.env`, 167 lineas antes de llegar a la
# funcion que si sabe escribirlo (`configure_deploy_env`, con su `[ ! -f ]`).
# Los prompts de esa funcion leen $APP_DIR, $PUBLIC_HOSTNAME y las HEALTHCHECK_*,
# asi que sin `source` tienen que venir de los defaults de mas abajo, que en un
# host recien instalado son los mismos valores que install.sh escribe.
if [ "$MODE" != configure ]; then
    [ -r "$CONFIG" ] || die "No existe $CONFIG (root, 600). Vease deploy/README-deploy.md."
    # shellcheck disable=SC1090
    source "$CONFIG"
fi

# Se captura ESTA DEFINIDA antes de aplicar los defaults, y no por estilo.
# `${VAR:=}` no distingue "la clave no esta en deploy.env" de "la clave esta con
# un valor vacio a proposito", y para HEALTHCHECK_RESOLVE la diferencia es
# exactamente la que decide si el health check entra a 127.0.0.1 con SNI o sale
# por DNS de verdad. Con `${VAR+x}` si se distingue, y el default de abajo solo
# aplica al primer caso.
HEALTHCHECK_URL_DEFINED=${HEALTHCHECK_URL+si}
HEALTHCHECK_RESOLVE_DEFINED=${HEALTHCHECK_RESOLVE+si}

: "${APP_DIR:=/srv/webcmp}"
: "${DEPLOY_USER:=webcmp}"
: "${GUNICORN_UNIT:=webcmp}"
: "${HUEY_UNIT:=webcmp-huey}"
: "${ENCRYPTION_ENV:=/etc/webcmp/encryption.env}"
: "${PYTHON:=$APP_DIR/.venv/bin/python}"
# `PIP` NO se invoca por su shim (`$VENV/bin/pip`): ese shim no existe siempre.
# Un venv creado con `uv` no lo trae, y el `.venv` de desarrollo de este repo es
# justo ese caso: tiene `pip3` y `pip3.14`, y NO tiene `pip`. Por eso el comando
# real es `python -m pip`, que no depende del shim.
#
# Son DOS palabras, asi que no puede vivir en un string: `readonly` + quoting
# partirian mal los argumentos. Va en un array. `PIP` se sigue leyendo del
# entorno/deploy.env, y ahi si significa "un unico ejecutable".
if [ -n "${PIP:-}" ]; then
    PIP_CMD=("$PIP")
else
    PIP_CMD=("$PYTHON" -m pip)
fi
: "${PUBLIC_HOSTNAME:=web.cmw.insmet.cu}"
# Modo de proxy declarado por el instalador. NO decide nada en este script: el
# health check se despacha por las claves HEALTHCHECK_* de mas abajo. Se guarda
# porque es el dato que hace falta para explicar un deploy bloqueado ("no hay
# TLS porque el proxy es externo", no "el certificado esta vencido").
: "${PROXY_MODE:=}"
# Destino del health check. Vacio = comportamiento historico: 443 en 127.0.0.1
# con el SNI y el Host correctos. Ver la seccion 8 para el por que de cada clave.
: "${HEALTHCHECK_URL:=}"
# `--resolve host:port:ip`. Con HEALTHCHECK_URL vacio se usa
# "$PUBLIC_HOSTNAME:443:127.0.0.1", que es el default de siempre; con URL puesta
# y esta vacia, el health check sale por DNS de verdad.
: "${HEALTHCHECK_RESOLVE:=}"
# `curl --unix-socket`. Con proxy externo (Nginx Proxy Manager) el upstream no
# es un puerto TCP de la maquina: es el socket de Gunicorn. Es el unico modo de
# probar el mismo camino que el proxy sin abrir un puerto que no hace falta.
: "${HEALTHCHECK_UNIX_SOCKET:=}"
# Cabecera `Host:` explicita. Necesaria con socket unix: curl pone `localhost` y
# Django responde DisallowedHost, que el health check leeria como un sitio caido.
: "${HEALTHCHECK_HOST_HEADER:=}"
# Solo para servidores de prueba con certificado autofirmado. Vacio (lo normal) =
# el health check verifica el TLS de verdad. Ponerlo en produccion anula la
# unica comprobacion que detecta un certificado vencido o ajeno.
: "${HEALTHCHECK_INSECURE:=}"
# Cache de pip. Sin esto no hay ninguno: `webcmp` es una cuenta de sistema sin
# home, pip no encuentra donde escribir ~/.cache y avisa "the cache has been
# disabled". El efecto practico es que cada deploy re-descarga las ruedas desde
# PyPI, lo que multiplica el tiempo del deploy y convierte cada corte de red en un
# deploy fallido. Todas las versiones van pinadas con `==`, asi que un cache no
# puede servir un artefacto equivocado.
: "${PIP_CACHE_DIR:=/var/cache/webcmp-pip}"
# Cache de config de Matplotlib. Mismo motivo que el de pip: sin un directorio
# escribible, cada import regenera el indice de fuentes y escupe un WARNING que
# tapa el resto del log. Lo declara tambien webcmp.service.
: "${MPLCONFIGDIR:=$APP_DIR/.cache/matplotlib}"

readonly APP_DIR DEPLOY_USER GUNICORN_UNIT HUEY_UNIT ENCRYPTION_ENV PYTHON
readonly PUBLIC_HOSTNAME PROXY_MODE HEALTHCHECK_URL HEALTHCHECK_RESOLVE
readonly HEALTHCHECK_UNIX_SOCKET HEALTHCHECK_HOST_HEADER HEALTHCHECK_INSECURE
readonly HEALTHCHECK_URL_DEFINED HEALTHCHECK_RESOLVE_DEFINED
readonly PIP_CACHE_DIR MPLCONFIGDIR
# El array se congela aparte: `readonly PIP_CMD` sin `-a` no protege el contenido
# de un array en bash < 5.0.
readonly -a PIP_CMD

# Validacion del destino del health check, en el arranque y NO solo en el punto
# 8: en --check tiene que ser el reporte, y en un deploy fallar aqui con un
# mensaje sobre la config es mil veces mas util que un curl que responde 000
# veinte segundos despues.
validate_healthcheck_target() {
    if [ -n "$HEALTHCHECK_UNIX_SOCKET" ]; then
        valid_socket_path "$HEALTHCHECK_UNIX_SOCKET" \
            || die "HEALTHCHECK_UNIX_SOCKET='$HEALTHCHECK_UNIX_SOCKET' no es una ruta absoluta limpia (sin espacios, comillas ni \$)."
    fi
    if [ -n "$HEALTHCHECK_HOST_HEADER" ]; then
        valid_hostname "$HEALTHCHECK_HOST_HEADER" \
            || die "HEALTHCHECK_HOST_HEADER='$HEALTHCHECK_HOST_HEADER' no es un hostname valido."
    fi
    [ -z "$HEALTHCHECK_URL" ] && return 0
    valid_url "$HEALTHCHECK_URL" || die "HEALTHCHECK_URL invalida: '$HEALTHCHECK_URL'.
  Tiene que empezar por http:// o https:// y traer un host no vacio, sin espacios.
  Ejemplos validos:  https://web.cmw.insmet.cu/   o   http://localhost/
  O borrar la variable y dejar el default (443 en 127.0.0.1 con SNI)."
    if [ -n "$HEALTHCHECK_RESOLVE" ] && ! valid_resolve "$HEALTHCHECK_RESOLVE"; then
        die "HEALTHCHECK_RESOLVE invalido: '$HEALTHCHECK_RESOLVE'.
  Formato de curl --resolve: host:port:ip. Ejemplo: web.cmw.insmet.cu:443:127.0.0.1
  O la palabra 'none' para que el health check salga por DNS de verdad."
    fi
}

# --------------------------------------------------------------------------
# Modo --configure
# --------------------------------------------------------------------------
#
# Reescribe SOLO las claves que administra este script y deja intactas las que
# haya agregado a mano. Un reescritura total de la config perderia justo los
# campos que el operador ajusto por su cuenta (un proxy distinto, un
# HEALTHCHECK_TIMEOUT, un APP_DIR raro), y perderlos sin avisar es la razon por la
# que el install.sh original se negaba a pisar el archivo.
configure_deploy_env() {
    [ -t 0 ] || die "--configure necesita una terminal: va a preguntar cosas. En un pipeline, pasalas por variable de entorno o edita $CONFIG a mano."

    log "Configuracion de $CONFIG (proxy: ${PROXY_MODE:-sin declarar})"

    cfg_app_dir=$(ask APP_DIR "$APP_DIR" 'Directorio del checkout' valid_abs_path) || die 'Valor invalido.'
    cfg_deploy_user=$(ask DEPLOY_USER "$DEPLOY_USER" 'Usuario dueno del checkout (el que corre git)' valid_unit_name) || die 'Valor invalido.'
    cfg_gunicorn_unit=$(ask GUNICORN_UNIT "$GUNICORN_UNIT" 'Unit de Gunicorn' valid_unit_name) || die 'Valor invalido.'
    cfg_huey_unit=$(ask HUEY_UNIT "$HUEY_UNIT" 'Unit de Huey' valid_unit_name) || die 'Valor invalido.'
    cfg_encryption_env=$(ask ENCRYPTION_ENV "$ENCRYPTION_ENV" 'Archivo con ENCRYPTION_KEY' valid_abs_path) || die 'Valor invalido.'
    cfg_public_hostname=$(ask PUBLIC_HOSTNAME "$PUBLIC_HOSTNAME" 'Hostname publico (server_name de Nginx, EXTERNAL_HOSTNAME del .env)' valid_hostname) || die 'Valor invalido.'

    log "Destino del health check"
    cat >&2 <<'EOF'
  Proxy local (Nginx en esta maquina, TLS aqui):
      HEALTHCHECK_URL=https://<host>/  y  HEALTHCHECK_RESOLVE=<host>:443:127.0.0.1
      Asi entra a 127.0.0.1:443 con el SNI correcto, sin salir del servidor.
  Proxy externo (Nginx Proxy Manager, u otro):
      HEALTHCHECK_URL=http://localhost/  HEALTHCHECK_UNIX_SOCKET=/run/webcmp/gunicorn.sock
      HEALTHCHECK_HOST_HEADER=<host>
      Asi prueba el socket de Gunicorn, que es lo que el proxy externo usa de verdad.
EOF
    cfg_hc_url=$(ask HEALTHCHECK_URL "${HEALTHCHECK_URL:-https://$PUBLIC_HOSTNAME/}" 'URL del health check (vacio = default: https://<PUBLIC_HOSTNAME>/ en 127.0.0.1:443)' valid_url) || die 'Valor invalido.'
    cfg_hc_resolve=$(ask HEALTHCHECK_RESOLVE "${HEALTHCHECK_RESOLVE:-$PUBLIC_HOSTNAME:443:127.0.0.1}" 'curl --resolve host:port:ip (none = sin --resolve; vacio = el default)' valid_resolve) || die 'Valor invalido.'
    cfg_hc_socket=$(ask HEALTHCHECK_UNIX_SOCKET "$HEALTHCHECK_UNIX_SOCKET" 'Socket unix de Gunicorn (vacio = ninguno)' valid_socket_path) || die 'Valor invalido.'
    cfg_hc_host=$(ask HEALTHCHECK_HOST_HEADER "$HEALTHCHECK_HOST_HEADER" 'Cabecera Host: del health check (vacio = la de la URL)' valid_hostname) || die 'Valor invalido.'

    if ask_yes_no "$([ -n "$HEALTHCHECK_INSECURE" ] && echo si || echo no)" \
        'El certificado es autofirmado y el health check debe NO verificar el TLS?'
    then
        cfg_hc_insecure=1
    else
        cfg_hc_insecure=
    fi

    cfg_proxy_mode=$(ask PROXY_MODE "${PROXY_MODE:-nginx-local}" 'Modo de proxy (nginx-local | external)' valid_unit_name) || die 'Valor invalido.'

    if [ ! -f "$CONFIG" ] || ask_yes_no no "Escribir los cambios en $CONFIG?"; then
        [ -f "$CONFIG" ] || cat > "$CONFIG" <<'EOF'
# /etc/webcmp/deploy.env: configuracion de webcmp-deploy. root:root 600.
# Lo escribe `webcmp-deploy --configure`; se puede editar a mano entre corridas.
# NO tiene secretos: por eso puede viver fuera del .env del sitio.
EOF
        env_set "$CONFIG" APP_DIR "$cfg_app_dir"
        env_set "$CONFIG" DEPLOY_USER "$cfg_deploy_user"
        env_set "$CONFIG" GUNICORN_UNIT "$cfg_gunicorn_unit"
        env_set "$CONFIG" HUEY_UNIT "$cfg_huey_unit"
        env_set "$CONFIG" ENCRYPTION_ENV "$cfg_encryption_env"
        env_set "$CONFIG" PUBLIC_HOSTNAME "$cfg_public_hostname"
        env_set "$CONFIG" HEALTHCHECK_URL "$cfg_hc_url"
        env_set "$CONFIG" HEALTHCHECK_RESOLVE "$cfg_hc_resolve"
        env_set "$CONFIG" HEALTHCHECK_UNIX_SOCKET "$cfg_hc_socket"
        env_set "$CONFIG" HEALTHCHECK_HOST_HEADER "$cfg_hc_host"
        env_set "$CONFIG" HEALTHCHECK_INSECURE "$cfg_hc_insecure"
        env_set "$CONFIG" PROXY_MODE "$cfg_proxy_mode"
        # `install "$CONFIG" "$CONFIG"` NO: GNU install se niega con "are the same
        # file". Y el modo importa de verdad: 0600 es lo unico que impide que el
        # usuario de despliegue (que tiene sudo para este script) lea la config.
        chown root:root "$CONFIG"
        chmod 0600 "$CONFIG"
        echo "  $CONFIG actualizado."
        grep -E '^[A-Z_]+=' "$CONFIG" | sed 's/^/    /' >&2
    else
        warn "$CONFIG queda como estaba."
    fi

    log "Sin desplegar. Verificá con: $0 --check"
    exit 0
}

if [ "$MODE" = configure ]; then
    configure_deploy_env
fi

# --------------------------------------------------------------------------
# Modo --check
# --------------------------------------------------------------------------
#
# Reporta, no repara. La razon de que sea un reporte y no un `die` en el primer
# problema: un `--check` que se corta en el primer fallo deja al operador con un
# error por vez y sin la lista completa, que es exactamente lo que hace lento
# configurar un servidor desde cero.
CHECK_FAILED=0

check_ok()   { printf '  \033[1;32mOK\033[0m     %s\n' "$*"; }
check_fail() { printf '  \033[1;31mFALLA\033[0m  %s\n' "$*"; CHECK_FAILED=1; }
check_skip() { printf '  \033[1;33mSIN DATOS\033[0m %s\n' "$*"; }

check_files() {
    log "Archivos"
    if [ -d "$APP_DIR/.git" ]; then
        check_ok "$APP_DIR es un checkout de git."
    else
        check_fail "$APP_DIR no es un checkout de git."
    fi
    if [ -x "$PYTHON" ]; then
        if sudo -u "$DEPLOY_USER" "$PYTHON" -c 'import django' 2>/dev/null; then
            check_ok "venv en $PYTHON (import django funciona)."
        else
            check_fail "El venv de $PYTHON no puede importar django como $DEPLOY_USER."
        fi
    else
        check_skip "No hay venv en $PYTHON todavia (normal antes de la primera instalacion)."
    fi
    if [ -r "$ENCRYPTION_ENV" ]; then
        if grep -q '^ENCRYPTION_KEY=' "$ENCRYPTION_ENV"; then
            check_ok "$ENCRYPTION_ENV tiene ENCRYPTION_KEY."
        else
            check_fail "$ENCRYPTION_ENV no define ENCRYPTION_KEY=."
        fi
    else
        check_fail "No se puede leer $ENCRYPTION_ENV. Sin el, systemd arranca sin descifrar SECRET_KEY."
    fi
}

check_units() {
    log "Unidades systemd"
    for unit in "$GUNICORN_UNIT" "$HUEY_UNIT"; do
        if [ -f "/etc/systemd/system/$unit.service" ]; then
            check_ok "/etc/systemd/system/$unit.service instalado."
        else
            check_fail "Falta /etc/systemd/system/$unit.service (deploy/install.sh lo instala)."
        fi
    done
    if [ -n "$PROXY_MODE" ]; then
        case "$PROXY_MODE" in
            nginx-local) check_ok "PROXY_MODE=$PROXY_MODE (Nginx local: el health check necesita 443 en 127.0.0.1)." ;;
            external)    check_ok "PROXY_MODE=$PROXY_MODE (proxy externo: el health check deberia apuntar al socket o al upstream local)." ;;
            *)           check_fail "PROXY_MODE='$PROXY_MODE' no es un modo conocido (nginx-local | external)." ;;
        esac
    else
        check_skip "PROXY_MODE no declarado. Solo informativo."
    fi
}

check_sudoers() {
    log "Sudoers"
    local sudoers=/etc/sudoers.d/webcmp-deploy
    if [ -r "$sudoers" ]; then
        if visudo -c -f "$sudoers" >/dev/null 2>&1; then
            check_ok "$sudoers es valido."
        else
            check_fail "$sudoers no es valido (visudo -c -f)."
        fi
        if grep -qE "^[[:space:]]*${DEPLOY_USER}[[:space:]]+ALL=" "$sudoers"; then
            check_ok "El sudoers concede sudo a '$DEPLOY_USER'."
        else
            check_fail "El sudoers no concede sudo a '$DEPLOY_USER'."
        fi
    else
        check_fail "No existe $sudoers."
    fi
}

check_healthcheck() {
    log "Health check"
    local resolved=${HEALTHCHECK_URL:-https://$PUBLIC_HOSTNAME/}
    if [ -n "$HEALTHCHECK_UNIX_SOCKET" ]; then
        if [ -S "$HEALTHCHECK_UNIX_SOCKET" ]; then
            check_ok "Socket $HEALTHCHECK_UNIX_SOCKET existe."
        else
            check_fail "HEALTHCHECK_UNIX_SOCKET=$HEALTHCHECK_UNIX_SOCKET no es un socket. El servicio corre?"
        fi
    else
        check_skip "Sin HEALTHCHECK_UNIX_SOCKET: el health check usa la red."
    fi
    if [ -n "$HEALTHCHECK_URL" ]; then
        check_ok "Destino: $resolved"
        if [ -n "$HEALTHCHECK_UNIX_SOCKET" ]; then
            check_ok "El transporte es el socket: --resolve no aplica y no se manda."
        elif [ -z "$HEALTHCHECK_RESOLVE" ] && [ -z "$HEALTHCHECK_RESOLVE_DEFINED" ]; then
            check_ok "Sin HEALTHCHECK_RESOLVE: se usa el default $PUBLIC_HOSTNAME:443:127.0.0.1."
        elif [ "$HEALTHCHECK_RESOLVE" = none ]; then
            check_skip "HEALTHCHECK_RESOLVE=none: la URL se resuelve por DNS desde este servidor. Si el DNS es interno y no resuelve, el health check falla sin que el sitio este caido."
        else
            check_ok "--resolve $HEALTHCHECK_RESOLVE."
        fi
    else
        check_ok "Destino: default https://$PUBLIC_HOSTNAME/ con --resolve $PUBLIC_HOSTNAME:443:127.0.0.1."
    fi
    if [ -n "$HEALTHCHECK_INSECURE" ]; then
        check_fail "HEALTHCHECK_INSECURE esta activo: el health check ya no detecta un certificado vencido o de otro dominio. Solo para servidores de prueba."
    fi
    # El estado del servicio se consulta, no se modifica: `is-active` es de solo
    # lectura y es la unica forma de decir "todo bien" sin desplegar nada.
    if command -v systemctl >/dev/null 2>&1; then
        if systemctl is-active --quiet "$GUNICORN_UNIT"; then
            check_ok "$GUNICORN_UNIT esta activo."
        else
            check_fail "$GUNICORN_UNIT no esta activo (systemctl is-active)."
        fi
    fi
}

check_django() {
    log "Configuracion de Django"
    if [ ! -x "$PYTHON" ]; then
        check_skip "Sin venv no se puede correr manage.py."
        return
    fi
    if ! command -v systemd-run >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
        check_skip "systemd no disponible: no se puede correr manage.py en el contexto de produccion (PRODUCTION=1 + ENCRYPTION_KEY)."
        return
    fi
    # check --deploy sin --fail-level: es un reporte, no un gate. El gate es el
    # punto 4 del deploy.
    local output status=0
    output=$(systemd-run --quiet --pipe --wait \
        --uid="$DEPLOY_USER" --gid="$DEPLOY_USER" \
        -p "EnvironmentFile=$ENCRYPTION_ENV" \
        -p Environment=PRODUCTION=1 \
        -p "Environment=MPLCONFIGDIR=$MPLCONFIGDIR" \
        --working-directory="$APP_DIR" \
        "$PYTHON" manage.py check --deploy 2>&1) || status=$?
    printf '%s\n' "$output" | sed 's/^/    /' >&2
    if [ "$status" -eq 0 ]; then
        check_ok "manage.py check --deploy."
    else
        check_fail "manage.py check --deploy salio con $status."
    fi
}

if [ "$MODE" = check ]; then
    validate_healthcheck_target
    log "Chequeo de $CONFIG (no se despliega nada)"
    check_files
    check_units
    check_sudoers
    check_healthcheck
    check_django
    echo
    if [ "$CHECK_FAILED" -eq 0 ]; then
        log "Todo lo chequeado esta bien."
        exit 0
    fi
    die "Hay chequeos en FALLA. Arriba esta el detalle; no se toco nada."
fi

[ -d "$APP_DIR/.git" ] || die "$APP_DIR no es un checkout de git."
[ -x "$PYTHON" ] || die "No existe el venv: $PYTHON"
validate_healthcheck_target

# El cache es de DEPLOY_USER, no de root: si fuera de root, los artefactos
# cacheados serian ilegibles para el pip del deploy y el cache no serviria de
# nada, que es exactamente el problema que vino a resolver.
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0700 "$PIP_CACHE_DIR"
export PIP_CACHE_DIR
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0700 "$MPLCONFIGDIR"

target_sha=${1:-}
[ -n "$target_sha" ] || die "Uso: $0 <commit-sha> [ref-de-respaldo]"

# --------------------------------------------------------------------------
# Lock: dos pushes seguidos no pueden migrar a la vez
# --------------------------------------------------------------------------

exec 9>"$LOCKFILE"
flock -n 9 || die "Ya hay un despliegue en curso (lock $LOCKFILE). No se toca nada."

# --------------------------------------------------------------------------
# Estado previo, para el rollback
# --------------------------------------------------------------------------

previous_sha=$(gitapp rev-parse HEAD)
previous_sha=${2:-$previous_sha}
migrated=0
current_sha="$previous_sha"

# OJO: aca NO se hace `rev-parse --short "$target_sha"`. Ese comando solo resuelve
# objetos que ya estan en el repo local, y el `fetch` de mas abajo todavia no
# corrio: desplegar un commit recien pusheado fallaria con un "unknown revision"
# antes de haber tocado nada, con un error que no dice nada del deploy. Se loguea
# el SHA crudo y el corto se resuelve despues del checkout.
log "Deploy $target_sha sobre $(gitapp rev-parse --short "$previous_sha")"

# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------

rollback() {
    local status=$?
    # Se desarma EXIT y ERR: el `exit` del final de esta funcion no tiene que
    # volver a entrar aca.
    trap - ERR EXIT

    if [ "$status" -eq 0 ] || [ "$current_sha" = "$previous_sha" ]; then
        exit "$status"
    fi

    warn "El despliegue fallo (status $status). Se revierte el codigo a $(gitapp rev-parse --short "$previous_sha")."
    warn "NO se tocan la base de datos ni los archivos de media/."

    if [ "$migrated" -eq 1 ]; then
        warn "Las migraciones YA se aplicaron. Django no genera rollback automatico:"
        warn "si una migracion fue destructiva, la base quedo con el esquema nuevo."
        warn "Revisala a mano antes de volver a exponer el sitio."
    fi

    gitapp fetch --quiet origin || true
    gitapp checkout --force --quiet "$previous_sha" || {
        warn "No se pudo volver al commit anterior. El servidor quedo en $current_sha."
        warn "Corrigilo a mano: sudo -u $DEPLOY_USER git -C $APP_DIR checkout $previous_sha && systemctl restart $GUNICORN_UNIT"
        exit 1
    }

    # `env PIP_CACHE_DIR=...` no es opcional ni cosmetico. `sudo` aplica env_reset y
    # descarta las variables del entorno del llamador, asi que un `export
    # PIP_CACHE_DIR=...` de este script NO llega al pip que corre como DEPLOY_USER:
    # el cache sigue sin existir y cada deploy vuelve a descargar todo desde PyPI.
    # Pasandola por `env` queda en el entorno del comando, que es el que si hereda.
    # Con un prefix simple (`sudo -u user VAR=val cmd`) sudo tambien la respeta, pero
    # `env` es explicito y no depende de la configuracion de sudoers del servidor.
    sudo -u "$DEPLOY_USER" env "PIP_CACHE_DIR=$PIP_CACHE_DIR" \
        "${PIP_CMD[@]}" install --quiet -r "$APP_DIR/requirements/prod.txt" || warn "Falló el pip install del rollback; se reinicia igual."

    manage collectstatic --no-input || warn "Falló collectstatic en el rollback."
    systemctl restart "$GUNICORN_UNIT" || warn "Falló el restart de $GUNICORN_UNIT."
    systemctl restart "$HUEY_UNIT" || warn "Falló el restart de $HUEY_UNIT."

    warn "Codigo revertido a $(gitapp rev-parse --short "$previous_sha"). El sitio vuelve al estado anterior."
    exit "$status"
}

# Corre `manage.py` en el MISMO contexto que corre Gunicorn.
#
# systemd-run --pipe --wait crea una unidad transitoria con el EnvironmentFile de
# la clave de descifrado y PRODUCTION=1, exactamente igual que webcmp.service. Sin
# esto, un `manage.py` corrido a mano sin PRODUCTION cae en el perfil de DESARROLLO
# (ver config/settings/__init__.py): migra con las credenciales del entorno en vez
# de con las de produccion, y el deploy "pasa" contra la base equivocada.
#
# --uid webcmp: los archivos los escribe el usuario de servicio, que es el dueno
# del venv, de media/ y de staticfiles/. Como root quedarian con dueno root y el
# servicio dejaria de poder escribir en ellos.
manage() {
    # Por que systemd-run y no un shell normal: `manage.py` necesita leer la
    # ENCRYPTION_KEY de /etc/webcmp/encryption.env (600, root) y correr como
    # DEPLOY_USER, que no puede leerla. systemd resuelve las dos cosas: lee el
    # EnvironmentFile como root antes de hacer el drop de privilegios.
    #
    # Un `sudo -u webcmp manage.py ...` a secas no descifra nada y cae al perfil
    # de desarrollo (SQLite, DEBUG, EMAIL_BACKEND de consola), que es el modo de
    # fallo mas caro: el deploy "pasa" y migra la base equivocada.
    #
    # OJO: systemd-run NO hereda el entorno de quien llama. Un
    # `DJANGO_SUPERUSER_PASSWORD=... systemd-run ... createsuperuser --noinput`
    # crea la cuenta con una contrasena inservible y sin avisar, porque la
    # variable se queda en la shell del operador. Para pasar algo, va con
    # -p Environment=NOMBRE=valor.
    systemd-run --quiet --pipe --wait \
        --uid="$DEPLOY_USER" --gid="$DEPLOY_USER" \
        -p "EnvironmentFile=$ENCRYPTION_ENV" \
        -p Environment=PRODUCTION=1 \
        -p "Environment=MPLCONFIGDIR=$MPLCONFIGDIR" \
        --working-directory="$APP_DIR" \
        "$PYTHON" manage.py "$@"
}

# La trampa es EXIT y no ERR. No es indistinto: `trap ... ERR` NO se dispara
# cuando el script hace `exit`, y `die()` -que es como termina practicamente todo
# el deploy, incluido el health check- justamente hace `exit 1`. Con ERR, un deploy
# que migraba y despues fallaba en el health check se dejaba el servidor en el
# commit roto: el rollback era inalcanzable justo en el caso para el que existe.
# Verificado en el servidor de prueba: fallo forzado del health check, el codigo
# se quedo en el commit nuevo y el aviso de revertido nunca apareció.
#
# EXIT cubre las dos salidas: un comando que falla (set -e mata el script y el
# status es el del comando) y un `exit` explicito de `die`. El caso status==0
# esta contemplado mas abajo y no hace nada.
trap rollback EXIT

# --------------------------------------------------------------------------
# 1. Codigo
# --------------------------------------------------------------------------

if [ -n "$(gitapp status --porcelain --untracked-files=no)" ]; then
    die "El checkout tiene cambios versionados sin commitear. Se aborta para no perderlos:
  sudo -u $DEPLOY_USER git -C $APP_DIR status
  sudo -u $DEPLOY_USER git -C $APP_DIR diff

El \`sudo -u\` no es decorativo: el checkout es de $DEPLOY_USER y git se niega a
operar sobre un repo de otro usuario."
fi

gitapp fetch --quiet origin
gitapp checkout --force --quiet "$target_sha"
current_sha=$(gitapp rev-parse HEAD)

# 2. Dependencias
log "Instalando requirements/prod.txt"
sudo -u "$DEPLOY_USER" env "PIP_CACHE_DIR=$PIP_CACHE_DIR" \
    "${PIP_CMD[@]}" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements/prod.txt"

# 3. Generar migraciones desde los modelos
#
# OJO con el flujo de este proyecto: NO versiona las migraciones. El .gitignore
# tiene `**/migrations/*` con la unica excepcion de `__init__.py`, asi que un
# clone limpio llega SIN migraciones y el esquema se deriva de los modelos.
#
# Por eso el gate que se suele poner aca, `makemigrations --check --dry-run`
# ("el modelo cambio y nadie escribio la migracion"), NO sirve: en un clone
# limpio falla siempre, porque las migraciones todavia no existen y `--check`
# interpreta "no hay migraciones para el modelo" como "falta la migracion".
# El CI no lo sufre porque su job corre `makemigrations` antes de `migrate`.
#
# Consecuencia que hay que tener presente: una migracion se genera y se aplica en
# el mismo deploy, sin revision. Si un cambio de modelo llega con un ALTER
# destructivo, se aplica contra produccion sin que nadie lo mire. Versionar las
# migraciones es la unica forma de que eso sea revisable; mientras no se haga, el
# deploy es el unico lugar donde el schema se decide.
log "Generando migraciones desde los modelos"
manage makemigrations

# 4. Gate: la configuracion REAL de produccion
#
# El CI corre este mismo check con valores de mentira (db que no existe, sin
# SMTP). Aca corre contra el .env de verdad: es la primera vez que se verifica
# que la SECRET_KEY se descifra, que DB_* apunta al PostgreSQL real y que
# EMAIL_BACKEND no es el de consola. Tarda segundos y evita un sitio caido.
#
# `security.W008` (SECURE_SSL_REDIRECT) NO aparece: production.py lo silencia a
# proposito, porque el TLS y el redirect los termina el proxy. Ese silencio esta
# en SILENCED_SYSTEM_CHECKS, no en el --fail-level de esta linea.
log "Gate: check --deploy (configuracion real)"
manage check --deploy --fail-level WARNING

# 5. Migraciones: antes de que el codigo nuevo atienda un solo request
log "Aplicando migraciones"
manage migrate --noinput
migrated=1

# `--check` sale con codigo 1 si queda algo sin aplicar. Es la unica forma de
# confirmar que el esquema quedo donde tiene que estar y no "migrate no dijo
# nada", que es lo mismo que no haber corrido nada.
manage migrate --check

# 6. Estaticos: antes de que el codigo nuevo emita HTML que los referencia
log "Recolectando estaticos"
manage collectstatic --no-input

# 7. Reinicio
#
# `systemctl reload` (SIGHUP) NO alcanza para un despliegue: Gunicorn hace que el
# master recargue su configuracion y los workers se re-forkean desde el master.
# El master tiene el codigo viejo en memoria, asi que los workers nuevos tambien.
# El sitio serviria el template viejo con los estaticos nuevos.
log "Reiniciando $GUNICORN_UNIT y $HUEY_UNIT"
systemctl restart "$GUNICORN_UNIT"
systemctl restart "$HUEY_UNIT"

systemctl is-active --quiet "$GUNICORN_UNIT" || die "$GUNICORN_UNIT quedo inactivo."
systemctl is-active --quiet "$HUEY_UNIT" || die "$HUEY_UNIT quedo inactivo."

# --------------------------------------------------------------------------
# 8. Health check
# --------------------------------------------------------------------------
#
# Por defecto (HEALTHCHECK_URL vacio) sale al puerto 443 de 127.0.0.1 con el SNI
# y el Host correctos: atraviesa Nginx, TLS y el socket de Gunicorn, o sea el
# camino completo, sin salir del servidor y sin depender de que DNS resuelva
# desde aca. Es el comportamiento de siempre y no se toca.
#
# Con proxy EXTERNO (Nginx Proxy Manager) ese recorrido no existe: el 443 de
# esta maquina no lo escucha nadie. Por eso el destino es parametrizable:
#
#   HEALTHCHECK_URL (que se consulta) + HEALTHCHECK_RESOLVE (--resolve) +
#   HEALTHCHECK_UNIX_SOCKET (--unix-socket) + HEALTHCHECK_HOST_HEADER (Host:)
#
# Las cuatro estan validadas en el arranque (validate_healthcheck_target): un
# valor mal formado tiene que morir con un mensaje que diga cual es, no con un
# `000` veinte segundos mas tarde.
log "Health check"

hc_url=${HEALTHCHECK_URL:-https://$PUBLIC_HOSTNAME/}
hc_args=(--silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 15)

if [ -n "$HEALTHCHECK_UNIX_SOCKET" ]; then
    hc_args+=(--unix-socket "$HEALTHCHECK_UNIX_SOCKET")
elif [ -z "$HEALTHCHECK_URL" ]; then
    # Sin URL explicita: el comportamiento de siempre, 443 en 127.0.0.1 con SNI.
    hc_args+=(--resolve "$PUBLIC_HOSTNAME:443:127.0.0.1")
elif [ "$HEALTHCHECK_RESOLVE" = none ]; then
    : # el operador escribio la palabra `none`: salir por DNS de verdad
elif [ -n "$HEALTHCHECK_RESOLVE" ]; then
    hc_args+=(--resolve "$HEALTHCHECK_RESOLVE")
elif [ -z "$HEALTHCHECK_RESOLVE_DEFINED" ]; then
    # La clave no estaba en deploy.env y hay una URL: el default razonable es el
    # mismo --resolve de siempre, para no depender del DNS del servidor.
    hc_args+=(--resolve "$PUBLIC_HOSTNAME:443:127.0.0.1")
fi
# Con socket unix NO se pone --resolve ni aunque este definido: cuando el
# transporte es el socket, curl no resuelve ningun nombre y el flag queda como
# ruido. Un `--resolve` colado ahi en un deploy con proxy externo es la clase de
# cosa que hace que un health check falle por una opcion que no aplica.
[ -n "$HEALTHCHECK_HOST_HEADER" ] && hc_args+=(--header "Host: $HEALTHCHECK_HOST_HEADER")
# `--insecure` solo si el operador lo pidio explicitamente en deploy.env.
[ -n "$HEALTHCHECK_INSECURE" ] && hc_args+=(--insecure)

printf '  destino: %s' "$hc_url"
[ -n "$HEALTHCHECK_UNIX_SOCKET" ] && printf ' (socket %s)' "$HEALTHCHECK_UNIX_SOCKET"
printf '\n'

# `|| true`, no `|| echo 000`. `--write-out '%{http_code}'` YA imprime `000` cuando
# curl no logra conectarse, asi que un `|| echo 000` anade un segundo 000 y la
# variable queda con `000000`. Ese valor no matchea el patron `000` del case de
# abajo: un deploy con el sitio caido caia en el `*)` y decia "El sitio responde
# 000000, se esperaba 2xx o 3xx", que no dice nada. El `000)` con el mensaje del
# certificado autofirmado, que es el diagnostico util, era inalcanzable.
code=$(curl "${hc_args[@]}" "$hc_url" || true)

case "$code" in
    2* | 3*) log "El sitio responde $code." ;;
    000)
        if [ -n "${HEALTHCHECK_INSECURE:-}" ]; then
            die "Sin respuesta aun con --insecure (ver journalctl -u $GUNICORN_UNIT)."
        fi
        if [ -n "$HEALTHCHECK_UNIX_SOCKET" ]; then
            die "Sin respuesta de $hc_url por el socket $HEALTHCHECK_UNIX_SOCKET.
Con PROXY_MODE=external el health check va al socket de Gunicorn: si contesta 000,
el problema NO es el proxy externo. Mirá journalctl -u $GUNICORN_UNIT y que exista
$HEALTHCHECK_UNIX_SOCKET con el servicio arriba."
        fi
        die "Sin respuesta de $hc_url.
Si el servidor tiene un certificado autofirmado, poné
HEALTHCHECK_INSECURE=1 en $CONFIG para este servidor de prueba.
NUNCA en produccion: --insecure deja de detectar un certificado vencido o de otro
dominio, que es exactamente lo que el health check deberia estar mirando.
Si el proxy es externo, corré $0 --configure: el destino por defecto (443 en
127.0.0.1) no existe en esa topologia y el health check no puede pasar."
        ;;
    *) die "El sitio responde $code, se esperaba 2xx o 3xx." ;;
esac

# Desarma la trampa antes de imprimir el resumen: si fallara un `git rev-parse`
# aca, el rollback intentaria volver al commit anterior... que es este mismo.
trap - ERR EXIT

# Estado para el panel: el commit que quedo sirviendo. Se escribe al final y de
# forma atomica porque el panel lo lee en cada visita al dashboard; un archivo a
# medias haria que "hay actualizacion" parpadee sin motivo.
install -d -m 0755 "$(dirname "$DEPLOYED_STATE")"
gitapp rev-parse HEAD > "$DEPLOYED_STATE.tmp"
chmod 0644 "$DEPLOYED_STATE.tmp"
mv -f "$DEPLOYED_STATE.tmp" "$DEPLOYED_STATE"

log "Desplegado $(gitapp rev-parse --short HEAD)."
printf '  Web:  %s\n' "$hc_url"
printf '  Logs: journalctl -u %s -f\n' "$GUNICORN_UNIT"
printf '  Huey: journalctl -u %s -f\n' "$HUEY_UNIT"
printf '  Volver atras: sudo %s %s\n' "$0" "$previous_sha"
