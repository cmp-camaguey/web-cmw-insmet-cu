#!/usr/bin/env bash
# Instalador del portal del CMP Camaguey. Auto-contenido: no lee NADA del
# checkout antes de clonarlo, asi que se puede ejecutar como archivo suelto.
#
#   curl -fsSL https://raw.githubusercontent.com/cmp-camaguey/web-cmw-insmet-cu/main/deploy/install.sh \
#       | sudo bash -s -- --help
#
# Que instala, en este orden (el orden es una dependencia, no una preferencia):
#   1. prerrequisitos de sistema (apt) y version de Python
#   2. usuario de servicio webcmp + media/, logs/, staticfiles/, .cache/
#   3. clone del repo en APP_DIR, propiedad de webcmp
#   4. rol y base de datos, con las credenciales que se preguntaron
#   5. venv + requirements/prod.txt
#   6. .env con scripts/generate_env.py y los valores preguntados, sin CHANGE_ME
#   7. makemigrations, migrate, collectstatic, check --deploy
#   8. unidades systemd renderizadas a APP_DIR/SERVICE_USER reales
#   9. proxy: (A) Nginx local con TLS, o (B) Nginx local SIN TLS detras de un
#      reverse proxy externo (Nginx Proxy Manager) que termina el TLS
#  10. /etc/webcmp/deploy.env con el destino del health check que corresponde
#  11. andamiaje de deploy automatico (usuario, keypair, sudoers, webcmp-deploy)
#  12. superusuario, por systemd-run con la ENCRYPTION_KEY
#
# AUTO-CONTENIDO, y por que importa: este archivo se ejecuta por un pipe, sin
# checkout delante. Todo lo que necesita saber esta escrito aca o se pregunta.
# La version anterior leia deploy/deploy.sh, deploy/deploy.env.example y
# deploy/sudoers/webcmp-deploy de un repo que todavia no existia en el servidor,
# y por eso no podia correr de esta forma.
#
# IDEMPOTENCIA. Correrlo dos veces no cambia nada y no rompe lo ya armado:
#   - NO regenera SECRET_KEY ni ENCRYPTION_KEY: se conservan si el par descifra
#     (es lo que ya hace scripts/generate_env.py, y por que importa: regenerar
#     la SECRET_KEY invalida todas las sesiones y cookies firmadas del sitio)
#   - NO tira la base: el rol se altera, la base se crea solo si no existe
#   - NO pisa un .env ni un /etc/webcmp/deploy.env sin confirmacion
#   - cada pregunta muestra como default el valor que ya esta en vigor
# Un paso que regenera un secreto o pisa una config editada a mano convierte un
# reintento en un incidente.
#
# FALLA CERRADO. Sin terminal y sin --non-interactive, aborta ANTES de tocar
# nada: un instalador que adivina en un pipe escribe el .env equivocado y nadie
# lo ve hasta que el sitio responde con la base equivocada. Con
# --non-interactive todas las respuestas salen de variables de entorno o de los
# defaults, y el resumen final dice de donde salio cada una.
#
# --dry-run imprime el plan completo y no ejecuta nada. Los pasos nuevos lo
# respetan: las preguntas no se hacen y su respuesta se imprime como
# "[dry-run] CLAVE=valor", con los secretos enmascarados.

set -Eeuo pipefail

# --------------------------------------------------------------------------
# Defaults. Todos overridables por variable de entorno, para no tener que
# editar el script en un servidor con otro layout.
# --------------------------------------------------------------------------
#
# El repo se clona con `git clone` (no con curl|tar) porque el deploy depende de
# un checkout real: deploy.sh hace `git fetch`/`checkout` en produccion y
# aborta si el directorio no es un repo.
REPO_URL=${REPO_URL:-https://github.com/cmp-camaguey/web-cmw-insmet-cu.git}
REPO_REF=${REPO_REF:-main}
APP_DIR=${APP_DIR:-/srv/webcmp}
SERVICE_USER=${SERVICE_USER:-webcmp}
SERVICE_GROUP=${SERVICE_GROUP:-$SERVICE_USER}
SERVICE_SHELL=${SERVICE_SHELL:-/usr/sbin/nologin}
DEPLOY_LOGIN_USER=${DEPLOY_LOGIN_USER:-deploy}
# Home del usuario de despliegue. `useradd --create-home` lo pone en /home/<user>
# y el authorized_keys tiene que ir ahi: sshd lo busca en el home real del
# usuario, no en una ruta que el instalador elija a gusto.
DEPLOY_HOME=${DEPLOY_HOME:-/home/$DEPLOY_LOGIN_USER}
SCRIPT_PATH=${SCRIPT_PATH:-/usr/local/sbin/webcmp-deploy}
# Ejecutor del boton "Actualizar" del panel (change 025-update-web-deploy).
AUTODEPLOY_SCRIPT=${AUTODEPLOY_SCRIPT:-/usr/local/sbin/webcmp-autodeploy}
AUTODEPLOY_STATE_DIR=${AUTODEPLOY_STATE_DIR:-/var/lib/webcmp}
CONFIG_DIR=${CONFIG_DIR:-/etc/webcmp}
CONFIG_FILE=${CONFIG_FILE:-$CONFIG_DIR/deploy.env}
ENCRYPTION_ENV=${ENCRYPTION_ENV:-$CONFIG_DIR/encryption.env}
PIP_CACHE_DIR=${PIP_CACHE_DIR:-/var/cache/webcmp-pip}
MPLCONFIGDIR=${MPLCONFIGDIR:-$APP_DIR/.cache/matplotlib}
VENV_DIR=${VENV_DIR:-$APP_DIR/.venv}
DEPLOY_SSH_KEY=${DEPLOY_SSH_KEY:-$CONFIG_DIR/deploy_key}
DEPLOY_SUDOERS=${DEPLOY_SUDOERS:-/etc/sudoers.d/webcmp-deploy}
NGINX_AVAILABLE=${NGINX_AVAILABLE:-/etc/nginx/sites-available/webcmp.conf}
NGINX_ENABLED=${NGINX_ENABLED:-/etc/nginx/sites-enabled/webcmp.conf}
NGINX_DEFAULT_SITE=${NGINX_DEFAULT_SITE:-/etc/nginx/sites-enabled/default}
NGINX_ERROR_ROOT=${NGINX_ERROR_ROOT:-/var/www/webcmp-errors}
ACME_ROOT=${ACME_ROOT:-/var/www/letsencrypt}
TLS_DIR=${TLS_DIR:-$CONFIG_DIR/tls}
# El vhost NUNCA se copia tal cual: se renderiza desde el ejemplo del checkout
# (ver render_vhost). Hay DOS ejemplos y cual se usa depende de PROXY_MODE, que
# se pregunta mas abajo; por eso esta asignacion es solo el default y se vuelve
# a fijar despues de la pregunta:
#   nginx-local -> webcmp.conf.example       (Nginx hace TLS)
#   external    -> webcmp-http.conf.example  (el TLS lo termina el proxy)
vhost_src=$APP_DIR/deploy/nginx/webcmp.conf.example
# El socket de Gunicorn sale de RuntimeDirectory=webcmp en el unit, o sea
# /run/<GUNICORN_UNIT>/gunicorn.sock. Los tres tienen que decir lo mismo: el
# vhost de Nginx, el health check de deploy.sh y el unit.
GUNICORN_UNIT=${GUNICORN_UNIT:-webcmp}
HUEY_UNIT=${HUEY_UNIT:-webcmp-huey}
# El .path se habilita y vigila; el .service lo arranca systemd, nunca a mano.
AUTODEPLOY_UNIT=${AUTODEPLOY_UNIT:-webcmp-autodeploy}
GUNICORN_SOCKET=${GUNICORN_SOCKET:-/run/$GUNICORN_UNIT/gunicorn.sock}

# scripts/generate_env.py usa PEP 758 (except sin parentesis, `except A, B:`),
# que existe SOLO desde Python 3.14. Con 3.12 o 3.13 el generador no compila y
# falla con un SyntaxError que no menciona la version: el error dice
# "multiple exception types must be parenthesized" y no dice "instala Python
# 3.14". Se chequea aca, en el arranque, con el mensaje que si lo dice.
MIN_PYTHON=(3 14)

DRY_RUN=0
INTERACTIVE=1
HELP=0
ALLOW_INCOMPLETE_ENV=0
SKIP_DEPLOY_SCAFFOLD=0
HAVE_CHECKOUT=0
CHANGED=0
ANSWER_SOURCES=()

# Canal lateral de `ask_secret`: un archivo temporal con un nombre de clave por
# linea para cada secreto que GENERO el instalador, para que el padre sepa si
# tiene que mostrarlo. Se borra al salir, incluso con Ctrl-C o `die`, para no
# dejar un archivo con nombres de claves tirado en /tmp.
ASK_GENERATED_FILE=$(mktemp -t webcmp-generated.XXXXXX)
cleanup_generated_flags() { [ -n "${ASK_GENERATED_FILE:-}" ] && rm -f "$ASK_GENERATED_FILE"; }

# El trap tambien para el spinner. Sin esto, Ctrl-C durante el pip install deja
# el subshell del spinner vivo: el operador vuelve a su prompt y el spinner le
# sigue escribiendo "\\r| pip install [02m[01m04s]" encima de lo que esta
# escribiendo, hasta que el subshell muera solo por mucho sleep 0.1. Se ve como
# la terminal corrupta y no es recuperable sin abrir otra pestana.
#
# `command -v` porque `spinner_stop` se define mas abajo (linea ~250) y este trap
# se registra antes: si el script muriera entremedio, un `spinner_stop` pelado
# daria "command not found" y taparia el error real con otro error.
trap 'cleanup_generated_flags; command -v spinner_stop >/dev/null && spinner_stop; exit 130' INT
trap 'cleanup_generated_flags; command -v spinner_stop >/dev/null && spinner_stop' EXIT TERM

# Contadores del resumen final. Todos arrancan en 0 y SOLO pasan a 1 en el punto
# exacto donde la cosa ocurrio, asi que el resumen final no puede afirmar nada que
# no haya pasado. Ver C.13.
STEPS_TOTAL=0
STEPS_DONE=0
DEPLOY_SSH_KEY_CREATED=0
SUPERUSER_CREATED=0
GUNICORN_ENABLED=0
INSTALL_STARTED_AT=$(date +%s)

# --------------------------------------------------------------------------
# Barra de progreso
# --------------------------------------------------------------------------
#
# La barra va a stderr y solo si stderr es un terminal. En un log de CI o en un
# `| tee` un `\r` y 30 caracteres ANSI por seccion son basura que el que lee el
# log tiene que filtrar a mano, y el CI no es un terminal.
PROGRESS_TOTAL=0
PROGRESS_DONE=0
PROGRESS_CURRENT=
progress_on() { [ -t 2 ] && [ "${NO_PROGRESS:-0}" != 1 ]; }

# Ponderacion por peso de trabajo, no por numero de secciones. "Unidades systemd"
# son 400 lineas y son instantaneas; "Instalando requirements/prod.txt" son 10 y
# son minutos. Con el conteo de secciones (1/14) el operador ve la barra clavada
# en 10% durante el pip install y despues tres saltos rapidos al final: ve mal
# justo cuando tiene mas ganas de mirar. Con peso, la barra avanza poco a poco
# donde el servidor esta trabajando de verdad.
#
# Los pesos son unidades relativas de trabajo esperado, no segundos.
progress_reset() {
    PROGRESS_DONE=0
    PROGRESS_TOTAL=0
    PROGRESS_CURRENT=
    for _weight in "$@"; do PROGRESS_TOTAL=$((PROGRESS_TOTAL + _weight)); done
    progress_draw
}

progress_draw() {
    progress_on || return 0
    [ "$PROGRESS_TOTAL" -gt 0 ] || return 0
    local pct=$((PROGRESS_DONE * 100 / PROGRESS_TOTAL))
    local filled=$((PROGRESS_DONE * 30 / PROGRESS_TOTAL))
    local bar=''
    local i
    for ((i = 0; i < 30; i++)); do
        if [ "$i" -lt "$filled" ]; then bar+='#'; else bar+='-'; fi
    done
    printf '\r\033[K  [%s] %3d%%  %s' "$bar" "$pct" "$PROGRESS_CURRENT" >&2
}

# Borra la linea de la barra. Todo lo que imprima mientras la barra este puesta
# tiene que llamarla antes: si no, el texto sale pegado al final de la barra y la
# lectura es un caos.
progress_clear() {
    progress_on || return 0
    printf '\r\033[K' >&2
}

progress_done() {
    PROGRESS_DONE=$((PROGRESS_DONE + $1))
    progress_draw
}

# Cierra el bloque de la barra para que lo que venga abajo (un aviso, una
# pregunta, el resumen) no quede encima.
# Deja la barra en su propia linea para que lo que venga abajo salga debajo y no
# al lado. En el log del pty se ve el problema: el texto de una seccion se
# imprimia pegado al final del "[####] 71%" y las dos cosas quedaban ilegibles.
progress_break() {
    progress_clear
    PROGRESS_CURRENT=
}

# --------------------------------------------------------------------------
# Salida en vivo: spinner, reloj y log con timestamp
# --------------------------------------------------------------------------
#
# La barra de secciones (arriba) dice EN QUE PARTE va la instalacion. Esto dice
# QUE ESTA PASANDO ahora mismo: un comando que tarda tres minutos sin decir una
# palabra parece colgado, y la primera pregunta del operador va a ser si hay que
# esperar o si se murió.
#
# El problema tecnico de fondo es que el spinner escribe a stderr desde un
# proceso en background mientras el comando watched escribe a stderr en el
# primer plano. Los dos borran la misma linea con `\r\033[K` y se pisan. Por eso
# NADA imprime directo: el spinner solo dibuja, y toda linea de log pasa por
# `log_line`, que borra el spinner, imprime, y lo vuelve a dibujar. Es el unico
# orden que no produce lineas a medio escribir.
SPINNER_PID=
SPINNER_MSG=
ELAPSED_START=

# Frames ASCII, no Unicode: el instalador corre por SSH a un servidor donde el
# locale puede ser POSIX y un braille UTF-8 sale como "??????". Un spinner que
# se ve roto en el servidor es peor que uno sin spinner.
SPINNER_FRAMES=('|' '-' '\' '/')

spinner_render() {
    local frame elapsed
    elapsed=$(( $(date +%s) - ELAPSED_START ))
    if [ -n "$SPINNER_MSG" ]; then
        frame=${SPINNER_FRAMES[$(( (elapsed * 10) % 4 ))]}
        # mm:ss, no "[%dm%02ds]": ese formato imprimia literalmente "[0m03s]",
        # que en un terminal se ve como un escape ANSI a medio escribir. Un reloj
        # que parece un error de formato hace desconfiar del installing entero.
        printf '\r\033[K  \033[36m%s\033[0m %s \033[2m(%02d:%02d)\033[0m' \
            "$frame" "$SPINNER_MSG" "$((elapsed / 60))" "$((elapsed % 60))" >&2
    fi
}

spinner_start() {
    SPINNER_MSG=$1
    ELAPSED_START=$(date +%s)
    progress_on || return 0
    spinner_render
    (
        while :; do
            sleep 0.1
            spinner_render
        done
    ) &
    SPINNER_PID=$!
}

# El subshell del spinner hereda SPINNER_MSG y ELAPSED_START por fork, asi que
# alcanza con asignarlos ANTES de lanzarlo. Por eso el orden importa: primero los
# dos, despues el `&`.
spinner_stop() {
    local pid=$SPINNER_PID
    [ -n "$pid" ] || return 0
    SPINNER_PID=
    # `|| true` en kill y wait, y NO es cosmetico: `wait` de un subshell
    # matado con SIGTERM devuelve 143 (128 + 15), y con `set -e` esa linea
    # mataba el instalador ACA, sin mensaje y sin llegar ni a `log_ok` ni a
    # `return`. Era exactamente la muerte silenciosa del paso de migraciones:
    # el unico `run_live` que no venia con `|| die` es el de `manage()`, asi
    # que era el primero que pasaba por aca con `set -e` activo. `die()` tam-
    # bien llama aca: con un spinner activo el ERROR ni siquiera se imprimia.
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    progress_clear
}

# log_line <texto>
#
# Toda linea de salida de un comando pasa por aca. Escribe arriba de la barra y
# la vuelve a dibujar debajo: el operador ve el log growing mientras el comando
# corre, que es la mitad del pedido.
log_line() {
    local text=$1
    [ -n "$text" ] || return 0
    if [ -n "$SPINNER_PID" ]; then progress_clear; fi
    printf '  \033[2m%s\033[0m %s\n' "$(date +%H:%M:%S)" "$text" >&2
    spinner_render
}

# El "ok" del comando terminado tiene su propia funcion y NO pasa por `log_line`
# con escapes adentro: un `\033[32m` embebido en el texto llega literal a
# cualquier filtro de log, a un `tee`, y a un pager que no sabe de ANSI. El
# color se aplica en el printf, no en los datos.
log_ok() {
    local label=$1
    printf '  \033[2m%s\033[0m \033[32mok\033[0m  %s\n' "$(date +%H:%M:%S)" "$label" >&2
}

# run_live <etiqueta> <comando> [args...]
#
# Corre un comando largo con spinner y log en vivo. A diferencia de `run`, el
# comando NO puede fallar en silencio: su codigo de salida sube intacto para que
# el `|| die` de quien lo llama siga funcionando igual.
run_live() {
    local label=$1
    shift
    if [ "$DRY_RUN" -eq 1 ]; then
        run "$label" "$@"
        return 0
    fi
    if ! progress_on; then
        # Sin terminal no hay spinner que dibujar. Se corre el comando con su
        # salida normal: en CI el log crudo es exactamente lo que se quiere.
        # El status se captura y se devuelve, igual que en `run`: con el
        # `CHANGED=1` seguido de `return $?` el exit code era SIEMPRE 0 (el
        # status de la asignacion), asi que el `|| die` de apt-get, pip y git
        # clone era codigo muerto en cualquier contexto sin TTY (CI, `| tee`,
        # systemd-run --pipe): el comando fallaba y el instalador seguia.
        local status=0
        "$@" || status=$?
        [ "$status" -eq 0 ] && CHANGED=1
        return "$status"
    fi
    spinner_start "$label"
    "$@" 2>&1 | while IFS= read -r line; do log_line "$line"; done
    local status=${PIPESTATUS[0]}
    # El spinner se para ANTES del "ok": si no, el `log_line` de abajo tendria que
    # pelear con el subshell que sigue redibujando, y la linea queda cortada.
    spinner_stop
    if [ "$status" -eq 0 ]; then
        log_ok "$label"
    fi
    CHANGED=1
    return "$status"
}

log()  { progress_break; printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { progress_break; printf '\033[1;33mAVISO:\033[0m %s\n' "$*" >&2; }
die()  { spinner_stop; progress_break; printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# log_paso <peso> <titulo>
#
# Seccion que avanza la barra. El peso es trabajo esperado (ver progress_reset):
# 1 = un instante, 10 = el pip install que se lleva la mayor parte del tiempo.
step() {
    local weight=$1 title=$2
    STEPS_DONE=$((STEPS_DONE + 1))
    log "$title"
    progress_done "$weight"
    progress_draw
    # Newline tras la barra: lo que imprime el cuerpo de la seccion (apt-get,
    # pip, migrate) sale DEBAJO y no pegado al "[####] 71%". Sin esto el
    # operador lee la linea del comando y el porcentaje en la misma, y el
    # progreso se pierde en un enredo que hay que leer dos veces.
    progress_on && printf '\n' >&2
    return 0
}

# Los pasos del plan, en orden, con su peso: "<peso> <nombre>". Se declara aqui
# y no se deriva con grep sobre el archivo porque los pesos son una decision
# (que parte se lleva el tiempo), no un dato.
PROGRESS_PLAN=(
    "2|Preflight"
    "1|Configuracion"
    "1|Resumen de lo que se va a instalar"
    "8|Prerrequisitos de sistema"
    "1|Usuario de servicio"
    "6|Checkout"
    "2|Base de datos: rol y base"
    "2|Entorno virtual"
    "25|requirements/prod.txt"
    "1|Archivo .env"
    "1|Valores del .env"
    "8|Migraciones y estaticos"
    "4|Unidades systemd"
    "1|Configuracion del deploy"
    "4|Arrancando los servicios"
)
progress_begin() {
    local entry total=0
    STEPS_TOTAL=${#PROGRESS_PLAN[@]}
    for entry in "${PROGRESS_PLAN[@]}"; do total=$((total + ${entry%%|*})); done
    PROGRESS_TOTAL=$total
    PROGRESS_DONE=0
    PROGRESS_CURRENT=
    progress_draw
}

# --------------------------------------------------------------------------
# Wrappers de ejecucion
# --------------------------------------------------------------------------

# Todo lo que muta el sistema pasa por aca. En --dry-run se imprime en vez de
# ejecutarse, que es lo unico que hace util el modo: un dry-run que solo muestra
# texto fijo no se puede comparar contra el estado real del servidor.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] %s\n' "$*"
    else
        # El status se captura y se DEVUELVE. Con dos lineas sueltas
        # (`"$@"` despues `CHANGED=1`) había dos modos de fallo distintos
        # segun el contexto: con `set -e` activo el script moria aca sin
        # decir nada, y en un contexto `|| ...` el status se TAPIABA
        # (CHANGED=1 devolvia 0), con lo que por ejemplo
        #   run systemctl is-active --quiet $HUEY_UNIT || warn "..."
        # nunca llegaba al warn: el aviso era codigo muerto.
        local st=0
        "$@" || st=$?
        [ "$st" -eq 0 ] && CHANGED=1
        return "$st"
    fi
}

# Para los comandos que son shell puro (redirecciones, &&), que `run` no cubre.
# Igual que `run`: el status se captura y se devuelve, o un `|| ...` del llamador
# queda tapado por el exit 0 del ultimo comando.
run_sh() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] %s\n' "$1"
    else
        local st=0
        eval "$1" || st=$?
        [ "$st" -eq 0 ] && CHANGED=1
        return "$st"
    fi
}

# install, pero solo si el destino no es ya identico.
#
# No es purismo. Tocar /etc/sudoers.d/webcmp-deploy obliga a sudo a releer el
# archivo en cada invocacion, y reescribir /usr/local/sbin/webcmp-deploy le
# cambia el mtime a un archivo que puede estar corriendo. Y sobre todo: un resumen
# que dice "cambio algo" cuando no cambio nada hace que la idempotencia sea una
# promesa que nadie puede verificar desde la salida.
install_if_changed() {
    local src=$1 dest=$2 mode=$3 owner=${4:-root} group=${5:-root}
    # `10#` quita los ceros de la izquierda. Sin esto la comparacion NUNCA puede
    # ser cierta: el script pide `0755` y `stat -c %a` devuelve `755`, asi que
    # "root:root 0755" != "root:root 755" y el script reescribe los archivos
    # identicos en cada corrida, que es justo lo que este helper evita.
    if [ -f "$dest" ] && cmp -s "$src" "$dest" \
        && [ "$(stat -c '%U:%G %a' "$dest")" = "$owner:$group $((10#$mode))" ]
    then
        printf '  %s ya esta al dia (%s:%s %s)\n' "$dest" "$owner" "$group" "$((10#$mode))"
        return 0
    fi
    run install -o "$owner" -g "$group" -m "$mode" "$src" "$dest"
}

# Como el de arriba, pero para directorios. Existe aparte y no reusa
# install_if_changed porque ese compara contenido, que un directorio no tiene.
ensure_dir() {
    local dir=$1 mode=$2 owner=$3 group=$4
    if [ -d "$dir" ] && [ "$(stat -c '%U:%G %a' "$dir")" = "$owner:$group $((10#$mode))" ]
    then
        printf '  %s ya esta al dia (%s:%s %s)\n' "$dir" "$owner" "$group" "$((10#$mode))"
        return 0
    fi
    run install -d -o "$owner" -g "$group" -m "$mode" "$dir"
}

usage() {
    cat <<'EOF'
Instalador de web-cmw-insmet-cu. Un solo archivo, sin checkout previo:

  curl -fsSL https://raw.githubusercontent.com/cmp-camaguey/web-cmw-insmet-cu/main/deploy/install.sh \
      | sudo bash -s -- --help

Uso:
  sudo bash install.sh [opciones]
  curl -fsSL <raw-url>/deploy/install.sh | sudo bash -s -- [opciones]

Opciones:
  --non-interactive   No preguntar nada: cada valor sale de la variable de
                      entorno del mismo nombre o del default. Sin terminal esto
                      es obligatorio.
  --allow-incomplete-env
                      Dejar los CHANGE_ME que sobren en el .env (por ejemplo si
                      el SMTP todavia no existe). NO migra, NO crea el
                      superusuario y NO arranca los servicios: avisa de que el
                      sitio queda sin levantar.
  --skip-deploy-scaffold
                      No instalar el usuario de despliegue, su keypair ni el
                      sudoers: para un servidor donde el deploy va a correr a mano.
  --dry-run, -n      Imprimir el plan y no ejecutar nada.
  --help, -h         Esto. Sale 0 sin exigir root ni TTY y sin tocar nada.

Variables de entorno que se respetan (las preguntas usan el mismo nombre):
  REPO_URL, REPO_REF, APP_DIR, SERVICE_USER, DEPLOY_LOGIN_USER, SCRIPT_PATH,
  CONFIG_DIR, CONFIG_FILE, ENCRYPTION_ENV, DB_NAME, DB_USER,
  DB_PASS, DB_HOST, DB_PORT, DB_SSL_MODE, EMAIL_HOST, EMAIL_PORT,
  EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, DEFAULT_FROM_EMAIL, PUBLIC_HOSTNAME,
  REDIS_URL, USE_REDIS_CACHE, LOG_LEVEL, SUPERUSER_USERNAME, SUPERUSER_EMAIL,
  SUPERUSER_PASSWORD.

Que NO hace: no administra el deploy. Para desplegar un commit:
  sudo $SCRIPT_PATH <commit-sha>
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n)           DRY_RUN=1 ;;
        --non-interactive)      INTERACTIVE=0 ;;
        --allow-incomplete-env) ALLOW_INCOMPLETE_ENV=1 ;;
        --skip-deploy-scaffold) SKIP_DEPLOY_SCAFFOLD=1 ;;
        -h|--help)              HELP=1 ;;
        *) die "Opcion desconocida: $1 (--help para el uso)." ;;
    esac
    shift
done

# La ayuda sale 0 sin exigir root ni TTY y ANTES del preflight: es el contrato de
# `curl ... | sudo bash -s -- --help` que promete el README, y el unico comando
# que un operador puede correr sin consecuencias sobre un servidor real.
if [ "$HELP" -eq 1 ]; then
    usage
    exit 0
fi

# --------------------------------------------------------------------------
# Preguntas
# --------------------------------------------------------------------------
#
# Reglas, iguales a las de deploy.sh:
#   - vacio = conservar el default, y el default es SIEMPRE el valor vigente
#   - la validacion es una funcion aparte, para que el error sea del campo
#   - reintentos acotados y despues die: un bucle de repregunta infinito en un
#     instalador colgado deja al operador sin salida y sin saber por que
#
# `ask` y `ask_secret` devuelven el valor por stdout y escriben TODO lo demas en
# stderr. No es una mania de stdout: el llamador hace `X=$(ask ...)`, asi que
# cualquier texto por stdout que no sea la respuesta terminaria DENTRO del valor.
MAX_PROMPT_TRIES=3

# Un nombre de dominio o un ALLOWED_HOSTS con espacios, dos puntos, barras o
# comillas rompe el vhost de Nginx, el `source` de deploy.env y el
# `ALLOWED_HOSTS` de Django. Se acota aca, una vez.
valid_hostname() {
    [ -n "$1" ] || return 1
    case "$1" in
        *[!a-zA-Z0-9.-]*) return 1 ;;
        -* | .* | *..*) return 1 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$'
}

valid_email() {
    [ -n "$1" ] || return 1
    # La clase DEBE incluir `@`. Sin ella el `case` rechaza toda direccion antes
    # de que el regex la mire, y ningun correo valido pasa: los dos casos que
    # usan este validador (ACME_EMAIL, SUPERUSER_EMAIL) quedan inalcanzables.
    #
    # El guion va AL FINAL a proposito. En `+-@` bash lee un RANGE (0x2B..0x40)
    # y acepta de mas `:` `/` `<` `=` `?`, caracteres que no pertenecen a un
    # correo. Con `@+-` cada simbolo es literal.
    case "$1" in
        *[!a-zA-Z0-9._%@+-]*) return 1 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[^@ ]+@[^@ ]+\.[^@ ]+$'
}

valid_port() {
    printf '%s' "$1" | grep -Eq '^[0-9]{1,5}$' && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

valid_db_name() {
    [ -n "$1" ] || return 1
    case "$1" in
        -*) return 1 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[a-zA-Z_][a-zA-Z0-9_$-]*$'
}

# Servidor SMTP. Acepta un hostname (`smtp.caonao.cu`, `mail.corp`) y tambien
# un `host:puerto` IPv4 literal, porque algunos relays internos se dan asi.
# NO acepta una direccion de correo: el `@` pertenece al usuario, no al host, y
# ademas romperia el `source` de deploy.env. Por eso el error dice que se puso un
# correo donde va el servidor.
valid_smtp_host() {
    valid_host "$1" || return 1
    case "$1" in
        *@*) return 1 ;;
    esac
    return 0
}

valid_db_user() {
    valid_db_name "$1"
}

valid_host() {
    [ -n "$1" ] || return 1
    case "$1" in
        *[!a-zA-Z0-9.:_-]*) return 1 ;;
    esac
    return 0
}

# db: nombre de usuario de la app. No el de root ni el de postgres: es el rol
# que Django usa para conectarse.
valid_app_user() {
    [ -n "$1" ] || return 1
    case "$1" in
        *[!a-zA-Z0-9._-]*) return 1 ;;
        -*) return 1 ;;
    esac
    return 0
}

# Username de SMTP. Acepta las dos formas reales: un login corto (`admin`, que
# usan los relays internos) y la direccion completa (`meteocamaguey@caonao.cu`,
# que es lo que casi todo proveedor publico exige como AUTH LOGIN). Usar
# valid_app_user aqui rechazaba la direccion entera, que es el caso mayoritario.
valid_smtp_user() {
    [ -n "$1" ] || return 1
    if valid_email "$1"; then
        return 0
    fi
    valid_app_user "$1"
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

valid_choice() {
    # valid_choice <permitidos separados por espacio> <valor>
    case " $1 " in
        *" $2 "*) return 0 ;;
    esac
    return 1
}

# No hay validador de motor de base de datos, y es a proposito: el motor ya no
# se pregunta, es constante (postgresql). `get_database_config()` de
# config/settings/base.py exige DB_ENGINE en los tres perfiles y rechaza sqlite
# de forma explicita, asi que el instalador de PRODUCCION solo maneja PostgreSQL
# y un `.env` de desarrollo ya no puede colarse como respaldo silencioso.

# El generador escribe DEBUG|INFO|WARNING|ERROR|CRITICAL y Django no valida este
# valor: uno mal escrito no da error, solo desactiva el logging en un nivel y deja
# el deploy sin dejar rastro. Un enum explicito lo corta aca.
valid_log_level() {
    valid_choice 'DEBUG INFO WARNING ERROR CRITICAL' "$1"
}

# Enmascara un valor si la clave parece un secreto. El resumen final reimprime
# todas las respuestas, asi que una contrasena que llegue ahi queda en el
# scrollback del terminal y en el log de la sesion.
mask() {
    case "$1" in
        *PASS* | *SECRET* | *ENCRYPTION_KEY*) printf '<oculto>' ;;
        *) printf '%s' "$2" ;;
    esac
}

# Genera un secreto de 32 caracteres alfanumericos.
#
# Alfanumerico y nada mas, a proposito: estas cadenas van a un `CREATE ROLE`, a un
# `EMAIL_HOST_PASSWORD` y a un `--resolve` que se imprime en pantalla. Un alfabeto
# mas rico (comillas, $, espacios) obliga a que cada consumidor tenga su
# propio escapado, y el que se olvide de uno rompe la instalacion con un error
# que no menciona el caracter problematico. 32 caracteres alfanumericos son
# ~190 bits: por encima de lo que se puede forzar.
gen_secret() {
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 32
    printf ''
}

print_once_secret() {
    # Imprime un secreto recien generado, UNA vez, con el aviso de guardarlo.
    # Un segundo aviso (por ejemplo en el resumen) lo convertiria en un secreto
    # impreso dos veces, que es la mitad de razon por la que se imprime.
    #
    # El tercer argumento NO es siempre el mismo texto: la clave de descifrado se
    # regenera con --rotate-keys y destruye las sesiones, y una contrasena de
    # base o de superusuario se cambia en el .env y nada mas. Decirle a un
    # operador que rotate-keys "restaura" una contrasena de PostgreSQL lo manda a
    # romper la SECRET_KEY por una perdida que no era la que tuvo.
    cat >&2 <<EOF

  ============================================================
  GUARDA ESTE VALOR AHORA. No se vuelve a mostrar:

      $1 = $2

EOF
    printf '  ' >&2
    "$3" >&2
    cat >&2 <<EOF
  ============================================================
EOF
}

# Textos de recuperacion de print_once_secret. Son functions y no texto plano
# porque cada secreto tiene un procedimiento distinto, y confundirlos es el error
# caro: --rotate-keys no restaura una contrasena de base de datos.
recover_django_keys() {
    printf 'Queda cifrada en %s, con la ENCRYPTION_KEY de %s.\n' "$ENV_FILE" "$ENCRYPTION_ENV"
    printf 'Se recupera con `python scripts/generate_env.py --production --rotate-keys`,\n'
    printf 'que regenera el par: eso invalida TODAS las sesiones y cookies firmadas.\n'
}

recover_env_password() {
    # Este mensaje decia "queda escrita en el .env" y era falso: el instalador
    # nunca escribe SUPERUSER_PASSWORD ahi. Un operador que pierde la contrasena
    # con esa frase en la mano busca una clave que no existe, y la busca en el
    # unico archivo donde no puede estar. Lo unico que queda es el hash en la
    # base, asi que la unica salida es cambiarla.
    printf 'No queda guardada en ningun archivo: solo el hash en la base.\n'
    printf '  Para cambiarla:\n'
    printf '    sudo systemd-run --quiet --pipe --wait --uid=%s \\\n' "$SERVICE_USER"
    printf '      -p EnvironmentFile=%s -p Environment=PRODUCTION=1 \\\n' "$ENCRYPTION_ENV"
    printf '      --working-directory=%s %s manage.py changepassword %s\n' \
        "$APP_DIR" "$VENV_DIR/bin/python" "$SUPERUSER_USERNAME"
}

# --- Superusuario: consulta y reseteo ------------------------------------
# Usan el mismo systemd-run que `manage()` pero con su propio
# `-p Environment=`, cosa que `manage()` no puede agregar: por eso no se reusa.
# Se ejecutan despues de `migrate`, asi que la tabla ya existe.
superuser_exists() {
    systemd-run --quiet --pipe --wait \
        --uid="$SERVICE_USER" --gid="$SERVICE_USER" \
        -p "EnvironmentFile=$ENCRYPTION_ENV" \
        -p Environment=PRODUCTION=1 \
        -p "Environment=MPLCONFIGDIR=$MPLCONFIGDIR" \
        --working-directory="$APP_DIR" \
        "$VENV_DIR/bin/python" manage.py shell -c \
        "import sys
from django.contrib.auth import get_user_model
sys.exit(0 if get_user_model().objects.filter(username='$SUPERUSER_USERNAME').exists() else 1)"
}

reset_superuser_password() {
    # La contrasena viaja por entorno y no por argv: en `ps` la veria cualquiera
    # de la maquina. Es el mismo camino que usa `createsuperuser` mas abajo.
    systemd-run --quiet --pipe --wait \
        --uid="$SERVICE_USER" --gid="$SERVICE_USER" \
        -p "EnvironmentFile=$ENCRYPTION_ENV" \
        -p Environment=PRODUCTION=1 \
        -p "Environment=MPLCONFIGDIR=$MPLCONFIGDIR" \
        -p "Environment=DJANGO_SUPERUSER_PASSWORD=$SUPERUSER_PASSWORD" \
        --working-directory="$APP_DIR" \
        "$VENV_DIR/bin/python" manage.py shell -c \
        "import os
from django.contrib.auth import get_user_model
u = get_user_model().objects.get(username='$SUPERUSER_USERNAME')
u.set_password(os.environ['DJANGO_SUPERUSER_PASSWORD'])
u.is_staff = True
u.is_superuser = True
u.is_active = True
u.save()"
}

print_changepassword_hint() {
    printf '  sudo systemd-run --quiet --pipe --wait --uid=%s \\\n' "$SERVICE_USER" >&2
    printf '    -p EnvironmentFile=%s -p Environment=PRODUCTION=1 \\\n' "$ENCRYPTION_ENV" >&2
    printf '    --working-directory=%s %s manage.py changepassword %s\n' \
        "$APP_DIR" "$VENV_DIR/bin/python" "$SUPERUSER_USERNAME" >&2
}

# ¿Esta corrida tiene que preguntar? En dry-run no: preguntar por valores que
# despues no se usan solo le roba tiempo al operador. En --non-interactive
# tampoco: responder por stdin sin tener terminal es un deadlock.
can_prompt() {
    [ "$INTERACTIVE" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]
}

# ask <CLAVE> <default> <pregunta> [validador]
ask() {
    local key=$1 default=$2 question=$3 validator=${4:-}
    local answer='' tries=0 source=default
    if ! can_prompt; then
        if [ -n "${!key:-}" ] && [ "${!key}" != "$default" ]; then
            answer=${!key}
            source=entorno
        else
            answer=$default
        fi
        printf '  [dry-run] %s=%s (%s)\n' "$key" "$(mask "$key" "$answer")" \
            "$([ "$DRY_RUN" -eq 1 ] && echo dry-run || echo no-interactivo)" >&2
        printf '%s' "$answer"
        return 0
    fi
    # Antes de pintar la pregunta hay que borrar la linea de la barra. Sin
    # esto el prompt aparece pegado al porcentaje:
    #   [#----------] 4%  Usuario SMTP: ...
    progress_break
    while :; do
        printf '%s [%s]: ' "$question" "$default" >&2
        IFS= read -r answer || die "EOF leyendo la respuesta de $key."
        answer=${answer:-$default}
        # Vacio con default vacio es una respuesta valida y EXPLICITA: "esto todavia
        # no existe" (el SMTP que aun no hay, el correo de un registro opcional).
        # Sin esta excepcion el validador rechaza el vacio, el operador reintenta
        # tres veces y el instalador muere en el campo que justamente era opcional.
        if { [ -z "$answer" ] && [ -z "$default" ]; } \
            || [ -z "$validator" ] || "$validator" "$answer" >/dev/null 2>&1
        then
            source=pregunta
            printf '%s' "$answer"
            ANSWER_SOURCES+=("$key=$source")
            return 0
        fi
        tries=$((tries + 1))
        printf '  Valor invalido para %s.\n' "$key" >&2
        if [ "$tries" -ge "$MAX_PROMPT_TRIES" ]; then
            printf '  %s intentos agotados.\n' "$MAX_PROMPT_TRIES" >&2
            return 1
        fi
        printf '  Reintentos restantes: %s\n' "$((MAX_PROMPT_TRIES - tries))" >&2
    done
}

# Canal lateral para avisar "este secreto lo genero el instalador".
#
# No puede ser una variable global: `ask_secret` se invoca dentro de una
# sustitucion de comando (`X=$(ask_secret ...)`), asi que corre en un subshell y
# cualquier `ANSWER_SOURCES+=` suyo muere con el subshell. Por eso el flag viaja
# en un archivo temporal: lo escribe el subshell y lo lee el padre, que es el
# unico que decide si el secreto hay que mostrarlo.
# (ASK_GENERATED_FILE se crea arriba, junto a los demas estado del instalador.)
secret_was_generated() {
    [ -n "$ASK_GENERATED_FILE" ] && [ -f "$ASK_GENERATED_FILE" ] || return 1
    grep -Fxq -- "$1" "$ASK_GENERATED_FILE"
}

# ask_secret <CLAVE> <default> <pregunta> [validador]
#
# `read -s`: una contrasena tipeada en un servidor sale en el scrollback del
# terminal y en cualquier grabacion de sesion. Si se deja vacio se genera una y
# se imprime UNA vez, solo en ese caso (ver `secret_was_generated`). En una
# corrida no interactiva NO se imprime: el valor podria estar yendo a un log de
# CI, y un secreto en un log ya no es un secreto.
ask_secret() {
    local key=$1 default=$2 question=$3 validator=${4:-}
    local answer='' tries=0
    if ! can_prompt; then
        if [ -n "${!key:-}" ]; then
            answer=${!key}
        elif [ -n "$default" ]; then
            answer=$default
        else
            answer=$(gen_secret)
            [ -n "$ASK_GENERATED_FILE" ] && printf '%s\n' "$key" >>"$ASK_GENERATED_FILE"
            printf '  [dry-run] %s=<generada, escrita en el .env, no impresa>\n' "$key" >&2
            printf '%s' "$answer"
            return 0
        fi
        printf '  [dry-run] %s=<oculto> (%s)\n' "$key" \
            "$([ "$DRY_RUN" -eq 1 ] && echo dry-run || echo no-interactivo)" >&2
        printf '%s' "$answer"
        return 0
    fi
    progress_break
    while :; do
        if [ -n "$default" ]; then
            printf '%s (Enter = se conserva la actual): ' "$question" >&2
        else
            printf '%s (Enter = generar una): ' "$question" >&2
        fi
        IFS= read -r -s answer || die "EOF leyendo $key."
        printf '\n' >&2
        answer=${answer:-$default}
        if [ -z "$answer" ]; then
            answer=$(gen_secret)
            [ -n "$ASK_GENERATED_FILE" ] && printf '%s\n' "$key" >>"$ASK_GENERATED_FILE"
            printf '%s' "$answer"
            ANSWER_SOURCES+=("$key=generada")
            return 0
        fi
        if [ -z "$validator" ] || "$validator" "$answer" >/dev/null 2>&1; then
            printf '%s' "$answer"
            ANSWER_SOURCES+=("$key=pregunta")
            return 0
        fi
        tries=$((tries + 1))
        printf '  Valor invalido para %s.\n' "$key" >&2
        if [ "$tries" -ge "$MAX_PROMPT_TRIES" ]; then
            printf '  %s intentos agotados.\n' "$MAX_PROMPT_TRIES" >&2
            return 1
        fi
        printf '  Reintentos restantes: %s\n' "$((MAX_PROMPT_TRIES - tries))" >&2
    done
}

# ask_optional_secret <CLAVE> <default> <pregunta>
#
# Igual que ask_secret, salvo que el vacio es una respuesta valida: no genera
# nada. Existe para los secretos que son opcionales de verdad (el SMTP que todavia
# no hay, el bind de LDAP si el servidor no tiene password). Contrasena de la base
# y del superusuario NO usan esta: ahi el vacio significa generar.
ask_optional_secret() {
    local key=$1 default=$2 question=$3
    local answer=''
    if ! can_prompt; then
        if [ -n "${!key:-}" ]; then
            answer=${!key}
        else
            answer=$default
        fi
        printf '  [dry-run] %s=%s (%s)\n' "$key" \
            "$([ -n "$answer" ] && mask "$key" "$answer" || printf '<vacio>')" \
            "$([ "$DRY_RUN" -eq 1 ] && echo dry-run || echo no-interactivo)" >&2
        printf '%s' "$answer"
        return 0
    fi
    progress_break
    printf '%s (Enter = dejarlo vacio): ' "$question" >&2
    IFS= read -r -s answer || die "EOF leyendo $key."
    printf '\n' >&2
    printf '%s' "${answer:-$default}"
    ANSWER_SOURCES+=("$key=pregunta")
    return 0
}

# ask_yes_no <default si|no> <pregunta>
#
# Devuelve 0 por si. Acepta s/si/yes/y y n/no, en mayusculas y minusculas.
# Un bucle de repregunta infinito en un instalador colgado deja al operador sin
    # salida y sin saber por que, asi que los tres reintentos estan contados.
# Y con una respuesta que no sea si/no NO se asume el default: "se asume si" por
    # culpa de una tecla mal apretada es como se instala lo que no se queria.
# Un prompt NUNCA se escribe encima de la barra: primero se borra la linea con
# progress_break, se imprime la pregunta, y la barra vuelve sola en el proximo
# step.
#
# Que NO se redibuje la barra despues del prompt es deliberado. Se intento, y
# queda peor: progress_draw no emite salto de linea, asi que al volver a pintar
# la barra el siguiente prompt se pegaba a ella en la misma linea:
#
#   [#-----------------------------]   4%  Usuario SMTP: login o correo completo
#
# Ademas el prompt se pide con la barra al 4%, cuando en realidad no hay nada
# que mostrar todavia. La barra se levanta en cada step, y un step siempre esta
# seguido de un log o de un comando en vivo, que si.info redibujan.

ask_yes_no() {
    local default=$1 question=$2 answer='' other tries=0
    other=$([ "$default" = si ] && echo no || echo si)
    if ! can_prompt; then
        printf '  [dry-run] (si=%s, no=%s) %s -> %s\n' "$default" "$other" "$question" "$default" >&2
        [ "$default" = si ]
        return
    fi
    progress_break
    while :; do
        printf '%s [%s/%s] (vacio = %s): ' "$question" "$default" "$other" "$default" >&2
        IFS= read -r answer || die "EOF leyendo la respuesta."
        answer=${answer:-$default}
        case "$answer" in
            s | S | si | SI | yes | y | Y)
                ANSWER_SOURCES+=("($1)=si")
                return 0 ;;
            n | N | no | NO)
                ANSWER_SOURCES+=("($1)=no")
                return 1 ;;
        esac
        tries=$((tries + 1))
        printf '  Se entiende si/no. Reintenta.\n' >&2
        if [ "$tries" -ge "$MAX_PROMPT_TRIES" ]; then
            printf '  %s intentos agotados.\n' "$MAX_PROMPT_TRIES" >&2
            return 1
        fi
    done
}

# ask_one_of <CLAVE> <default> <pregunta> <opcion1> <opcion2> ...
# Devuelve SIEMPRE el TOKEN de la opcion, nunca el texto de descripcion: las
# opciones se escriben como "token: descripcion" para poder imprimir una ayuda
# legible en el prompt, pero el valor que consume el resto del instalador es el
# token. Se usa donde la respuesta es una palabra y el numero es incomodo de
# recordar (que elegiste, 1 o 2).
ask_one_of() {
    local key=$1 default=$2 question=$3
    shift 3
    local index=0 option answer tries=0 shift_count=0 token lista
    # El break va ANTES del for: estas opciones se imprimen una por linea y la
    # primera se pegaba a la barra.
    progress_break
    for option in "$@"; do
        index=$((index + 1))
        printf '    %s) %s\n' "$index" "$option" >&2
    done
    local prompt="$question"
    if ! can_prompt; then
        # La respuesta sale de la variable de entorno del mismo nombre: es lo
        # que promete el propio mensaje del preflight ("cada valor sale de la
        # variable de entorno del mismo nombre o del default"). Antes esta
        # funcion IGNORABA el env y devolvia siempre el default, y en modo no
        # interactivo eso no es un detalle de comodidad: PROXY_MODE=external
        # seguia dando nginx-local, la opcion B era inalcanzable sin terminal,
        # y el instalador instalaba y configuraba Nginx donde se le habia
        # pedido proxy externo. Mismo bug que el valor numerico de antes: se
        # recibe la respuesta y despues no se usa.
        if [ -n "${!key:-}" ]; then
            answer=${!key}
            # El valor del env se VALIDA contra las opciones. Un token que no
            # matchea ninguna rama es la misma trampa: instalaria con un modo
            # de proxy que ninguna rama del script conoce.
            for option in "$@"; do
                token=${option%%:*}
                if [ "$answer" = "$token" ]; then
                    printf '  [dry-run] %s=%s (%s)\n' "$key" "$(mask "$key" "$answer")" \
                        "$([ "$DRY_RUN" -eq 1 ] && echo dry-run || echo no-interactivo)" >&2
                    printf '%s' "$answer"
                    return 0
                fi
            done
            lista=''
            for option in "$@"; do lista="$lista${option%%:*} "; done
            die "$key='$answer' no es un valor valido. Opciones: $lista"
        fi
        printf '  [dry-run] %s=%s (default)\n' "$key" "$(mask "$key" "$default")" >&2
        printf '%s' "$default"
        return 0
    fi
    while :; do
        printf '%s [%s]: ' "$prompt" "$default" >&2
        IFS= read -r answer || die "EOF leyendo la respuesta de $key."
        answer=${answer:-$default}
        if [ "$answer" = "$default" ]; then
            printf '%s' "$default"
            ANSWER_SOURCES+=("$key=$default")
            return 0
        fi
        # El operador puede responder por numero o por palabra, y ambos tienen
        # que caer en el MISMO token. Antes el numero devolvia el texto entero
        # ("nginx-local: Nginx instalado y configurado..."), y como todos los
        # call sites comparan con el token, elegir 1 en el prompt de PROXY_MODE
        # terminaba con un valor que no matcheaba NINGUNA de las ramas: ni
        # nginx-local ni external. Un instalador que recibe la respuesta que le
        # pediste y luego la ignora es peor que uno que no pregunta.
        if printf '%s' "$answer" | grep -Eq '^[0-9]+$'; then
            index=$answer
            shift_count=0
            for option in "$@"; do
                shift_count=$((shift_count + 1))
                if [ "$shift_count" -eq "$index" ]; then
                    token=${option%%:*}
                    printf '%s' "$token"
                    ANSWER_SOURCES+=("$key=$token")
                    return 0
                fi
            done
        fi
        # Acepta tambien la PALABRA que abre cada opcion, que es lo que decia el
        # propio mensaje de error de abajo ("o el texto exacto") y lo que el
        # docstring promete ("donde la respuesta es una palabra"). Antes solo
        # pasaba el default: escribir 'no' o 'external' caia siempre en
        # "Opcion invalida", asi que a un operador atento le quedaba la
        # sensacion de que el prompt no entendia su propia pregunta.
        for option in "$@"; do
            token=${option%%:*}
            if [ "$answer" = "$token" ]; then
                printf '%s' "$token"
                ANSWER_SOURCES+=("$key=$token")
                return 0
            fi
        done
        # No se repregunta para siempre: un bucle infinito de repregunta deja al
        # operador sin salida y sin saber por que.
        tries=$((tries + 1))
        printf '  Opcion invalida para %s (numero del 1 al %s, o el texto exacto).\n' \
            "$key" "$(($# + 0))" >&2
        if [ "$tries" -ge "$MAX_PROMPT_TRIES" ]; then
            printf '  %s intentos agotados.\n' "$MAX_PROMPT_TRIES" >&2
            return 1
        fi
    done
}

# --------------------------------------------------------------------------
# Utilidades de archivos
# --------------------------------------------------------------------------

# env_set <archivo> <CLAVE> <valor>
#
# Reemplaza la linea KEY=... o la agrega al final. El valor viaja por el ENTORNO
# y no por `-v` de awk a proposito: awk procesa escapes en el valor de `-v`, asi
# que una contrasena con `\n` o `\1` llegaria al archivo como dos caracteres. Por
# ENVIRON el valor sale literal, que es lo unico aceptable en un archivo con
# contrasenas.
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

# env_get <archivo> <CLAVE>  -> valor, o vacio
env_get() {
    [ -f "$1" ] || return 0
    awk -v k="$2" 'index($0, k "=") == 1 { sub("^" k "=", ""); print; exit }' "$1"
}

# psql_quote <valor> -> argumento de \set de psql entre comillas simples
#
# El escapado es la unica defensa contra una contrasena con comilla o barra: sin
# esto, un `'` cierra el literal de psql y el resto se ejecuta. El escapado es
# con barra invertida, porque aca lo hace el lexer de \set, no SQL.
psql_quote() {
    local v=$1
    v=${v//\\/\\\\}
    v=${v//\'/\\\'}
    printf "'%s'" "$v"
}

# render_template <origen> <destino> <viejo><><nuevo> [<viejo><><nuevo> ...]
#
# Sustitucion LITERAL, no de expresion regular. No es purismo: `gsub` de awk
# trata el patron como regex, y el par de reemplazos mas importante aca es
# `webcmp` -> `<SERVICE_USER>`. Como regex, `webcmp` tambien matchea
# `webcmp-huey` y el nombre del zone de cache de Nginx, y el vhost renderizado
# apuntaria a un socket que no existe. Ademas los pares llegan por el entorno
# (ENVIRON) y no por `-v`, por el mismo motivo que env_set.
#
# El separador del par es `<>` y NO `=` a proposito: varios de los pares que este
# script necesita tienen un `=` adentro, tanto del lado viejo como del nuevo
# (`User=webcmp` -> `User=webcmp-app`, `deploy ALL=(root)` -> `ci-deploy
# ALL=(root)`). Con `viejo=nuevo`, el shell parte por el PRIMER `=`, que en esos
# casos esta en el medio de la clave, y el resultado no es un error visible sino
# un archivo generado con basura que pasa `bash -n` y revienta en el servidor.
render_template() {
    local src=$1 dest=$2
    shift 2
    local old_list='' new_list='' count=0 pair
    for pair in "$@"; do
        old_list+="${pair%%<>*}"$'\x1f'
        new_list+="${pair#*<>}"$'\x1f'
        count=$((count + 1))
    done
    RT_COUNT=$count RT_OLD_LIST="$old_list" RT_NEW_LIST="$new_list" awk '
        function litrep(s, old, new,   out, p) {
            if (old == "") return s
            out = ""
            while ((p = index(s, old)) > 0) {
                out = out substr(s, 1, p - 1) new
                s = substr(s, p + length(old))
            }
            return out s
        }
        BEGIN {
            n = ENVIRON["RT_COUNT"]
            split(ENVIRON["RT_OLD_LIST"], O, "\037")
            split(ENVIRON["RT_NEW_LIST"], W, "\037")
        }
        { line = $0; for (i = 1; i <= n; i++) line = litrep(line, O[i], W[i]); print line }
    ' "$src" > "$dest"
}

# manage <manage.py args...>
#
# Corre manage.py en el MISMO contexto que corre Gunicorn: una unidad transitoria
# con el EnvironmentFile de la clave de descifrado y PRODUCTION=1, exactamente
# como webcmp.service.
#
# Por que systemd-run y no un shell normal: `manage.py` necesita leer la
# ENCRYPTION_KEY de $ENCRYPTION_ENV (600, root) y correr como SERVICE_USER, que
# no puede leerla. systemd resuelve las dos cosas: lee el EnvironmentFile como
# root antes de hacer el drop de privilegios.
#
# Por que NO un `sudo -u webcmp manage.py`: sin PRODUCTION cae al perfil de
# desarrollo (config/settings/__init__.py elige perfil por variable de entorno),
# y migra con las credenciales que encuentre en el entorno en vez de con las de
# produccion. El instalador "pasa" y deja la base equivocada sin un solo error:
# es el modo de fallo mas caro de este proyecto.
#
# OJO: systemd-run NO hereda el entorno de quien llama. Por eso las variables
# extra van con -p Environment=NOMBRE=valor y nunca con un `VAR=x systemd-run`.
manage() {
    if [ "$DRY_RUN" -eq 1 ]; then
        # Se imprime la invocacion COMPLETA y no un "manage.py makemigrations"
        # de adorno: lo que hay que poder comparar contra el estado real del
        # servidor es si PRODUCTION=1 y el EnvironmentFile van a ir o no.
        printf '  [dry-run] systemd-run --quiet --pipe --wait --uid=%s --gid=%s \\\n' \
            "$SERVICE_USER" "$SERVICE_GROUP"
        printf '    -p EnvironmentFile=%s -p Environment=PRODUCTION=1 \\\n' "$ENCRYPTION_ENV"
        printf '    -p Environment=MPLCONFIGDIR=%s --working-directory=%s \\\n' \
            "$MPLCONFIGDIR" "$APP_DIR"
        printf '    %s manage.py' "$VENV_DIR/bin/python"
        printf ' %s\n' "$*"
        return 0
    fi
    if [ "${LIVE_MANAGE:-0}" -eq 1 ]; then
        # El log de `manage.py` va en vivo (ver `run_live`). Las migraciones de
        # un proyecto con apps de produccion son la parte mas lenta de esta
        # seccion y sin esto el operador ve la barra parada en "Migraciones" sin
        # saber si Django esta aplicando 40 migraciones o colgado.
        run_live "manage.py $*" \
            systemd-run --quiet --pipe --wait \
            --uid="$SERVICE_USER" --gid="$SERVICE_GROUP" \
            -p "EnvironmentFile=$ENCRYPTION_ENV" \
            -p Environment=PRODUCTION=1 \
            -p "Environment=MPLCONFIGDIR=$MPLCONFIGDIR" \
            --working-directory="$APP_DIR" \
            "$VENV_DIR/bin/python" manage.py "$@"
        return $?
    fi
    systemd-run --quiet --pipe --wait \
        --uid="$SERVICE_USER" --gid="$SERVICE_GROUP" \
        -p "EnvironmentFile=$ENCRYPTION_ENV" \
        -p Environment=PRODUCTION=1 \
        -p "Environment=MPLCONFIGDIR=$MPLCONFIGDIR" \
        --working-directory="$APP_DIR" \
        "$VENV_DIR/bin/python" manage.py "$@"
}

# ¿Hay systemd para systemd-run? Sin esto, manage() no puede correr y no hay
# reemplazo honesto: el unico fallback seria el perfil de desarrollo.
require_systemd() {
    command -v systemd-run >/dev/null 2>&1 \
        || die "systemd-run no esta instalado. Sin el no se puede correr manage.py con PRODUCTION=1 y la ENCRYPTION_KEY, y el instalador no arranca el sitio en el perfil equivocado."
    [ -d /run/systemd/system ] \
        || die "systemd no esta corriendo (falta /run/systemd/system). Este instalador no funciona en un contenedor sin systemd: manage.py caeria al perfil de desarrollo y migraria la base equivocada."
}

# ==========================================================================
# FASE A. Preflight. SOLO LECTURA: nada de esto toca el sistema, para que un
# error aqui deje el servidor exactamente como estaba.
# ==========================================================================

# La barra arranca antes del primer paso, que es el Preflight.
progress_begin

step 2 "Preflight"

[ "$(id -u)" -eq 0 ] || die "Hay que correrlo como root (sudo)."

# Fail closed en la terminal. El unico camino sin preguntas es --non-interactive,
# y --dry-run NO es una excepcion: aunque no muta nada, el mensaje de abajo dice
# el comando exacto que si funciona sin TTY, asi que dejarlo pasar solo agrega una
# forma de correr el instalador a medias. --help ya salio antes de llegar aca.
if [ "$INTERACTIVE" -eq 1 ] && [ ! -t 0 ]; then
    die "Este instalador necesita una terminal: va a preguntar el dominio, la base de datos y el correo.

Como no hay TTY (pipe, cron o CI) y no se paso --non-interactive, se aborta SIN
TOCAR NADA. Un instalador que adivina en un pipe escribe el .env equivocado y
el error aparece cuando el sitio ya esta sirviendo otra base de datos.

Para correrlo sin preguntas:

    curl -fsSL <raw-url>/deploy/install.sh | sudo bash -s -- --non-interactive

Cada valor sale de la variable de entorno del mismo nombre o del default; el
resumen final dice de donde salio cada uno. Para verlo primero, sin cambiar nada,
sumale --dry-run:

    curl -fsSL <raw-url>/deploy/install.sh | sudo bash -s -- --dry-run --non-interactive

O descargalo y corrélo con terminal:
    curl -fsSL -o install.sh <raw-url>/deploy/install.sh && sudo bash install.sh"
fi

# Distribucion. Todo el instalador es apt: no hay equivalente probado para
# dnf/apk y adivinar el gestor de paquetes produce un script que instala la
# mitad de las cosas.
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO_ID=${ID:-desconocida}
    DISTRO_LIKE=${ID_LIKE:-}
else
    DISTRO_ID=desconocida
    DISTRO_LIKE=
fi
case " $DISTRO_ID $DISTRO_LIKE " in
    *" debian "* | *" ubuntu "*) ;;
    *) die "Solo Debian/Ubuntu: este instalador usa apt y esta probado ahi.
  Distribucion detectada: ID=$DISTRO_ID ID_LIKE=${DISTRO_LIKE:-ninguno}.
  En otra distro, corre a mano los pasos que imprime --dry-run." ;;
esac
echo "  distro: $PRETTY_NAME ($DISTRO_ID)"

command -v apt-get >/dev/null 2>&1 || die "apt-get no esta disponible."
command -v openssl >/dev/null 2>&1 || warn "openssl no esta: se necesita para el certificado autofirmado."
# curl se instala con APT_PACKAGES (el health check de cada deploy depende de el),
# asi que aca no se avisa: un aviso que dice "falta curl" y trescientas lineas
# despues lo instala entrena al operador a ignorar los avisos.

# Python >= MIN_PYTHON. El motivo concreto esta en el comment de MIN_PYTHON
# arriba; aca solo se traduce a un error que lo dice.
PYTHON_BIN=${PYTHON_BIN:-python3}
PYTHON_VERSION=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '')
if [ -z "$PYTHON_VERSION" ]; then
    die "No se pudo ejecutar $PYTHON_BIN. Instalá python3 y volve a correr."
fi
# El (3, 14) va con coma a proposito: `tuple(3 14)` no es Python valido y
# fallaria con un SyntaxError que no dice nada de la version.
if ! "$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (${MIN_PYTHON[0]}, ${MIN_PYTHON[1]}) else 1)"; then
    die "Se necesita Python ${MIN_PYTHON[0]}.${MIN_PYTHON[1]} o superior; encontrado $PYTHON_VERSION.

No es una preferencia: scripts/generate_env.py usa PEP 758 (except sin parentesis,
\`except A, B:\`), que solo existe desde Python 3.14. Con una version anterior el
generador no compila y falla con un SyntaxError que no menciona la version."
fi
echo "  python: $PYTHON_VERSION (minimo ${MIN_PYTHON[0]}.${MIN_PYTHON[1]})"

require_systemd
echo "  systemd-run disponible."

# Estado previo, SOLO de lectura. Se usa para armar los defaults de las
# preguntas (el default es el valor vigente) y para avisar de re-corridas.
ENV_FILE=$APP_DIR/.env
ENV_FILE_EXISTS=0
[ -f "$ENV_FILE" ] && ENV_FILE_EXISTS=1
CONFIG_FILE_EXISTS=0
[ -f "$CONFIG_FILE" ] && CONFIG_FILE_EXISTS=1
# Tres estados distintos, y el nombre viejo los mezclaba en uno solo:
# APP_DIR_EXISTS decia "existe", pero en realidadologia "-d $APP_DIR/.git", o
# sea "¿es un checkout?". Con esa mentira el instalador no distinguia "directorio
# vacio" de "directorio con cosas dentro que no son un checkout", y en el
# segundo caso caia directo al git clone, que se niega a clonar sobre un destino
# no vacio y abortaba con "destination path already exists and is not an empty
# directory". Cada estado se resuelve distinto, asi que se mide cada uno.
APP_DIR_PRESENT=0
APP_DIR_EMPTY=1
HAS_CHECKOUT=0
if [ -d "$APP_DIR" ]; then
    APP_DIR_PRESENT=1
    [ -d "$APP_DIR/.git" ] && HAS_CHECKOUT=1
    # -mindepth 1 porque la entrada "." del propio directorio no cuenta, y
    # -print -quit corta en el primer hallazgo: para saber si hay algo alcanza
    # con encontrar uno, y listar el arbol entero de media/ seria una TONELADA de
    # lineas en pantalla.
    [ -n "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ] \
        && APP_DIR_EMPTY=0
fi

if [ "$HAS_CHECKOUT" -eq 1 ]; then
    echo "  $APP_DIR ya es un checkout: se reusa (idempotente)."
fi
if [ "$APP_DIR_PRESENT" -eq 1 ] && [ "$APP_DIR_EMPTY" -eq 0 ] && [ "$HAS_CHECKOUT" -eq 0 ]; then
    echo "  $APP_DIR existe, tiene contenido y NO es un checkout de git."
fi
if [ "$ENV_FILE_EXISTS" -eq 1 ]; then
    echo "  $ENV_FILE ya existe: se conserva y sus valores son los defaults."
fi
if [ "$CONFIG_FILE_EXISTS" -eq 1 ]; then
    echo "  $CONFIG_FILE ya existe: NO se pisa sin confirmacion."
fi

# ==========================================================================
# FASE B. Preguntas. Tampoco muta nada: por eso el instalador puede fallar
# aqui sin dejar el servidor a medio instalar.
# ==========================================================================

step 1 "Configuracion (todo tiene default; Enter para conservarlo)"

EXTERNAL_HOSTNAME=$(ask PUBLIC_HOSTNAME "${PUBLIC_HOSTNAME:-www.meteocamaguey.cu}" \
    'Dominio publico del sitio (EXTERNAL_HOSTNAME del .env, server_name de Nginx, SNI del health check)' \
    valid_hostname) || die 'Dominio invalido.'

# El alias se CALCULA, no se prefija. Con un canonico que ya es `www.x`,
# `www.$EXTERNAL_HOSTNAME` daria `www.www.x`: un nombre que no existe en DNS y
# que hace fallar la emision del certificado con un error de validacion de
# dominio. En ese caso el alias es el dominio sin `www`, y el default es `no`
# porque la URL publicada es la que lleva `www`.
if [[ "$EXTERNAL_HOSTNAME" == www.* ]]; then
    ALIAS_HOSTNAME=${EXTERNAL_HOSTNAME#www.}
    ALIAS_DEFAULT=no
else
    ALIAS_HOSTNAME=www.$EXTERNAL_HOSTNAME
    ALIAS_DEFAULT=si
fi
USE_WWW=$(ask_one_of USE_WWW "$ALIAS_DEFAULT" "Sirve tambien en $ALIAS_HOSTNAME?" \
    "si: anade $ALIAS_HOSTNAME al vhost, al ALLOWED_HOSTS y al certificado" \
    'no: solo el dominio publico') || die 'Opcion invalida.'
if [ "$USE_WWW" = si ]; then
    WWW_HOSTNAME=$ALIAS_HOSTNAME
else
    WWW_HOSTNAME=
fi

HTTP_PORT=$(ask HTTP_PORT 80 'Puerto HTTP (se redirige a HTTPS si hay TLS)' valid_port) || die 'Puerto invalido.'
HTTPS_PORT=$(ask HTTPS_PORT 443 'Puerto HTTPS' valid_port) || die 'Puerto invalido.'

PROXY_MODE=$(ask_one_of PROXY_MODE nginx-local 'Reverse proxy' \
    'nginx-local: Nginx instalado y configurado en esta maquina, con TLS' \
    'external: el TLS lo termina un proxy externo (Nginx Proxy Manager) en otra maquina. Nginx SI se instala aca para servir /media/ y /static/' \
    ) || die 'Opcion invalida.'

# TLS_MODE NO se pre-asigna a `none` antes de la pregunta. ask_one_of en modo no
# interactivo lee ${!key} y lo valida contra las opciones: con `none` ya puesto
# la validacion fallaba SIEMPRE y el instalador moria aca con
# "TLS_MODE='none' no es un valor valido". El default de la pregunta es `certbot`;
# `none` es el valor del modo proxy externo, donde el TLS se resuelve afuera.
TLS_MODE=${TLS_MODE:-}
ACME_EMAIL=
if [ "$PROXY_MODE" = nginx-local ]; then
    # El aviso va ACA y no despues de elegir, porque la eleccion de un
    # certificado autofirmado es la que deja el sitio en rojo en cada navegador
    # y la que obliga a HEALTHCHECK_INSECURE=1.
    cat >&2 <<EOF

  Con proxy nginx-local, el TLS se resuelve aca:

    certbot  -> requiere que $EXTERNAL_HOSTNAME resuelva a esta maquina desde
                Internet y que los puertos $HTTP_PORT y $HTTPS_PORT esten abiertos
                hacia afuera. Emite un certificado que los navegadores confian.
    autofirmado -> funciona sin DNS publico ni puertos abiertos, pero el
                navegador lo muestra como no seguro en cada visita y el health
                check de deploy.sh necesita HEALTHCHECK_INSECURE=1, con lo que
                deja de detectar un certificado vencido.
EOF
    TLS_MODE=$(ask_one_of TLS_MODE certbot 'TLS' \
        'certbot: DNS publico y puertos abiertos, certificado de confianza' \
        'self-signed: certificado autofirmado, con las consecuencias de arriba' \
        ) || die 'Opcion invalida.'
    if [ "$TLS_MODE" = certbot ]; then
        # Default vacio a proposito: este bloque (TLS) va ANTES del bloque de correo, asi
# que EMAIL_HOST_USER todavia no esta preguntado. Ponerlo de default usaria una
# variable sin asignar bajo `set -u`, que en bash no es "" sino un error que
# mata el instalador. El correo de ACME lo elige el operador explicitamente.
ACME_EMAIL=$(ask ACME_EMAIL "" "Correo para el registro de Let's Encrypt (vacio = sin registro)" valid_email) || die 'Correo invalido.'
    fi
else
    # Proxy externo: el TLS se termina en el proxy, no aca. `none` es un valor
    # fijo del modo, no una respuesta del operador, y por eso NO pasa por
    # ask_one_of: con PROXY_MODE=external no hay eleccion que hacer.
    TLS_MODE=none
fi

# El vhost que se renderiza depende del modo, y se decide ACA y no en la
# definicion de `vhost_src`: PROXY_MODE todavia no existia cuando se declaro esa
# variable. Ambos vhosts hacen proxy al mismo socket y sirven los mismos
# directorios; la diferencia es que `external` no hace TLS (lo termina el proxy)
# y propaga el X-Forwarded-Proto que ese proxy manda en vez de reescribirlo.
if [ "$PROXY_MODE" = external ]; then
    vhost_src=$APP_DIR/deploy/nginx/webcmp-http.conf.example
else
    vhost_src=$APP_DIR/deploy/nginx/webcmp.conf.example
fi

# --- Base de datos -------------------------------------------------------

# Constante, no pregunta: PostgreSQL es el unico motor de produccion. Asignacion
# lisa a proposito, sin `:=` ni lectura del entorno, para que un DB_ENGINE
# exportado por error no pueda contradecir la decision.
DB_ENGINE=postgresql

cat >&2 <<EOF

  Base de datos: PostgreSQL.
  El motor no se pregunta. Es el unico que trae driver en requirements/base.txt
  (psycopg[binary], que prod.txt incluye), y el unico que el proyecto asume para
  produccion. MySQL
  exigiria instalar mysqlclient fuera de los pins, y SQLite no tiene
  concurrencia ni lee DB_*; ninguno de los dos corresponde a un sitio con
  usuarios entrando.
EOF

DB_NAME=$(ask DB_NAME webcmp 'Nombre de la base' valid_db_name) || die 'Nombre de base invalido.'
DB_USER=$(ask DB_USER webcmp 'Usuario de la base (rol en PostgreSQL)' valid_app_user) || die 'Usuario de base invalido.'
DB_HOST=$(ask DB_HOST localhost 'Host de la base de datos' valid_host) || die 'Host invalido.'
DB_PORT=$(ask DB_PORT 5432 'Puerto de PostgreSQL (Enter = 5432)' valid_port) || die 'Puerto invalido.'
DB_SSL_MODE=$(ask DB_SSL_MODE prefer \
    'DB_SSL_MODE (disable|allow|prefer|require|verify-ca|verify-full)' \
    ) || die 'Valor invalido.'

# El default del password es el que YA esta en el .env, si lo hay. Es lo que
# hace segura una re-corrida: sin esto, cada pasada por el instalador cambiaria
# la contrasena de la base y dejaria al Django del servidor sin poder conectarse.
if [ "$ENV_FILE_EXISTS" -eq 1 ]; then
    EXISTING_DB_PASS=$(env_get "$ENV_FILE" DB_PASS)
else
    EXISTING_DB_PASS=
fi
# Mismo criterio que SUPERUSER_PASSWORD: el aviso depende de si el operador
# dejo el campo vacio (y entonces se genero), no de si no habia una antes. Si la
# tecleo, ya la tiene; volver a imprimirla solo la mete en el scrollback.
DB_PASS=$(ask_secret DB_PASS "$EXISTING_DB_PASS" 'Contrasena de la base de datos') || die 'Contrasena invalida.'
# INTERACTIVE y no solo DRY_RUN: un `DRY_RUN=0 --non-interactive` es un job de CI,
# y ahi el aviso fuera del prompt se va al log. Sin operador delante, "guardar esto"
# no tiene a quien avisarle y el secreto ya quedo en el .env de todas formas.
if secret_was_generated DB_PASS && [ "$INTERACTIVE" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
    print_once_secret 'DB_PASS' "$DB_PASS" recover_env_password
fi

# --- Correo --------------------------------------------------------------

cat >&2 <<EOF

  Correo. En produccion, config/settings/production.py RECHAZA los backends
  silenciosos (consola, filebased, locmem): aceptan el mensaje y lo descartan.
  El instalador no ofrece esa opcion a proposito.

  Se configura el correo aunque el servidor todavia no responda: el instalador
  no prueba conectividad SMTP en ningun momento, solo valida el formato. Si el
  host no existe todavia, escribe el que va a existir y el envio funcionara en
  cuanto el servidor este arriba.
EOF
EMAIL_HOST=$(ask EMAIL_HOST "mx.caonao.cu" 'Servidor SMTP, SIN @ (vacio = todavia no hay correo)' valid_smtp_host) || die 'Host SMTP invalido. Es el servidor sin @ (ej. mx.caonao.cu); la direccion de correo va en "Usuario SMTP".'
EMAIL_PORT=$(ask EMAIL_PORT 587 'Puerto SMTP' valid_port) || die 'Puerto SMTP invalido.'
# OJO con el vocabulario. Las PREGUNTAS hablan si/no (es lo que se lee en
# pantalla), pero los VALORES que van al .env son los que Django compara:
# `os.getenv('EMAIL_USE_TLS', 'True') == 'True'`. Escribir `si` deja la
# comparacion en False sin ningun error: el correo sale en claro por el 587 y
# el unico sintoma es un SMTP que rechaza el AUTH o un mensaje que nunca llega.
# Lo mismo vale para EMAIL_USE_SSL y USE_REDIS_CACHE.
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
if [ "$EMAIL_PORT" = 465 ]; then
    # 465 es SMTPS implicito: TLS desde el principio de la conexion. Preguntar
    # STARTTLS ahi produce una combinacion que no existe (SSL y TLS los dos) y
    # Django falla al abrir el socket con un error que no menciona el puerto.
    EMAIL_USE_TLS=False
    EMAIL_USE_SSL=True
    echo "  puerto 465: EMAIL_USE_SSL=True y EMAIL_USE_TLS=False (TLS implicito)."
elif ask_yes_no si 'STARTTLS en el SMTP? (EMAIL_USE_TLS)'; then
    EMAIL_USE_TLS=True
else
    EMAIL_USE_TLS=False
fi
EMAIL_HOST_USER=$(ask EMAIL_HOST_USER "meteocamaguey" 'Usuario SMTP: login o correo completo (vacio = SMTP sin usuario)' valid_smtp_user) || die 'Usuario SMTP invalido.'
EMAIL_HOST_PASSWORD=$(ask_optional_secret EMAIL_HOST_PASSWORD "" 'Contrasena SMTP (vacio = SMTP sin contrasena)') || die 'Contrasena SMTP invalida.'
DEFAULT_FROM_EMAIL=$(ask DEFAULT_FROM_EMAIL 'Centro Meteorológico Provincial Camagüey <meteocamaguey@caonao.cu>' 'Remitente (nombre <correo>)') || die 'Remitente invalido.'

# "Configurado" es solo "hay servidor SMTP". Un relay sin usuario ni contrasena
# (un relay interno en el puerto 25, tipico en un servidor de correo corporativo)
# es una configuracion valida, y exigirle usuario seria dejar un CHANGE_ME
# imposible de resolver sin cambiar de servidor de correo.
if [ -n "$EMAIL_HOST" ]; then
    EMAIL_CONFIGURED=1
else
    EMAIL_CONFIGURED=0
    # Si no se configura SMTP, dejar las claves vacias para que no quede CHANGE_ME
    EMAIL_PORT=587
    EMAIL_USE_TLS=True
    EMAIL_USE_SSL=False
    EMAIL_HOST_USER=
    EMAIL_HOST_PASSWORD=
    DEFAULT_FROM_EMAIL=
fi

# --- Superusuario --------------------------------------------------------

# Delimiter COMILLADO: sin las comillas los backticks del cuerpo se ejecutan
# como sustitucion de comandos. Con `<<EOF` crudo, `createsuperuser --noinput`
# se intentaba correr como comando del shell y el dry-run imprimia
# "linea 1462: createsuperuser: command not found" en el medio de la seccion de
# superusuario. Los otros dos backticks del archivo (location / y ssh-keyscan)
# ya van escapados con `\`; este delimiter era el que faltaba.
cat >&2 <<'EOF'

  Superusuario de Django. La cuenta se crea con `createsuperuser --noinput` por
  systemd-run, con la ENCRYPTION_KEY cargada y PRODUCTION=1.
EOF
SUPERUSER_USERNAME=$(ask SUPERUSER_USERNAME admin 'Usuario del superusuario' valid_app_user) || die 'Usuario invalido.'
# El default NO puede ser `$EMAIL_HOST_USER` a secas: con el usuario SMTP por
# defecto ahora siendo `meteocamaguey` (un login, no un correo), `valid_email`
# lo rechazaba y el operador que solo hacia Enter con la cabeza en otra parte
# perdia tres intentos y el instalador moria. Se usa el correo solo cuando el
# usuario SMTP SI es una direccion completa; si es un login corto, default vacio.
if valid_email "$EMAIL_HOST_USER"; then
    SUPERUSER_EMAIL_DEFAULT=$EMAIL_HOST_USER
else
    SUPERUSER_EMAIL_DEFAULT=
fi
SUPERUSER_EMAIL=$(ask SUPERUSER_EMAIL "$SUPERUSER_EMAIL_DEFAULT" 'Correo del superusuario' valid_email) || die 'Correo invalido.'
# El aviso depende de SI EL OPERADOR DEJO EL CAMPO VACIO, no de si el entorno
# venia vacio. Vacio => `ask_secret` genera y lo anota en ASK_GENERATED_FILE;
# tecleada => el archivo no se toca. Preguntar eso es lo unico que puede decir con
# certeza si la contrasena es nueva y hay que mostrarla o si el operador ya la
# tiene y repetirla solo la mete en el scrollback y en cualquier grabacion de
# sesion. Antes el flag se sacaba del entorno, asi que teclear una contrasena
# tambien la imprimia: un secreto que el operador eligio, impreso sin que se lo
# pidieran.
SUPERUSER_PASSWORD=$(ask_secret SUPERUSER_PASSWORD "${SUPERUSER_PASSWORD:-}" 'Contrasena del superusuario') || die 'Contrasena invalida.'
# Mismo criterio que DB_PASS: en CI no hay nadie a quien avisarle.
# Pero aca hay una diferencia que costo una contrasena: DB_PASS queda en el .env
# y se puede recuperar; esta NO se escribe en ningun archivo. Con la guarda
# `INTERACTIVE -eq 1`, un `--non-interactive` sin SUPERUSER_PASSWORD generaba la
# contrasena, la usaba para crear la cuenta y la tiraba: el resumen decia
# "Superusuario admin creado" y no habia forma de entrar. Generada es generada,
# haya TTY o no.
if secret_was_generated SUPERUSER_PASSWORD && [ "$DRY_RUN" -eq 0 ]; then
    print_once_secret 'SUPERUSER_PASSWORD' "$SUPERUSER_PASSWORD" recover_env_password
fi

# --- Otros campos del .env ----------------------------------------------

USE_REDIS_CACHE=False
if ask_yes_no si 'Cache con Redis? (USE_REDIS_CACHE)'; then USE_REDIS_CACHE=True; fi
REDIS_URL=$(ask REDIS_URL redis://127.0.0.1:6379/1 'URL de Redis') || die 'URL de Redis invalida.'
LOG_LEVEL=$(ask LOG_LEVEL INFO 'Nivel de log' valid_log_level) || die 'Nivel de log invalido.'
FTP_OBS=no
if ask_yes_no no 'El deployment lee observaciones por FTP?'; then
    FTP_OBS=si
    FTP_OBS_HOST=$(ask FTP_OBS_HOST "" 'Host FTP de observaciones' valid_host) || die 'Host FTP invalido.'
    FTP_OBS_USER=$(ask FTP_OBS_USER "" 'Usuario FTP' valid_app_user) || die 'Usuario FTP invalido.'
    FTP_OBS_PASS=$(ask_optional_secret FTP_OBS_PASS "" 'Contrasena FTP (vacio = FTP sin contrasena)') || die 'Contrasena FTP invalida.'
    FTP_OBS_PORT=$(ask FTP_OBS_PORT 990 'Puerto FTP' valid_port) || die 'Puerto FTP invalido.'
else
    FTP_OBS_HOST= FTP_OBS_USER= FTP_OBS_PASS= FTP_OBS_PORT=990
fi
LDAP_ENABLED=no
if ask_yes_no no 'Autenticacion LDAP?'; then
    LDAP_ENABLED=si
    LDAP_SERVER_URI=$(ask LDAP_SERVER_URI ldap://ldap.ejemplo.cu:389 'LDAP_SERVER_URI') || die 'URI invalida.'
    LDAP_BIND_DN=$(ask LDAP_BIND_DN "" 'LDAP_BIND_DN (cn=admin,dc=ejemplo,dc=cu)') || die 'Bind DN invalido.'
    LDAP_BIND_PASSWORD=$(ask_optional_secret LDAP_BIND_PASSWORD "" 'LDAP_BIND_PASSWORD (vacio = sin contrasena)') || die 'Contrasena LDAP invalida.'
    LDAP_USER_SEARCH_BASE=$(ask LDAP_USER_SEARCH_BASE ou=people,dc=ejemplo,dc=cu 'LDAP_USER_SEARCH_BASE') || die 'Base de busqueda invalida.'
fi

# ==========================================================================
# FASE C. Ejecucion. De aca en adelante SI se toca el sistema.
# ==========================================================================

step 1 "Resumen de lo que se va a instalar"
cat <<EOF
  checkout        $APP_DIR ($REPO_URL @ $REPO_REF)
  usuario         $SERVICE_USER:$SERVICE_GROUP (shell $SERVICE_SHELL)
  dominio         $EXTERNAL_HOSTNAME${WWW_HOSTNAME:+, $WWW_HOSTNAME}
  proxy           $PROXY_MODE (TLS: $TLS_MODE)
  base            $DB_ENGINE db=$DB_NAME user=$DB_USER host=${DB_HOST:-local}:$DB_PORT
  correo          ${EMAIL_HOST:-SIN CONFIGURAR}
  superusuario    $SUPERUSER_USERNAME
  deploy.sh       $SCRIPT_PATH
  deploy.env      $CONFIG_FILE
  health check    $([ "$PROXY_MODE" = nginx-local ] \
      && echo "https://$EXTERNAL_HOSTNAME/ en 127.0.0.1:$HTTPS_PORT con SNI (Nginx con TLS)" \
      || echo "http://$EXTERNAL_HOSTNAME/ en 127.0.0.1:$HTTP_PORT (Nginx local, TLS en el proxy)")
EOF
if [ "$DRY_RUN" -eq 1 ]; then
    echo
    echo "dry-run: se imprime el plan y no se ejecuta nada."
fi

# --- C.1 Prerrequisitos de sistema --------------------------------------

step 8 "Prerrequisitos de sistema"

# La lista no es la del README del proyecto: esa no alcanza. La tabla de
# deploy/README-deploy.md documenta por que cada uno hace falta (build-essential
# para los pins sin ruedas en Python 3.14, libpango1.0-dev para WeasyPrint,
# libcairo2-dev para pycairo). Se replica aca porque este instalador tiene que
# ser autocontenido: no puede leer el README antes de clonar el repo.
APT_PACKAGES=(
    git
    sudo
    ca-certificates
    # curl NO es opcional ni decorativo: `webcmp-deploy` lo usa en el health
    # check de CADA deploy, y sin el el deploy muere al final con un `000` que
    # no dice "falta curl". Antes solo se avisaba en el preflight y se asumia
    # que la imagen lo traia -- en un contenedor minimo no lo trae, y el
    # instalador terminaba en verde con un deploy roto.
    curl
    openssl
    python3
    python3-venv
    python3-dev
    build-essential
    pkg-config
    libcairo2-dev
    libpango1.0-dev
    redis-server
)
# Nginx se instala en LOS DOS modos. La diferencia no es "con Nginx o sin
# Nginx", es quien termina TLS:
#   nginx-local -> Nginx hace TLS y hace de proxy.
#   external    -> el TLS lo termina el proxy de afuera (Nginx Proxy Manager en
#                  otra maquina), pero Nginx local SIGUE siendo necesario: es el
#                  unico que puede servir /media/ y /static/ desde este
#                  filesystem. Django no sirve media con DEBUG=False y WhiteNoise
#                  solo sirve staticfiles, asi que sin este Nginx el logo y el
#                  favicon de todas las paginas dan 404 junto con cada PDF
#                  subido, y ningun health check lo detecta.
APT_PACKAGES+=(nginx)
# psycopg[binary] viene con su binario, asi que no hacen falta build-essential
# ni headers de PostgreSQL para compilar el driver.

if [ "$DRY_RUN" -eq 1 ]; then
    printf '  [dry-run] apt-get install -y %s\n' "${APT_PACKAGES[*]}"
else
    export DEBIAN_FRONTEND=noninteractive
    # `apt-get update` y el install se muestran en vivo: son el primer comando
    # que el operador ve correr y el que mas tiempo tarda sin decir nada. Un
    # instalador que se queda mudo 90 segundos en el paso 1 parece colgado.
    # Sin `-qq`: ese flag es lo que hace que apt no diga ni "Leyendo listas de
    # paquetes..." ni "Configurando <paquete>". Con el, el log en vivo llega
    # practicamente vacio y el operador no ve nada pasar dentro del spinner.
    run_live 'apt-get update' apt-get update \
        || die "apt-get update fallo."
    # Se instala lo que falte: `apt-get install` sobre un paquete ya presente no
    # hace nada, asi que no hace falta un ciclo de deteccion por paquete.
    run_live 'apt-get install (paquetes del sistema)' \
        apt-get install -y "${APT_PACKAGES[@]}" \
        || die "apt-get install fallo. Revisa el repositorio de la distro y volve a correr."
fi

# El servidor de base de datos NO se instala sin permiso. Instalar PostgreSQL es
# una decision de infraestructura (backup, locale, puerto, version) que el
# instalador no tiene derecho a tomar por su cuenta, y un `apt-get install
# postgresql` de sorpresa puede abrir el 5432 al mundo segun la configuracion de
# la distro.
DB_SERVER_PACKAGE=postgresql
if ! command -v psql >/dev/null 2>&1; then
    warn "No encontre el cliente de PostgreSQL (psql) en el servidor."
    if [ "$DRY_RUN" -eq 1 ]; then
        # En --dry-run no hay a quien preguntar: `ask_yes_no` devuelve el
        # default, que aca es `no`, y la rama del `else` mataba el dry-run con
        # "Sin postgresql no se puede crear el rol y la base". Un dry-run que
        # aborta en un contenedor limpio no sirve para nada, y el contenedor
        # limpio es exactamente donde se corre. Se imprime el plan de la rama
        # que el operador va a elegir en la corrida real.
        printf '  [dry-run] apt-get install -y %s (si se responde "si" en la corrida real)\n' \
            "$DB_SERVER_PACKAGE"
    elif ask_yes_no no "Instalar $DB_SERVER_PACKAGE con apt?"; then
        if [ "$DRY_RUN" -eq 1 ]; then
            printf '  [dry-run] apt-get install -y %s\n' "$DB_SERVER_PACKAGE"
        else
            # Con run_live, no con un apt-get pelado. Este es el comando mas
            # LENTO de todo el instalador: instalar el server de PostgreSQL son
            # 2-5 minutos en un VPS con SSD, y con -qq el operador se queda
            # mirando una pantalla muerta sin poder distinguir "todavia baja" de
            # "se trabo". El log deja ver si esta resolviendo dependencias o si
            # esta esperando algo de red.
            run_live "apt-get install $DB_SERVER_PACKAGE" \
                apt-get install -y "$DB_SERVER_PACKAGE" \
                || die "No se pudo instalar $DB_SERVER_PACKAGE. Instalalo a mano y volve a correr."
        fi
    else
        die "Sin $DB_SERVER_PACKAGE no se puede crear el rol y la base. El instalador no puede seguir sin saber si el servidor de base ya existe."
    fi
fi

# --- C.2 Usuario de servicio y directorios -------------------------------

step 1 "Usuario de servicio $SERVICE_USER"

if id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "  ya existe (shell: $(getent passwd "$SERVICE_USER" | cut -d: -f7))"
else
    # --system: es una cuenta de servicio. Shell nologin, porque existe para
    # correr Gunicorn y Huey, no para que alguien se loguee con el. El usuario
    # de despliegue (abajo) es el que tiene shell, y son dos cosas distintas.
    # El home se pone en APP_DIR para que los paths absolutos de los units
    # coincidan con el dueno y ProtectHome=true no corte el checkout.
    run useradd --system --home-dir "$APP_DIR" --shell "$SERVICE_SHELL" \
        --no-create-home "$SERVICE_USER"
    echo "  creado"
fi

# Solo el directorio raiz aca. Los de runtime (media/, logs/, staticfiles/,
# .cache/ y MPLCONFIGDIR) se crean DESPUES del checkout: `git clone` se niega a
# clonar sobre un destino que no este vacio, y con estos directorios adentro el
# clone de una instalacion nueva fallaba con "destination path already exists
# and is not an empty directory". Ademas, si el operador acepta mover el
# directorio a un .preinstall, esos directorios se van con el backup y tenian
# que volver a crearse en este mismo arranque, no en el proximo.
#
# MEDIA_ROOT, LOG_FILE, STATIC_ROOT y MPLCONFIGDIR viven adentro de APP_DIR y los
# escribe el usuario de servicio. Con el directorio equivocado, collectstatic
# escribe en / y el servicio no puede escribir sus propios logs: dos fallos
# distintos que se ven igual.
ensure_dir "$APP_DIR" 0755 "$SERVICE_USER" "$SERVICE_GROUP"
# Cache de pip. Sin esto, cada deploy vuelve a descargar todas las dependencias
# desde PyPI: `webcmp` es cuenta de sistema sin home, asi que pip no encuentra
# donde escribir ~/.cache. Ver la nota de PIP_CACHE_DIR en deploy.env.example.
ensure_dir "$PIP_CACHE_DIR" 0700 "$SERVICE_USER" "$SERVICE_USER"
ensure_dir "$CONFIG_DIR" 0755 root root
ensure_dir "$TLS_DIR" 0700 root root

# --- C.3 Clone -----------------------------------------------------------

step 6 "Checkout en $APP_DIR"

if [ "$HAS_CHECKOUT" -eq 1 ]; then
    # Clone existente: se actualiza, NO se regenera. Un `rm -rf $APP_DIR` para
    # "empezar limpio" se llevaria por delante media/, .env y el venv, que es
    # justamente lo que este script existe para no perder.
    echo "  ya existe; se actualiza a $REPO_REF (idempotente)."
    CURRENT_ORIGIN=$(runuser -u "$SERVICE_USER" -- git -C "$APP_DIR" remote get-url origin 2>/dev/null || echo '')
    if [ -n "$CURRENT_ORIGIN" ] && [ "$CURRENT_ORIGIN" != "$REPO_URL" ]; then
        warn "El origen actual es $CURRENT_ORIGIN y se pidio $REPO_URL."
        if ask_yes_no no "Cambiar el remoto origin a $REPO_URL?"; then
            run_sh "runuser -u '$SERVICE_USER' -- git -C '$APP_DIR' remote set-url origin '$REPO_URL'"
        else
            warn "Se deja el origen como estaba."
        fi
    fi
    # `git` corre como SERVICE_USER y no como root, por la misma razon que en
    # deploy.sh: un repo escribible por otro usuario con un .git/config capaz de
    # ejecutar comandos es un vector de escalada de privilegios, y git >= 2.35.2
    # se niega a trabajar sobre el con "dubious ownership".
    run_sh "runuser -u '$SERVICE_USER' -- git -C '$APP_DIR' fetch --quiet origin"
    run_sh "runuser -u '$SERVICE_USER' -- git -C '$APP_DIR' checkout --quiet '$REPO_REF'"
    run_sh "runuser -u '$SERVICE_USER' -- git -C '$APP_DIR' merge --ff-only --quiet 'origin/$REPO_REF' \
        || echo '  [aviso] no se pudo hacer fast-forward a origin/$REPO_REF; se deja el checkout como esta'"
elif [ "$APP_DIR_PRESENT" -eq 1 ] && [ "$APP_DIR_EMPTY" -eq 0 ]; then
    # El caso que antes moría con "destination path already exists": hay cosas
    # adentro y no son un checkout. Casi siempre es media/, .env y .venv de una
    # corrida anterior que se cortó a mitad, O un /srv que ya tenia algo del
    # operador.
    #
    # NO se borra nada por cuenta propia. Un rm -rf aca es exactamente el
    # escenario que este instalador existe para no perder: el .env con las claves
    # de la base, los PDFs ya subidos y el venv con dos minutos de compilacion.
    # Se ASK, y el default es no.
    warn "$APP_DIR tiene contenido pero no es un checkout de git."
    echo "  Primeros elementos:"
    find "$APP_DIR" -mindepth 1 -maxdepth 1 -printf '    %f\n' 2>/dev/null | head -20
    if ! ask_yes_no no "Mover $APP_DIR a $APP_DIR.preinstall-<fecha> y clonar de cero?"; then
        # Sin default de "si" porque borrar datos del operador no puede ser la
        # respuesta que se obtiene solo apretando Enter.
        die "$APP_DIR tiene contenido que no es un checkout.
       El instalador no lo borra solo. Revisa que sea, movelo a mano, o
       responde 'si' la proxima vez para que lo guarde como $APP_DIR.preinstall-<fecha>."
    fi
    BACKUP_DIR="$APP_DIR.preinstall-$(date +%Y%m%d-%H%M%S)"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] mv %s %s\n' "$APP_DIR" "$BACKUP_DIR"
    else
        run mv "$APP_DIR" "$BACKUP_DIR" \
            || die "No se pudo mover $APP_DIR a $BACKUP_DIR. Revisa permisos de /srv."
        echo "  contenido anterior conservado en $BACKUP_DIR (borralo cuando confirmes)."
    fi
    # ensure_dir vuelve a crear $APP_DIR vacio, y de ahi si entra el clone.
    if [ "$DRY_RUN" -eq 0 ]; then
        ensure_dir "$APP_DIR" 0755 "$SERVICE_USER" "$SERVICE_GROUP"
    fi
fi

# El clone vive en un bloque PROPIO, condicionado a "todavia no hay checkout",
# y no en un `else` del bloque de arriba: los tres caminos sin checkout (no
# existe, existe vacio, o existia con contenido y se acaba de mover) tienen que
# terminar aca. Antes el clone estaba SOLO en la rama "existe con contenido y no
# es un repo", asi que una instalacion nueva no clonaba nada y moria en la
# verificacion de mas abajo con "Falta .../deploy/deploy.sh: el clon esta
# incompleto" sin haber intentado un solo `git clone`.
if [ "$HAS_CHECKOUT" -eq 0 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] runuser -u %s -- git clone %s %s\n' "$SERVICE_USER" "$REPO_URL" "$APP_DIR"
    else
        # En vivo y sin `--quiet`: es el primer comando que depende de Internet y
        # el que mas se traba por firewall o DNS. Con el log el operador ve si
        # esta bajando o si se quedo esperando, que es justo lo que hay que
        # distinguir antes de decidir si toca abrir puertos.
        run_live "git clone $REPO_URL" \
            runuser -u "$SERVICE_USER" -- git clone --branch "$REPO_REF" "$REPO_URL" "$APP_DIR" \
            || die "No se pudo clonar $REPO_URL en $APP_DIR.

       Si el error fue 'destination path already exists and is not an empty
       directory', eso ya se trata arriba; si se escapo de ahi, casi seguro hay
       procesos con cwd en $APP_DIR o un montaje encima. Revisa con:
         ls -A $APP_DIR ; findmnt -T $APP_DIR ; lsof +D $APP_DIR
       Para red: salida a Internet, DNS, y que el proxy no este cortando
       github.com. Comprobacion rapida: curl -sI https://github.com"
    fi
fi

for f in deploy/deploy.sh deploy/sudoers/webcmp-deploy deploy/nginx/webcmp.conf.example \
         deploy/systemd/webcmp.service deploy/systemd/webcmp-huey.service \
         scripts/generate_env.py requirements/prod.txt
do
    [ -f "$APP_DIR/$f" ] && continue
    # En un dry-run sobre un servidor limpio el clone NO se hizo, asi que no hay
    # nada que verificar todavia. Morir aqui dejaria al --dry-run sin poder
    #correrse nunca en el servidor donde mas lo necesita uno: antes de clonar.
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] todavia no hay checkout en %s; %s se usara del repo al clonar.\n' \
            "$APP_DIR" "$f" >&2
        continue
    fi
    die "Falta $APP_DIR/$f: el clon esta incompleto."
done
HAVE_CHECKOUT=0
[ -d "$APP_DIR/.git" ] && HAVE_CHECKOUT=1
if [ "$HAVE_CHECKOUT" -eq 1 ]; then
    echo "  checkout listo."
else
    echo "  dry-run: los renders que necesitan el checkout se omiten."
fi

# Directorios de runtime, YA con el checkout adentro (ver por que no van antes
# del clone en el comentario de ensure_dir mas arriba). Ademas: si el operador
# acepto mover $APP_DIR a un .preinstall, estos cuatro se fueron con el backup y
# hay que volver a crearlos en ESTE arranque. Sin esto, la corrida que mueve el
# directorio termina sin media/ ni logs/ y el sitio no puede guardar archivos
# ni escribir sus logs hasta la proxima corrida del instalador.
ensure_dir "$APP_DIR/media" 0750 "$SERVICE_USER" "$SERVICE_GROUP"
ensure_dir "$APP_DIR/logs" 0750 "$SERVICE_USER" "$SERVICE_GROUP"
ensure_dir "$APP_DIR/staticfiles" 0755 "$SERVICE_USER" "$SERVICE_GROUP"
ensure_dir "$APP_DIR/.cache" 0750 "$SERVICE_USER" "$SERVICE_GROUP"
ensure_dir "$MPLCONFIGDIR" 0750 "$SERVICE_USER" "$SERVICE_GROUP"

# need_checkout <que se haria con el>
#
# Los renders (units, vhost, sudoers) leen del checkout archivos que este
# instalador tiene que self-contained: en un dry-run sobre un servidor limpio
# todavia no estan. La alternativa era dejar el render para adelante y fallar
# con un "awk: cannot open /srv/webcmp/deploy/..." que no dice que el problema es
# que --dry-run no clona.
need_checkout() {
    # Con checkout: hay con que trabajar, el consumidor sigue de largo.
    [ "$HAVE_CHECKOUT" -eq 1 ] && return 0
    # Sin checkout: se avisa Y se devuelve error, para que el consumidor se
    # SALTE el bloque. Antes devolvia 0 en las dos ramas, con lo cual los
    # cuatro `|| need_checkout` de mas abajo nunca cortaban: los bloques ejecutaban
    # renders desde deploy/systemd, deploy/nginx y deploy/sudoers, que en un
    # dry-run sin checkout no existen.
    printf '  [dry-run] sin checkout: %s\n' "$1" >&2
    return 1
}

# --- C.4 Base de datos ---------------------------------------------------

step 2 "Base de datos $DB_ENGINE: rol y base"

# El password viaja por el STDIN de psql, no por `-v` en la linea de comandos: un
# `-v dbpass=...` queda visible en `ps aux` mientras el comando corre, y basta
# otro usuario con permiso de ver la tabla de procesos para leer la contrasena.
#
# `\set` + `:'dbuser'` / `:"dbuser"` hacen el escapado por nosotros. Interpolar el
# valor a mano dentro de un DO $$ ... $$ se rompe con cualquier password que
# tenga un `$`, y se INYECTA con una que tenga una comilla simple: por eso
# psql_quote() y no concatenacion de strings.
#
# Idempotencia: el rol se crea solo si no existe, la base idem, y la contrasena
# se ALTERA al valor preguntado. Nunca hay DROP: tirar la base de un sitio en
# produccion es el peor error que puede cometer un instalador, y ninguna
# idempotencia lo justifica.
#
# TEMPLATE template0 es OBLIGATORIO, no un detalle. Sin TEMPLATE, CREATE DATABASE
# usa template1, que hereda el encoding del CLUSTER. Si el initdb corrio con
# locale C o POSIX (tipico en Debian/Ubuntu en un servidor sin locale UTF-8
# configurado), el cluster es SQL_ASCII y el CREATE revienta con:
#
#   ERROR: new encoding (UTF8) is incompatible with the encoding of the
#          template database (SQL_ASCII)
#   HINT:  Use the same encoding as in the template database, or use
#          template0 as template.
#
# template0 existe justamente para esto: es la plantilla neutra que permite
# crear una base con un encoding distinto al del cluster. template1 es la
# plantilla "con el locale ya puesto", por eso no se puede cambiarle el encoding.
#
# Pero template0 solo arregla el ENCODING, no el locale: la base nueva hereda
# LC_CTYPE del cluster, y si el initdb corrio con locale C, el resultado es
# UTF8 con LC_CTYPE='C'. Con eso PostgreSQL no pliega mayusculas de caracteres
# acentuados, y en la practica el sitio queda asi:
#
#   lower('ÁÉÍÓÚ')                     -> 'ÁÉÍÓÚ'   (sin cambiar)
#   ILIKE '%CAMAGÜEY%' sobre 'camagüey' -> 0 coincidencias
#   ORDER BY                           -> 'México, Ultima, Zulia, camagüey,
#                                          Ávila'   (orden de bytes, no espanol)
#
# O sea: un `icontains` de Django sobre cualquier nombre con tilde no encuentra
# NADA. Por eso LC_CTYPE/LC_COLLATE se pasan explicitamente.
#
# El locale se toma SIEMPRE de `locale -a`, nunca se inventa: PostgreSQL valida
# el nombre contra el sistema dentro del CREATE y uno que no exista aborta la
# instalacion entera con "invalid LC_COLLATE locale name". Preferencia: es_CU
# (Camagüey) > es_ES > C.utf8. Si ninguno existe se omite la clausula y la base
# queda UTF8 con LC_CTYPE=C: degradada, pero instalada. Ningun locale disponible
# es preferible a una instalacion que revienta.
DB_LOCALE=""
DB_HAS_LOCALE=false
for _cand in es_CU.utf8 es_CU.UTF-8 es_ES.utf8 es_ES.UTF-8 C.utf8 C.UTF-8; do
    # -F porque el candidato es literal (el punto de "C.utf8" no es regex),
    # -x para matchear la linea completa, -i porque locale -a puede reportar
    # "es_cu.utf8" en minusculas. Se guarda la linea TAL CUAL la reporta el
    # sistema: es la forma que setlocale() va a aceptar.
    #
    # `|| true` es OBLIGATORIO, no es redundante. Un candidato que NO existe
    # hace que grep salga con 1; con `set -o pipefail` eso llega a la
    # asignacion, la asignacion sale con 1, y `set -e` mata el script AQUI,
    # en silencio, antes de correr psql. El sintoma en una corrida real fue
    # exactamente ese: el step se imprimia, despues volvia al prompt sin un
    # solo mensaje de error. La decision se toma por CONTENIDO (-n), nunca
    # por el exit status, para que funcione con o sin pipefail.
    _found=$(locale -a 2>/dev/null | grep -Fxi "$_cand" | head -1 || true)
    if [ -n "$_found" ]; then
        DB_LOCALE=$_found
        DB_HAS_LOCALE=true
        break
    fi
done
unset _cand _found

if [ "$DRY_RUN" -eq 1 ]; then
    printf '  [dry-run] runuser -u postgres -- psql (crea rol %s y base %s, template0, UTF8, locale %s)\n' \
        "$DB_USER" "$DB_NAME" "${DB_LOCALE:-<el del cluster>}"
else
    runuser -u postgres -- psql --quiet --set=ON_ERROR_STOP=1 <<SQL
\\set dbuser $(psql_quote "$DB_USER")
\\set dbpass $(psql_quote "$DB_PASS")
\\set dbname $(psql_quote "$DB_NAME")
\\set haslocale $DB_HAS_LOCALE
\\set lc $(psql_quote "$DB_LOCALE")
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'dbuser', :'dbpass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'dbuser')
\\gexec
ALTER ROLE :"dbuser" WITH LOGIN PASSWORD :'dbpass';
\\if :haslocale
SELECT format('CREATE DATABASE %I OWNER %I ENCODING %L TEMPLATE template0 LC_CTYPE %L LC_COLLATE %L',
              :'dbname', :'dbuser', 'UTF8', :'lc', :'lc')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname')
\\else
SELECT format('CREATE DATABASE %I OWNER %I ENCODING %L TEMPLATE template0',
              :'dbname', :'dbuser', 'UTF8')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname')
\\endif
\\gexec
\\echo 'rol y base listos'
SQL
fi
echo "  rol y base listos (nunca se borra una base existente)."

# La verificacion va DESPUES y aparte, porque el `WHERE NOT EXISTS` de arriba
# salta en silencio si la base ya existe. Si en una corrida anterior se creo en
# SQL_ASCII, o si alguien la creo a mano, la creacion no falla, no avisa nada, y
# el error recien aparece en Django al guardar el primer texto con un acento.
# Chequear aca es la diferencia entre "instalacion exitosa que despues revienta"
# y un aviso en el paso donde paso.
if [ "$DRY_RUN" -eq 0 ]; then
    DB_ENCODING=$(runuser -u postgres -- psql -tA -d "$DB_NAME" -c \
        'SHOW server_encoding' 2>/dev/null || echo '')
    case "$DB_ENCODING" in
        UTF8)
            DB_CTYPE=$(runuser -u postgres -- psql -tA -d "$DB_NAME" -c \
                'SHOW lc_ctype' 2>/dev/null || echo '')
            case "$DB_CTYPE" in
                C|POSIX)
                    warn "La base $DB_NAME es UTF8 pero con LC_CTYPE='$DB_CTYPE'."
                    echo "  Con ese locale no se pliegan mayusculas de acentos: un"
                    echo "  'icontains' de Django sobre 'Camagüey' o 'meteorológico'"
                    echo "  no va a encontrar nada. Locale disponible en este"
                    echo "  sistema: ${DB_LOCALE:-ninguno UTF-8}."
                    echo "  PostgreSQL no permite cambiar lc_ctype de una base ya"
                    echo "  creada, para eso hay que recrearla (y esta NO se toca"
                    echo "  sola nunca). Dejalo anotado si el busca no funciona."
                    ;;
                '')
                    echo "  encoding de $DB_NAME: UTF8 (locale no verificable)."
                    ;;
                *)
                    echo "  encoding de $DB_NAME: UTF8, locale $DB_CTYPE (ok)."
                    ;;
            esac
            ;;
        *)
            warn "La base $DB_NAME quedo en encoding '${DB_ENCODING:-desconocido}', no UTF8."
            echo "  Django guarda texto con acentos y con ese encoding va a fallar al"
            echo "  guardar. Para recrearla (CONSERVANDO TODO LO DEMAS):"
            echo "    runuser -u postgres -- dropdb --force $DB_NAME"
            echo "  y volver a correr este instalador; template0 la creara en UTF8."
            echo "  Si preferis no tocarla, al menos revisa que no haya datos previos."
            ;;
    esac
fi

# --- C.5 venv ------------------------------------------------------------

step 2 "Entorno virtual"

if [ -x "$VENV_DIR/bin/python" ]; then
    echo "  ya existe en $VENV_DIR (idempotente)."
else
    run_sh "$PYTHON_BIN -m venv '$VENV_DIR'"
fi
# Asegurar permisos del venv (evita EACCES cuando pip install corre como $SERVICE_USER)
if [ "$DRY_RUN" -eq 0 ] && [ -d "$VENV_DIR" ]; then
    chown -R "$SERVICE_USER:${SERVICE_GROUP:-$SERVICE_USER}" "$VENV_DIR" || warn "No se pudo cambiar propietario de $VENV_DIR; pip install puede fallar"
fi

# requirements/prod.txt trae cryptography, que es lo unico que necesita
# scripts/generate_env.py. Por eso el venv va ANTES de generar el .env: el
# generador importa Fernet y no Django, asi que el venv es la unica forma de
# tener esa dependencia sin tocar el Python del sistema.
step 25 "Instalando requirements/prod.txt"
# `-m pip` y NO el shim `$VENV_DIR/bin/pip`: ese shim no esta garantizado (un
# venv creado con uv no lo trae) y, si falta, la instalacion de requirements
# falla con "No such file or directory". Mismo criterio que el PIP_CMD de
# deploy.sh.
# El pip install es el comando mas largo de toda la instalacion, y con
# `--quiet` no imprimia NADA: eran minutos de pantalla muerta con la barra
# clavada en 71%. Se saca `--quiet` justamente para eso, y el spinner muestra el
# reloj mientras corre. El log en vivo tambien deja ver que paquete esta yendo,
# que es la forma de distinguir "compilando cryptography" de "colgado".
if [ "$DRY_RUN" -eq 1 ]; then
    printf '  [dry-run] runuser -u %s -- env PIP_CACHE_DIR=%s %s -m pip install -r %s\n' \
        "$SERVICE_USER" "$PIP_CACHE_DIR" "$VENV_DIR/bin/python" "$APP_DIR/requirements/prod.txt"
else
    run_live 'pip install -r requirements/prod.txt' \
        runuser -u "$SERVICE_USER" -- env "PIP_CACHE_DIR=$PIP_CACHE_DIR" \
        "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check \
        -r "$APP_DIR/requirements/prod.txt" \
        || die 'El pip install fallo. Revisa la red del servidor y volve a correr.'
fi

# --- C.6 .env ------------------------------------------------------------

step 1 "Archivo .env"

# El generador es NO interactivo por diseño (su docstring lo dice: "un
# generador que adivina el entorno es un generador que un día escribe el .env
# equivocado"). Por eso NO se le pregunta nada: se lo corre y despues se
# sustituyen los CHANGE_ME con lo que el operador respondio. Toca hacerlo asi y
# no al reves porque el generador tiene invariants propios -- conserva el par de
# claves si descifra, escribe ENCRYPTION_KEY en /etc/webcmp/encryption.env y no
# en el .env, y aborta ANTES de tocar el .env si ese archivo no se puede escribir
# -- que no se pueden replicar con un `cat > .env`.
#
# --force solo se pasa con confirmacion: sin el, el generador se niega a pisar un
# .env afinado a mano, y el instalador respeta ese comportamiento en vez de
# quedar por encima.
ENV_GEN_ARGS=(--production --env-file "$ENV_FILE" --encryption-key-file "$ENCRYPTION_ENV")
ENV_REGENERATED=0
if [ "$ENV_FILE_EXISTS" -eq 1 ]; then
    # ¿El par SECRET_KEY/ENCRYPTION_KEY de esta instalación descifra?
    #
    # Si el .env es de una instalación anterior y el encryption.env es de otra,
    # Django no arranca: load_secret_key() de config/settings/base.py lanza
    # ImproperlyConfigured al primer import. Regenerar el par es la única salida,
    # pero SOLO cuando el par está roto de verdad: regenerarlo de más invalida
    # todas las sesiones y cookies firmadas del sitio.
    #
    # El código de salida distingue tres casos, y tratarlos igual es el bug que
    # tenía este bloque (todo caía en el `except Exception` con código 2, y el
    # `if` de abajo solo reaccionaba al 1):
    #   0  el par descifra            -> se conserva
    #   1  el par NO descifra, o falta una de las dos mitades -> se regenera
    #   2  no se pudo verificar (sin cryptography, archivo ilegible, error
    #      inesperado) -> NO se regenera a ciegas: se avisa y se conserva
    #
    # Corre con el Python del VENV, no con $PYTHON_BIN: `cryptography` la instala
    # requirements/prod.txt en el venv y el Python del sistema puede no tenerla.
    # Con el intérprete equivocado el import falla, el resultado sería 2 y la
    # validación no verificaría nada en ningún servidor.
    #
    # Las rutas van por entorno y el cuerpo por heredoc COMILLADO: el bloque
    # anterior interpolaba "$ENV_FILE" dentro de un `-c "..."`, así que un path
    # con comillas o un `$` en el .env se interpretaban como código bash.
    # >>> TEST-BLOCK: env-pair-validator
    if [ "$DRY_RUN" -eq 0 ] && [ -x "$VENV_DIR/bin/python" ]; then
        rc=0
        ENV_FILE="$ENV_FILE" ENCRYPTION_ENV="$ENCRYPTION_ENV" \
            "$VENV_DIR/bin/python" - <<'PY' || rc=$?
import os
import sys

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    # Sin la librería no hay forma de verificar; NO es un par roto.
    sys.exit(2)


def read_value(path, key):
    """Valor de `key=` en un archivo KEY=VALUE, o None si la clave no está.

    FileNotFoundError y OSError se dejan subir: el llamador distingue "falta
    una mitad" (se regenera) de "no se pudo leer" (no se toca nada).
    """
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line.startswith(f'{key}='):
                return line.split('=', 1)[1].strip()
    return None


try:
    encryption_key = read_value(os.environ['ENCRYPTION_ENV'], 'ENCRYPTION_KEY')
    secret_key = read_value(os.environ['ENV_FILE'], 'SECRET_KEY')
except FileNotFoundError:
    sys.exit(1)
except OSError:
    sys.exit(2)

if not encryption_key or not secret_key:
    sys.exit(1)

try:
    Fernet(encryption_key.encode()).decrypt(secret_key.encode())
except (InvalidToken, ValueError, TypeError):
    sys.exit(1)
except Exception:
    sys.exit(2)
sys.exit(0)
PY
        case "$rc" in
            0) : ;;
            1)
                warn "Par ENCRYPTION_KEY/SECRET_KEY inválido en $ENV_FILE: no descifra."
                warn "Con ese par Django no arranca: config/settings/base.py lo rechaza al importar."
                if can_prompt; then
                    # Regenerar invalida todas las sesiones y cookies firmadas y
                    # reescribe el .env desde la plantilla. Ya no es
                    # irreversible (abajo se respalda como .bak.<fecha>), pero
                    # sigue siendo una decision: la otra salida es recuperar la
                    # mitad que falta. Por eso se pregunta.
                    ask_yes_no si "Regenerar el par? (respalda .env y encryption.env como .bak.<fecha>, genera SECRET_KEY y ENCRYPTION_KEY nuevos e invalida las sesiones)" \
                        || die "No se tocó nada. El par roto impide que Django arranque, así que el instalador no puede seguir con el .env como está: restaurá la mitad que falta (por ejemplo, el encryption.env de la instalación a la que pertenece ese .env) y volvé a correrlo, o aceptá la regeneración (queda un .bak de los dos archivos)."
                    ENV_REGENERATED=1
                else
                    # Sin TTY no hay a quien preguntar. Se regenera avisando, y
                    # el respaldo de abajo deja la salida manual abierta.
                    warn "Sin terminal: se regenera el par con --force (las sesiones quedan invalidadas; queda un .bak de los dos archivos)."
                    ENV_REGENERATED=1
                fi
                ;;
            *)
                warn "No se pudo verificar el par ENCRYPTION_KEY/SECRET_KEY de $ENV_FILE (código $rc)."
                warn "Se conserva el archivo: regenerarlo sin estar seguro invalida todas las sesiones."
                ;;
        esac
    fi
    # <<< TEST-BLOCK: env-pair-validator
    # El validador no decidio nada: el par descifra (rc=0), no se pudo verificar
    # (rc=2), o el bloque no corrio (sin venv o en --dry-run). En una re-corrida
    # el .env puede estar afinado a mano, asi que se pregunta antes de
    # reescribirlo, y el default es conservar.
    if [ "$ENV_REGENERATED" -eq 1 ]; then
        : # el validador ya decidio: par roto y regeneracion aceptada
    elif ask_yes_no no "Reescribir $ENV_FILE desde la plantilla? (respalda el actual como .bak.<fecha> y genera SECRET_KEY y ENCRYPTION_KEY nuevos)"; then
        ENV_REGENERATED=1
    else
        ENV_REGENERATED=0
        echo "  se conserva el .env existente."
    fi
else
    ENV_REGENERATED=1
fi

if [ "$ENV_REGENERATED" -eq 1 ]; then
    ENV_GEN_ARGS+=(--force)
    # >>> TEST-BLOCK: env-backup
    # Respaldo ANTES de borrar. `--force` reescribe el .env COMPLETO desde la
    # plantilla (scripts/generate_env.py:build_env), asi que se lleva cualquier
    # valor afinado a mano que el instalador no administra (FTP_OBS_*, LDAP_*,
    # un DB_HOST distinto). El par roto justifica regenerar las claves, no
    # perder el resto; y si el generador falla despues del `rm`, el .bak es lo
    # unico que queda de la configuracion anterior.
    env_backup_stamp=$(date +%Y%m%d-%H%M%S)
    for env_backup_file in "$ENV_FILE" "$ENCRYPTION_ENV"; do
        if [ -f "$env_backup_file" ]; then
            if [ "$DRY_RUN" -eq 1 ]; then
                printf '  [dry-run] cp -p %s %s.bak.%s\n' \
                    "$env_backup_file" "$env_backup_file" "$env_backup_stamp"
            else
                cp -p "$env_backup_file" "$env_backup_file.bak.$env_backup_stamp" \
                    || die "No se pudo respaldar $env_backup_file: sin respaldo no se borra nada."
            fi
        fi
    done
    # Si el par es invalido o forzamos regeneracion, partimos de cero para que
    # `--force` genere claves nuevas de verdad: con los archivos en su lugar,
    # generate_env.py conserva el par que ya descifra y la promesa del prompt
    # ("SECRET_KEY y ENCRYPTION_KEY nuevos") seria falsa.
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] rm -f %s %s\n' "$ENV_FILE" "$ENCRYPTION_ENV"
    else
        rm -f "$ENV_FILE" "$ENCRYPTION_ENV"
    fi
    # <<< TEST-BLOCK: env-backup
    # Se corre COMO ROOT y no como webcmp: el generador tiene que escribir
    # $ENCRYPTION_ENV, que es root:root 600. Corrido como webcmp aborta sin
    # escribir nada (falla cerrado, que esta bien) y el servidor queda igual.
    run_sh "'$VENV_DIR/bin/python' '$APP_DIR/scripts/generate_env.py' $(printf '%q ' "${ENV_GEN_ARGS[@]}")"
    # Y despues hay que devolverle el .env a webcmp: Django corre como ese
    # usuario y tiene que poder leerlo. Con root:root 600 el sitio levanta y
    # falla al primer acceso con un 500 que no dice "no podi leer el .env".
    if [ "$DRY_RUN" -eq 0 ]; then
        chown "$SERVICE_USER:$SERVICE_GROUP" "$ENV_FILE" || true
        chmod 0600 "$ENV_FILE" || true
    else
        run chown "$SERVICE_USER:$SERVICE_GROUP" "$ENV_FILE"
        run chmod 0600 "$ENV_FILE"
    fi
fi

if [ "$ENV_REGENERATED" -eq 0 ]; then
    echo "  se aplica lo respondido sobre el .env existente."
fi

# Sustitucion de los valores preguntados. env_set reemplaza la linea KEY= o la
# agrega, asi que funciona igual con un .env recien generado (todo commented-out
# o con CHANGE_ME) y con uno afinado a mano (donde hay valores que hay que
# RESPETAR y no pisar a ciegas).
# Las claves secretas no pueden pasar por `run`: su rama de dry-run imprime el
# comando completo, y ese dry-run se pega en tickets, issues y chats. El `mask`
# de la linea siguiente llega tarde, porque el valor ya salio impreso arriba.
# Con este runner, el dry-run muestra `<oculto>` y el valor real no toca la
# salida. Aplica a DB_PASS, EMAIL_HOST_PASSWORD y FTP_OBS_PASS.
run_env_set() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] env_set %s %s %s\n' "$ENV_FILE" "$1" "$(mask "$1" "$2")"
    else
        local st=0
        env_set "$ENV_FILE" "$1" "$2" || st=$?
        [ "$st" -eq 0 ] && CHANGED=1
        return "$st"
    fi
}

apply_env_value() {
    run_env_set "$1" "$2"
    printf '  %s=%s\n' "$1" "$(mask "$1" "$2")"
}

step 1 "Sustituyendo los valores del .env"
apply_env_value EXTERNAL_HOSTNAME "$EXTERNAL_HOSTNAME"
# SITE_URL arma los enlaces absolutos de los correos (facturas, avisos, reportes)
# y production.py aborta si falta. El generador la escribe con su hostname por
# defecto, asi que si el operador elige otro dominio esa linea queda apuntando a
# meteocamaguey.cu mientras ALLOWED_HOSTS ya apunta al nuevo: los correos salen
# con un enlace a un sitio que no es el que atiende. Se escribe con la respuesta,
# igual que las otras tres listas de abajo.
apply_env_value SITE_URL "https://$EXTERNAL_HOSTNAME/"
if [ -n "$WWW_HOSTNAME" ]; then
    ALLOWED_HOSTS="$EXTERNAL_HOSTNAME,$WWW_HOSTNAME"
    ORIGINS="https://$EXTERNAL_HOSTNAME,https://$WWW_HOSTNAME"
else
    ALLOWED_HOSTS="$EXTERNAL_HOSTNAME"
    ORIGINS="https://$EXTERNAL_HOSTNAME"
fi
# EXTERNAL_HOSTNAME ya mete el dominio en ALLOWED_HOSTS y en
# CSRF_TRUSTED_ORIGINS por apply_external_hostname() de production.py. Repetirlo
# en las tres listas no esta de mas: ALLOWED_HOSTS lo lee el middleware de host
# con la lista ya resuelta, y si el dominio no esta en el archivo (por ejemplo
# porque EXTERNAL_HOSTNAME se escribio despues del import) la respuesta es
# DisallowedHost en cada peticion.
apply_env_value ALLOWED_HOSTS "$ALLOWED_HOSTS"
apply_env_value CSRF_TRUSTED_ORIGINS "$ORIGINS"
apply_env_value CORS_ALLOWED_ORIGINS "$ORIGINS"

apply_env_value DB_ENGINE "$DB_ENGINE"
apply_env_value DB_NAME "$DB_NAME"
apply_env_value DB_USER "$DB_USER"
apply_env_value DB_PASS "$DB_PASS"
apply_env_value DB_HOST "$DB_HOST"
apply_env_value DB_PORT "$DB_PORT"
apply_env_value DB_SSL_MODE "$DB_SSL_MODE"

if [ "$EMAIL_CONFIGURED" -eq 1 ]; then
    apply_env_value EMAIL_HOST "$EMAIL_HOST"
    apply_env_value EMAIL_PORT "$EMAIL_PORT"
    apply_env_value EMAIL_USE_TLS "$EMAIL_USE_TLS"
    apply_env_value EMAIL_USE_SSL "$EMAIL_USE_SSL"
    apply_env_value EMAIL_HOST_USER "$EMAIL_HOST_USER"
    apply_env_value EMAIL_HOST_PASSWORD "$EMAIL_HOST_PASSWORD"
    # El fallback a `$EMAIL_HOST_USER` solo vale si ese valor ES un correo. Con
    # un login corto (que es el default: `meteocamaguey`) el remitente caeria en
    # `meteocamaguey`, que no es un addr-spec valido y Django lo rechaza al
    # sanitizar la cabecera From. En ese caso se deja vacio y se avisa: es mejor
    # un remitente vacio, visible, que uno mal formado que revienta en cada envio.
    #
    # El default de la pregunta (T26) ya trae nombre y direccion, asi que este
    # fallback casi no se alcanza: queda para cuando el operador borra el valor a
    # mano y deja la pregunta vacia.
    if [ -n "$DEFAULT_FROM_EMAIL" ]; then
        apply_env_value DEFAULT_FROM_EMAIL "$DEFAULT_FROM_EMAIL"
    elif valid_email "$EMAIL_HOST_USER"; then
        apply_env_value DEFAULT_FROM_EMAIL "$EMAIL_HOST_USER"
    else
        apply_env_value DEFAULT_FROM_EMAIL ""
        warn "DEFAULT_FROM_EMAIL quedo vacio: el usuario SMTP ($EMAIL_HOST_USER) es"
        warn "un login, no una direccion. Pon 'Remitente (nombre <correo>)' si queres"
        warn "que los correos salgan con un remitente valido."
    fi
else
    # EMAIL_BACKEND se deja como lo escribio el generador
    # (config.custom_email_backend.CustomSTARTTLSBackend, que no es silencioso
    # y por eso production.py lo acepta), pero con EMAIL_HOST vacio el envio
    # falla. Se avisa y no se inventa nada.
    warn "Correo sin configurar: EMAIL_HOST quedo vacio, asi que los avisos y"
    warn "reportes que mandan correo van a fallar. Los CHANGE_ME de esa seccion"
    warn "del .env siguen ahi a proposito."
fi

apply_env_value USE_REDIS_CACHE "$USE_REDIS_CACHE"
apply_env_value REDIS_URL "$REDIS_URL"
apply_env_value LOG_LEVEL "$LOG_LEVEL"
apply_env_value FTP_OBS_HOST "$FTP_OBS_HOST"
apply_env_value FTP_OBS_USER "$FTP_OBS_USER"
apply_env_value FTP_OBS_PASS "$FTP_OBS_PASS"
apply_env_value FTP_OBS_PORT "$FTP_OBS_PORT"

if [ "$LDAP_ENABLED" = si ]; then
    # El generador deja el bloque LDAP comentado como plantilla. env_set no
    # pisa lineas comentadas (busca `^KEY=`), asi que estas claves se agregan
    # al final del archivo: es lo correcto, no un descuido, y la plantilla
    # comentada de arriba queda como recordatorio de las que faltan.
    apply_env_value LDAP_SERVER_URI "$LDAP_SERVER_URI"
    apply_env_value LDAP_BIND_DN "$LDAP_BIND_DN"
    apply_env_value LDAP_BIND_PASSWORD "$LDAP_BIND_PASSWORD"
    apply_env_value LDAP_USER_SEARCH_BASE "$LDAP_USER_SEARCH_BASE"
fi

# Gate del .env. Separar CHANGE_ME criticos (bloquean migraciones/arranque) de
# opcionales (SMTP/FTP). SMTP incompleto NO debe impedir hacer migraciones.
PENDING_CHANGES=$(grep -n '^[[:space:]]*[^#[:space:]].*CHANGE_ME' "$ENV_FILE" 2>/dev/null || true)
CRITICAL_CHANGES=$(printf '%s\n' "$PENDING_CHANGES" | grep -v -E 'EMAIL_HOST|EMAIL_PORT|EMAIL_HOST_USER|EMAIL_HOST_PASSWORD|DEFAULT_FROM_EMAIL|FTP_OBS_' || true)
NONCRIT_CHANGES=$(printf '%s\n' "$PENDING_CHANGES" | grep -E 'EMAIL_HOST|EMAIL_PORT|EMAIL_HOST_USER|EMAIL_HOST_PASSWORD|DEFAULT_FROM_EMAIL|FTP_OBS_' || true)
HAS_BLOCKING_CRITICAL=0
if [ -n "$CRITICAL_CHANGES" ]; then
    HAS_BLOCKING_CRITICAL=1
    if [ "$ALLOW_INCOMPLETE_ENV" -eq 1 ]; then
        warn "Quedan CHANGE_ME CRITICOS en $ENV_FILE (--allow-incomplete-env):"
        printf '%s\n' "$CRITICAL_CHANGES" | sed 's/^/    /' >&2
        warn "El sitio puede NO arrancar con esta configuracion."
    else
        printf '%s\n' "$CRITICAL_CHANGES" | sed 's/^/    /' >&2
        die "Quedan CHANGE_ME CRITICOS en $ENV_FILE. Se paro ANTES de migrar y de arrancar el sitio.

Si de verdad todavia no hay valores criticos completos, se puede continuar con:

    sudo bash install.sh --allow-incomplete-env

pero el sitio puede quedar SIN LEVANTAR hasta que se completen esos valores."
    fi
fi
if [ -n "$NONCRIT_CHANGES" ]; then
    warn "CHANGE_ME NO CRITICOS detectados (SMTP/FTP): el envio de correos/FTP"
    warn "NO funcionara hasta completarlos, pero migraciones/estaticos SI pueden"
    warn "ejecutarse. Lines:"
    printf '%s\n' "$NONCRIT_CHANGES" | sed 's/^/      /' >&2
fi
echo "  .env revisado."

# --- C.7 Migraciones, estaticos, gate ------------------------------------

step 8 "Migraciones y estaticos"

if [ "$HAS_BLOCKING_CRITICAL" -eq 1 ]; then
    # Con CHANGE_ME criticos NO se migra. Las migraciones abren conexion a la base
    # y un CHANGE_ME en DB_PASS las hace fallar con un error de autenticacion que
    # no dice "falta completar el .env".
    warn "No se corre makemigrations ni migrate: el .env tiene CHANGE_ME CRITICOS."
    warn "collectstatic y check --deploy si se corren: no tocan la base y sus"
    warn "resultados sirven igual para diagnosticar."
    warn "La migracion queda pendiente: hay que volver a correr este instalador"
    warn "cuando esten completos los valores CRITICOS."
else
    # LIVE_MANAGE=1 hace que manage() muestre el log en vivo (ver manage()).
    #
    # Los cinco `run manage ...` llevan `|| die`. No es ceremonia: `run` es un
    # wrapper comun y corriente, y SIN el `|| die` el fallo salia del script
    # por `set -e` en el punto exacto del comando, sin ERROR, sin decir cual
    # de los cinco era y volviendo al prompt del operador. Con el `|| die`,
    # ademas, `set -e` se suspende durante toda la cadena (run -> manage ->
    # run_live), que es lo que permite que run_live capture el exit code en
    # vez de morir a mitad del pipe.
    LIVE_MANAGE=1
    run manage makemigrations \
        || die "makemigrations fallo. El log de arriba dice por que. Sin el, migrate no se corre."
    LIVE_MANAGE=0
    run manage migrate --noinput \
        || die "migrate --noinput fallo. El log de arriba dice por que; la base queda sin aplicar."
    run manage migrate --check \
        || die "migrate --check fallo: quedaron migraciones sin aplicar en la base."
    LIVE_MANAGE=0
fi
# collectstatic tambien va en vivo: con DEBUG=False no imprime una linea hasta
# terminar, y en un proyecto con estaticos vendoreados son varios segundos en
# los que no se ve nada pasar.
LIVE_MANAGE=1
run manage collectstatic --no-input \
    || die "collectstatic fallo. El log de arriba dice por que; sin estaticos el sitio sirve CSS/JS 404."
LIVE_MANAGE=0
# El mismo gate que corre deploy.sh en cada deploy, corrido una vez con la
# configuracion real. security.W008 no aparece porque production.py lo silencia
# en SILENCED_SYSTEM_CHECKS: el TLS y el redirect los termina el proxy, no
# Django.
LIVE_MANAGE=1
run manage check --deploy --fail-level WARNING \
    || die "check --deploy fallo con WARNINGs. El listado de arriba dice cual."
LIVE_MANAGE=0

if [ -n "$PENDING_CHANGES" ]; then
    log "Superusuario omitido"
    warn "No se crea el superusuario con el .env incompleto: createsuperuser"
    warn "importa los settings y habria que hacerlo en el perfil equivocado."
else
    # El `.env` va al directorio del proyecto porque Django lo lee en
    # BASE_DIR/'.env' y en ningun otro lado. Passarlo en otro sitio no lo hace
    # aparecer.
    log "Superusuario $SUPERUSER_USERNAME"
    # OJO: systemd-run NO hereda el entorno de quien llama. Un
    # `DJANGO_SUPERUSER_PASSWORD=... systemd-run ... createsuperuser --noinput`
    # crea la cuenta con una contrasena inservible y sin avisar, porque la
    # variable se queda en la shell de root. Para pasar algo, va con
    # -p Environment=NOMBRE=valor. Es el bug documentado en deploy.sh:200-207 y
    # el que hace que este paso se escriba asi y no como un `export` de la
    # config de install.sh.
    #
    # Segunda corrida: `createsuperuser --noinput` con el usuario ya creado
    # aborta con "That username is already taken", la contrasena que se acaba de
    # imprimir no se aplica, y el warning mandaba a repetir justo el comando que
    # falla. Se consulta primero y, si la cuenta existe, se le pone la
    # contrasena respondida (la impresa) y se aseguran los flags. Sin eso, una
    # re-instalacion deja un panel inaccesible con una clave recien generada en
    # la mano.
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] systemd-run ... createsuperuser --noinput --username %s\n' "$SUPERUSER_USERNAME"
        printf '  [dry-run] si la cuenta existe: se le resetea la contrasena y se asegura is_superuser\n'
    elif superuser_exists; then
        if reset_superuser_password; then
            SUPERUSER_CREATED=1
            echo "  superusuario $SUPERUSER_USERNAME ya existia: contrasena actualizada."
        else
            warn "El superusuario $SUPERUSER_USERNAME existe y no se pudo actualizar la"
            warn "contrasena. Cambiala a mano en el mismo contexto que corre el sitio:"
            print_changepassword_hint
        fi
    elif systemd-run --quiet --pipe --wait \
        --uid="$SERVICE_USER" --gid="$SERVICE_USER" \
        -p "EnvironmentFile=$ENCRYPTION_ENV" \
        -p Environment=PRODUCTION=1 \
        -p "Environment=MPLCONFIGDIR=$MPLCONFIGDIR" \
        -p "Environment=DJANGO_SUPERUSER_PASSWORD=$SUPERUSER_PASSWORD" \
        --working-directory="$APP_DIR" \
        "$VENV_DIR/bin/python" manage.py createsuperuser --noinput \
        --username "$SUPERUSER_USERNAME" --email "$SUPERUSER_EMAIL"
    then
        SUPERUSER_CREATED=1
        echo "  superusuario listo."
    else
        warn "No se pudo crear el superusuario. Se deja el resto instalado."
        warn "Crear a mano (mismo contexto que el sitio):"
        print_changepassword_hint
    fi
fi

# --- C.8 Unidades systemd ------------------------------------------------

step 4 "Unidades systemd"

# RENDER, no copia. Las rutas de deploy/systemd/*.service estan escritas en el
# archivo a proposito (en un unit, `APP_DIR=/srv/webcmp` no es una clave valida
# de systemd y `systemd-analyze verify` falla con "Unknown key"), asi que un
# checkout en otra ruta necesita reescribir el archivo, no interpolarlo. El
# render es literal, no con regex: `webcmp` como patron matchearia tambien
# `webcmp-huey`, que es el nombre del otro unit.
render_unit() {
    local src=$1 dest=$2 tmp
    tmp=$(mktemp)
    render_template "$src" "$tmp" \
        "/srv/webcmp<>$APP_DIR" \
        "/etc/webcmp/encryption.env<>$ENCRYPTION_ENV" \
        "webcmp-huey<>$HUEY_UNIT" \
        "User=webcmp<>User=$SERVICE_USER" \
        "Group=webcmp<>Group=$SERVICE_GROUP" \
        "RuntimeDirectory=webcmp<>RuntimeDirectory=$GUNICORN_UNIT" \
        "/run/webcmp<>/run/$GUNICORN_UNIT" \
        || { rm -f "$tmp"; return 1; }
    install_if_changed "$tmp" "$dest" 0644
    rm -f "$tmp"
}

if need_checkout 'se renderizaran las dos unidades systemd desde deploy/systemd/'; then
    render_unit "$APP_DIR/deploy/systemd/webcmp.service" "/etc/systemd/system/$GUNICORN_UNIT.service"
    render_unit "$APP_DIR/deploy/systemd/webcmp-huey.service" "/etc/systemd/system/$HUEY_UNIT.service"
fi

run systemctl daemon-reload

# www-data tiene que poder tocar el socket de Gunicorn (0770 webcmp:webcmp). Sin
# esto Nginx devuelve 502 en cada respuesta y el log dice "connect() failed (13:
# Permission denied)", que parece un problema de permisos que no lo es.
# Aplica a los DOS modos: en `external` el Nginx local sigue haciendo el proxy al
# socket, aunque el TLS lo termine el proxy de afuera.
if id -nG www-data 2>/dev/null | tr ' ' '\n' | grep -qx "$SERVICE_GROUP"; then
    echo "  www-data ya esta en el grupo $SERVICE_GROUP"
else
    run usermod -aG "$SERVICE_GROUP" www-data
    log "Reiniciando Nginx para que tome el grupo nuevo"
    run systemctl restart nginx
    echo "  www-data ahora puede tocar el socket"
fi

# --- C.9 Reverse proxy ---------------------------------------------------

HEALTHCHECK_INSECURE_VALUE=
TLS_CERT=
TLS_KEY=

# Pagina de error ESTATICA que sirve Nginx para 502/503/504. La del ejemplo
# apunta a `root /var/www/webcmp-errors` con `internal;`: si el archivo no esta,
# un Gunicorn caido produce un 404 DENTRO del manejo del error, que es el
# sintoma mas imposible de diagnosticar que existe ("la pagina de error fallo").
write_error_page() {
    local tmp
    tmp=$(mktemp)
    cat > "$tmp" <<'EOF'
<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <title>Servicio temporalmente no disponible</title>
</head>
<body>
    <h1>Servicio temporalmente no disponible</h1>
    <p>El servicio del portal esta reiniciando o no responde en este momento.</p>
    <p>Reintentese en unos minutos.</p>
</body>
</html>
EOF
    install_if_changed "$tmp" "$NGINX_ERROR_ROOT/50x.html" 0644 root root
    rm -f "$tmp"
}

# El vhost del ejemplo, renderizado con los valores de esta instalacion.
#
# Se parte de deploy/nginx/webcmp.conf.example y no de un heredoc propio por una
# razon concreta: el ejemplo es lo que revisa la gente cuando el sitio devuelve
# 502, asi que si el vhost activo fuera OTRO archivo, cada diagnostico empezaria
# leyendo el equivocado. El render es literal (render_template), nunca regex.
render_vhost() {
    local src=$vhost_src dest=$1 tmp
    local -a pairs
    pairs=(
        # Rutas del checkout y del socket. El orden importa: /srv/webcmp va antes
        # que /srv/webcmp/staticfiles, si no el segundo par nunca se encuentra.
        "/srv/webcmp/staticfiles<>$APP_DIR/staticfiles"
        "/srv/webcmp/media<>$APP_DIR/media"
        "/srv/webcmp<>$APP_DIR"
        "/run/webcmp/gunicorn.sock<>$GUNICORN_SOCKET"
        "/var/www/webcmp-errors<>$NGINX_ERROR_ROOT"
        "/etc/nginx/certificate/www.meteocamaguey.cu.crt<>$TLS_CERT"
        "/etc/nginx/certificate/www.meteocamaguey.cu.key<>$TLS_KEY"
        "server_name www.meteocamaguey.cu;<>$SERVER_NAME_LINE"
        "listen 80 default_server;<>listen $HTTP_PORT default_server;"
        "listen [::]:80 default_server;<>listen [::]:$HTTP_PORT default_server;"
        "listen 443 ssl default_server;<>listen $HTTPS_PORT ssl default_server;"
        "listen [::]:443 ssl default_server;<>listen [::]:$HTTPS_PORT ssl default_server;"
    )
    tmp=$(mktemp)
    render_template "$src" "$tmp" "${pairs[@]}" || { rm -f "$tmp"; return 1; }
    # La redireccion de :80 a :https se mete dentro de `location /`, con la
    # excepcion de /.well-known/acme-challenge/ antes. Es lo unico que hace que
    # `certbot --webroot` funcione con este vhost: un `return 301` a nivel de
    # server se evalua ANTES de elegir location, asi que cualquier challenge de
    # ACME caeria en un redirect a https y la emision fallaria con un error que
    # no menciona el vhost.
    #
    # El `server { server_name _; }` del ejemplo es justamente esa caja: el
    # challenge se sirve por ahi, sin importar el dominio que se esta emitiendo.
    ACME_ROOT=$ACME_ROOT awk '
        /return 301 https:\/\/\$host\$request_uri;/ {
            print "        location ^~ /.well-known/acme-challenge/ {"
            print "            root " ENVIRON["ACME_ROOT"] ";"
            print "        }"
            print ""
            print "        location / {"
            sub(/^[ \t]+/, "", $0)
            print "            " $0
            print "        }"
            next
        }
        { print }
    ' "$tmp" > "$dest" || { rm -f "$tmp" "$dest"; return 1; }
    rm -f "$tmp"
}

# ngx_http_acme-challenge.conf de Debian. Su existence es lo que permite el
# `location ^~` que se acaba de agregar sin declarar `default_type`.
ensure_acme_root() {
    ensure_dir "$ACME_ROOT" 0755 www-data www-data
}

# gen_self_signed <certificado> <clave>
#
# OpenSSL, sin red: funciona con DNS privado, con puertos cerrados y en un
# servidor que todavia no tiene salida a Internet. El precio es el que ya se le
# dijo al operador: el navegador lo marca como no seguro.
gen_self_signed() {
    local cert=$1 key=$2 san="DNS:$EXTERNAL_HOSTNAME"
    [ -n "$WWW_HOSTNAME" ] && san="$san,DNS:$WWW_HOSTNAME"
    run openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -subj "/CN=$EXTERNAL_HOSTNAME" -addext "subjectAltName=$san" \
        -keyout "$key" -out "$cert" \
        || die "No se pudo generar el certificado autofirmado en $cert."
    if [ "$DRY_RUN" -eq 0 ]; then
        chown root:root "$key" "$cert" 2>/dev/null || true
        chmod 0600 "$key" 2>/dev/null || true
        chmod 0644 "$cert" 2>/dev/null || true
    fi
}

# install_vhost [certificado] [clave]
#
# Renderiza el ejemplo, lo instala, lo habilita, verifica con `nginx -t` y
# recarga. Se llama MAS DE UNA VEZ en la primera corrida con Let's Encrypt: el
# challenge de ACME lo sirve el puerto 80 de este mismo vhost, asi que el vhost
# tiene que estar de pie antes de certbot; y un vhost con `ssl` no pasa `nginx -t`
# sin un certificado que cargar.
#
# Sin argumentos se usa el vhost SIN TLS (modo `external`): no hay certificado
# que cargar ni rutas que sustituir, porque webcmp-http.conf.example no declara
# `ssl_certificate`.
install_vhost() {
    TLS_CERT=${1:-}
    TLS_KEY=${2:-}
    local tmp

    # --- Archivo y activacion ---
    if [ -f "$vhost_src" ] || need_checkout 'el vhost se renderizaria desde deploy/nginx/webcmp.conf.example'; then
        tmp=$(mktemp)
        render_vhost "$tmp" || { rm -f "$tmp"; die "No se pudo renderizar el vhost."; }
        install_if_changed "$tmp" "$NGINX_AVAILABLE" 0644 root root
        rm -f "$tmp"
    fi

    # Nginx NO acepta dos vhost del mismo nombre en sites-enabled: el segundo se
    # ignora con un aviso que no se lee. Por eso el enlace se hace con ln -sf y no
    # con un ln a secas, que falla si ya existe (idempotencia).
    if [ -L "$NGINX_ENABLED" ]; then
        if [ "$(readlink "$NGINX_ENABLED")" != "$NGINX_AVAILABLE" ]; then
            run ln -sf "$NGINX_AVAILABLE" "$NGINX_ENABLED"
        else
            echo "  $NGINX_ENABLED ya apunta a $NGINX_AVAILABLE."
        fi
    elif [ -e "$NGINX_ENABLED" ]; then
        die "$NGINX_ENABLED existe y NO es un symlink. Decidir a mano si se reemplaza
  por un enlace a $NGINX_AVAILABLE; el instalador no borra un archivo regular."
    else
        run ln -s "$NGINX_AVAILABLE" "$NGINX_ENABLED"
    fi

    # --- Cache de proxy ---
    # El vhost declara `proxy_cache_path /cache/nginx/tmpfs ...` y Nginx NO crea
    # ese path recursivamente: ngx_create_paths() hace mkdir() solo del ultimo
    # componente, asi que con /cache/nginx ausente `nginx -t` muere con
    #     mkdir() "/cache/nginx/tmpfs" failed (2: No such file or directory)
    # apuntando a un error que parece de Nginx y en realidad es del vhost: nadie
    # crea ese directorio. El camino se lee del vhost que se va a servir (no
    # esta escrito a mano aca) para que un cambio de path en el ejemplo no lo
    # deje desincronizado. Los padres quedan root:root y el leaf con dueno
    # www-data, porque los workers (user www-data) son los que escriben el cache.
    local cache_dir="" cache_src=""
    if [ -f "$NGINX_AVAILABLE" ]; then
        cache_src=$NGINX_AVAILABLE
    elif [ -f "$vhost_src" ]; then
        cache_src=$vhost_src
    fi
    if [ -n "$cache_src" ]; then
        cache_dir=$(awk '$1 == "proxy_cache_path" { print $2; exit }' "$cache_src")
    fi
    if [ -n "$cache_dir" ]; then
        ensure_dir "$(dirname "$cache_dir")" 0755 root root
        ensure_dir "$cache_dir" 0750 www-data www-data
    fi

    # nginx -t ANTES del reload, siempre. Un reload de una configuracion rota
    # deja al Nginx viejo sirviendo y el error queda en el log para cuando ya se
    # fue; con `nginx -t` primero, el instalador muere aca con el mensaje de Nginx.
    log "Verificando la configuracion de Nginx"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] nginx -t && systemctl reload nginx\n'
    else
        nginx -t || die "La configuracion de Nginx no pasa nginx -t. No se recarga nada."
        systemctl reload nginx || die "No se pudo recargar Nginx."
        echo "  vhost activo: $NGINX_AVAILABLE"
    fi
}

setup_nginx_local() {
    SERVER_NAME_LINE="server_name $EXTERNAL_HOSTNAME;"
    if [ -n "$WWW_HOSTNAME" ]; then
        SERVER_NAME_LINE="server_name $EXTERNAL_HOSTNAME $WWW_HOSTNAME;"
    fi

    ensure_dir "$NGINX_ERROR_ROOT" 0755 root root
    write_error_page

    # --- default_server en conflicto ---
    # Va ANTES del certificado porque el conflicto lo destapa `nginx -t`, y el
    # primer `nginx -t` ya no es el ultimo: con Let's Encrypt hay una pasada
    # bootstrap antes de certbot.
    #
    # El vhost del ejemplo pide `listen 80 default_server` y `listen 443 ssl
    # default_server`. El sitio default de Debian tambien los pide, y dos
    # default_server en el mismo puerto hacen que `nginx -t` falle con
    # "a duplicate default server". No se borra a ciegas: puede estar sirviendo
    # otro sitio, asi que se pregunta y, si no, se dice que hay que resolverlo.
    # El sitio default de Debian es un symlink a sites-available/default, asi que
    # hace falta `-L` tambien: con un enlace roto, `-e` da falso y el conflicto
    # aparece despues como un fallo de nginx -t que no lo señala.
    #
    # OJO con el destino del rename: renombrarlo DENTRO de sites-enabled no
    # deshabilita nada. El include del nginx.conf de Ubuntu/Debian es
    # `include /etc/nginx/sites-enabled/*` y ese glob matchea cualquier nombre,
    # `.disabled` incluido (los unicos que excluye son los ocultos). Por eso el
    # rename anterior seguia dejando el sitio cargado y `nginx -t` seguia
    # muriendo con "a duplicate default server for 0.0.0.0:80" citando el vhost
    # de webcmp, que no era el culpable. La salida es sacarlo del directorio que
    # se incluye y guardarlo en sites-available, donde estan los que no estan
    # habilitados.
    _mv_default_site_out() {
        # sites-enabled/X -> sites-available/X.disabled (con sufijo de fecha si
        # ya quedo algo ahi de una corrida anterior, para no pisarlo).
        local src=$1 dest
        dest="${src/sites-enabled/sites-available}"
        case "$dest" in
            *.disabled) ;;
            *) dest="$dest.disabled" ;;
        esac
        if [ -e "$dest" ] || [ -L "$dest" ]; then
            dest="$dest.$(date +%Y%m%d-%H%M%S)"
        fi
        run mv "$src" "$dest" || die "No se pudo mover $src a $dest."
        echo "  deshabilitado: $src -> $dest"
    }

    # Autoreparacion sin preguntar: un `.disabled` que quedo DENTRO de
    # sites-enabled es un estado invalido que dejo este mismo script en una
    # corrida anterior con el rename viejo. El operador ya decidio deshabilitar
    # ese sitio, no hay decision nueva que tomar, y si no se arregla aca
    # `nginx -t` vuelve a fallar igual que antes.
    if [ -e "$NGINX_DEFAULT_SITE.disabled" ] || [ -L "$NGINX_DEFAULT_SITE.disabled" ]; then
        warn "$NGINX_DEFAULT_SITE.disabled quedo dentro de sites-enabled y el include"
        warn "'sites-enabled/*' lo sigue cargando: eso hace fallar nginx -t con"
        warn "'a duplicate default server'. Se saca de ese directorio."
        _mv_default_site_out "$NGINX_DEFAULT_SITE.disabled"
    fi

    if [ -e "$NGINX_DEFAULT_SITE" ] || [ -L "$NGINX_DEFAULT_SITE" ]; then
        log "Nginx ya tiene un sitio por defecto"
        warn "$NGINX_DEFAULT_SITE esta activo y pide los mismos puertos con"
        warn "default_server que el vhost de webcmp. Los dos no pueden levantar."
        if ask_yes_no si "Deshabilitar $NGINX_DEFAULT_SITE (se saca de sites-enabled)?"; then
            _mv_default_site_out "$NGINX_DEFAULT_SITE"
            # Corrida anterior con el rename viejo: el sitio ya estaba "deshabilitado"
            # a medias y quedo el residuo en sites-enabled.
            if [ -e "$NGINX_DEFAULT_SITE.disabled" ] || [ -L "$NGINX_DEFAULT_SITE.disabled" ]; then
                _mv_default_site_out "$NGINX_DEFAULT_SITE.disabled"
            fi
        else
            die "Sin resolver el conflicto de default_server, nginx -t falla y el sitio no levanta.
  Opciones: habilitar este instalador con esa pregunta en 'si', o quitar del
  vhost de webcmp las dos lineas 'default_server'."
        fi
    fi

    # --- Certificado ---
    if [ "$TLS_MODE" = none ]; then
        # TLS externo: este vhost escucha HTTP y nada mas. No hay certificado
        # que emitir ni que cargar, y certbot NO debe correr: el proxy de afuera
        # es el que habla TLS con el navegador y el que renueva su certificado.
        log "Sin TLS en esta maquina: lo termina el proxy externo"
        install_vhost
    elif [ "$TLS_MODE" = self-signed ]; then
        TLS_CERT=$TLS_DIR/$EXTERNAL_HOSTNAME.crt
        TLS_KEY=$TLS_DIR/$EXTERNAL_HOSTNAME.key
        if [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
            echo "  certificado autofirmado ya existe (no se regenera)."
        else
            gen_self_signed "$TLS_CERT" "$TLS_KEY"
        fi
        HEALTHCHECK_INSECURE_VALUE=1
        warn "Certificado AUTOFIRMADO en $TLS_CERT: el navegador lo marca como no"
        warn "seguro en cada visita y el health check de deploy.sh dejara de"
        warn "verificar el TLS (HEALTHCHECK_INSECURE=1)."
        install_vhost "$TLS_CERT" "$TLS_KEY"
    else
        # certbot con --webroot, no --standalone ni --nginx: --standalone exige
        # el puerto 80 libre (hay que parar Nginx), y --nginx reescribe el vhost
        # que este mismo script genera. Con --webroot la renovacion automatica
        # sigue funcionando porque el challenge queda en el archivo y la
        # configuracion de renovacion es de certbot, no del vhost.
        command -v certbot >/dev/null 2>&1 || {
            warn "certbot no esta instalado. Sin el no hay certificado de Let's Encrypt."
            if ask_yes_no si "Instalar certbot con apt?"; then
                if [ "$DRY_RUN" -eq 1 ]; then
                    printf '  [dry-run] apt-get install -y certbot\n'
                else
                    apt-get install -y -qq certbot \
                        || die "No se pudo instalar certbot. Instalalo a mano o elige TLS autofirmado."
                fi
            else
                die "Sin certbot no hay certificado de confianza. Volve a correr y elige 'self-signed'."
            fi
        }
        ensure_acme_root
        if ! getent hosts "$EXTERNAL_HOSTNAME" >/dev/null 2>&1; then
            warn "$EXTERNAL_HOSTNAME no resuelve desde este servidor. certbot va a fallar"
            warn "con 'Could not bind' o con un timeout de validacion: necesita que el"
            warn "DNS publico apunte a esta maquina y que los puertos esten abiertos."
        fi
        TLS_CERT=/etc/letsencrypt/live/$EXTERNAL_HOSTNAME/fullchain.pem
        TLS_KEY=/etc/letsencrypt/live/$EXTERNAL_HOSTNAME/privkey.pem
        log "Certificado de Let's Encrypt para $EXTERNAL_HOSTNAME"
        local -a certbot_args
        certbot_args=(certonly --non-interactive --agree-tos --webroot
            -w "$ACME_ROOT" --cert-name "$EXTERNAL_HOSTNAME")
        if [ -n "$ACME_EMAIL" ]; then
            certbot_args+=(--email "$ACME_EMAIL")
        else
            certbot_args+=(--register-unsafely-without-email)
            warn "Sin correo de registro en ACME: Lets Encrypt avisa que no se puede"
            warn "avisar de la expiracion del certificado."
        fi
        certbot_args+=(-d "$EXTERNAL_HOSTNAME")
        [ -n "$WWW_HOSTNAME" ] && certbot_args+=(-d "$WWW_HOSTNAME")

        if [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
            # Ya hay certificado emitido: esta corrida es renovacion o no-op, y
            # el vhost puede cargar el certificado real de una.
            run certbot "${certbot_args[@]}" \
                || die "certbot fallo. Con --webroot necesita que el puerto $HTTP_PORT responda por $ACME_ROOT desde Internet.
  Diagnostico:  curl -I http://$EXTERNAL_HOSTNAME/.well-known/acme-challenge/  desde otra maquina.
  Alternativa:   volver a correr con TLS autofirmado y cambiar el DNS despues."
            install_vhost "$TLS_CERT" "$TLS_KEY"
        else
            # Primera emision. El challenge HTTP-01 lo responde el puerto 80 de
            # ESTE vhost, que todavia no esta instalado, y `nginx -t` no acepta
            # un `listen ... ssl` sin certificado. Por eso va primero un vhost
            # igual, con un autofirmado temporal: mismo archivo, mismo
            # `location ^~ /.well-known/acme-challenge/`, un certificado que Nginx
            # pueda cargar. El sitio queda sirviendo (con aviso del navegador)
            # durante la emision, en vez de estar caido.
            local boot_cert=$TLS_DIR/bootstrap-$EXTERNAL_HOSTNAME.crt
            local boot_key=$TLS_DIR/bootstrap-$EXTERNAL_HOSTNAME.key
            # install_vhost escribe las globales TLS_CERT/TLS_KEY (render_vhost
            # las lee). Sin estas copias, la llamada de abajo pisaba el par real
            # de Let's Encrypt con el temporal, y el vhost definitivo quedaba
            # apuntando al autofirmado: emision exitosa y certificado invalido.
            local real_cert=$TLS_CERT real_key=$TLS_KEY
            if [ ! -f "$boot_cert" ] || [ ! -f "$boot_key" ]; then
                ensure_dir "$TLS_DIR" 0755 root root
                gen_self_signed "$boot_cert" "$boot_key"
            fi
            log "Vhost temporal con certificado autofirmado, para el challenge de ACME"
            install_vhost "$boot_cert" "$boot_key"
            run certbot "${certbot_args[@]}" \
                || die "certbot fallo. Con --webroot necesita que el puerto $HTTP_PORT responda por $ACME_ROOT desde Internet.
  Diagnostico:  curl -sS -o /dev/null -w '%{http_code}\n' http://$EXTERNAL_HOSTNAME/.well-known/acme-challenge/prueba  desde otra maquina
                (probar con un archivo cualquiera en $ACME_ROOT: el codigo 404 es el esperado)
  Alternativa:   volver a correr con TLS autofirmado y cambiar el DNS despues."
            # Con el certificado real en disco, el mismo render cambia de rutas y
            # `install_if_changed` reinstala. El autofirmado temporal queda en
            # $TLS_DIR, ya sin uso: se puede borrar a mano.
            install_vhost "$real_cert" "$real_key"
        fi
    fi
}

# El Nginx local se instala y configura en LOS DOS modos. En `external` no hace
# TLS, pero sigue siendo el unico que puede servir /media/ y /static/ desde este
# filesystem, y el que evita exponer Gunicorn en la red.
setup_nginx_local

if [ "$PROXY_MODE" = external ]; then
    log "Proxy externo (Nginx Proxy Manager): TLS afuera, Nginx local sirve los archivos"
    # La IP con la que el LXC se hace visible para el NPM. `hostname -I` lista
    # todas las direcciones y la primera es la de la interfaz principal. Si sale
    # vacia no es fatal: se imprime el placeholder y el operador la completa.
    LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    cat <<EOF

  Datos del upstream para Nginx Proxy Manager:

    Domain Names           $EXTERNAL_HOSTNAME${WWW_HOSTNAME:+, $WWW_HOSTNAME}
    Scheme                 http     (NPM -> Nginx local; el TLS lo pone NPM)
    Forward Hostname / IP  ${LAN_IP:-IP_DEL_LXC}
    Forward Port           $HTTP_PORT
    Block Common Exploits  on
    Websockets Support     on       (el grafico de sounding las usa)
    HTTP/2 Support         on
    SSL                    el certificado lo emite NPM (Let's Encrypt). Para el
                           challenge HTTP-01, el dominio tiene que resolver a la
                           IP de NPM y el puerto 80 tiene que llegar ahi.

  Que hace el Nginx de esta maquina (NO es opcional):
    - Sirve /static/ y /media/ desde $APP_DIR. Django no sirve media con
      DEBUG=False y WhiteNoise solo sirve staticfiles: sin este Nginx, el logo,
      el favicon y cada PDF subido dan 404.
    - Hace proxy a Gunicorn por $GUNICORN_SOCKET, que NO se expone en la red.
    - Propaga el X-Forwarded-Proto que manda NPM. Si lo reescribiera con
      \$scheme, Django no veria la peticion como segura y el login entraria en
      un bucle de logout.

    No hace falta configurar Custom Locations. NPM manda X-Forwarded-Proto por
    defecto; si personalizaste su configuracion avanzada, comprueba que ese
    header siga ahi: es el que hace que Django vea la peticion como segura.

    Verificacion despues del deploy, desde el LXC:
      curl -sSI http://127.0.0.1:$HTTP_PORT/ | head -1
      curl -sSI http://127.0.0.1:$HTTP_PORT/static/dist/css/tabler.min.css | head -1
      curl -sSI http://127.0.0.1:$HTTP_PORT/media/<archivo-subido> | head -1

  Firewall (el instalador NO lo toca):
    El puerto $HTTP_PORT queda alcanzable en la red. Limitarlo a la maquina del
    NPM; nunca abrirlo a la LAN entera:
      sudo ufw allow from <IP_DEL_NPM> to any port $HTTP_PORT proto tcp
      sudo ufw deny $HTTP_PORT/tcp

  Aviso sobre HSTS:
    production.py tiene SECURE_HSTS_SECONDS=31536000 con IncludeSubDomains y
    Preload. Si NPM emite HSTS y despues se cae el TLS, el navegador se niega a
    volver por http:// durante un ano. Habilitarlo recien cuando https este
    estable.

EOF
fi

# --- C.10 /etc/webcmp/deploy.env -----------------------------------------

step 1 "Configuracion del deploy en $CONFIG_FILE"

# NO se pisa si existe sin confirmacion. Esta config se edita a mano
# (PUBLIC_HOSTNAME sobre todo) y pisarla en un reintento deja el health check
# pegado al host equivocado: el deploy falla aunque el sitio este perfecto.
#
# Y se escribe SIEMPRE con las claves que el health check necesita, incluso
# cuando el proxy es local y el comportamiento de deploy.sh sin tocar es
# exactamente el correcto. La alternativa (no escribir nada y dejar que el
# default se aplique) hace que la config de un servidor nuevo sea invisible:
# nadie puede ver de donde sale el host del health check.
if [ "$CONFIG_FILE_EXISTS" -eq 1 ]; then
    # Las ramas estaban INVERTIDAS: responder "si" a "Actualizar ..." dejaba
    # CONFIG_WRITTEN=0 y avisaba "se conserva", asi que la unica forma de que el
    # archivo se reescribiera era responder que NO. En una re-corrida donde se
    # cambio PUBLIC_HOSTNAME, el health check seguia apuntando al host viejo y el
    # deploy fallaba con el sitio perfecto.
    if ask_yes_no no "Actualizar $CONFIG_FILE con lo respondido?"; then
        CONFIG_WRITTEN=1
    else
        CONFIG_WRITTEN=0
        warn "$CONFIG_FILE se conserva. Si el health check falla, empeza por ese archivo."
    fi
else
    CONFIG_WRITTEN=1
fi

if [ "$CONFIG_WRITTEN" -eq 1 ]; then
    # Se escribe a un temporal y de ahi se instala, no con `{ ... } > "$CONFIG_FILE"`.
    # Dos razones: `install_if_changed` respeta --dry-run y compara contenido, y una
    # redireccion trivial sobreescribe siempre. Un dry-run que escribe el archivo
    # real es peor que no tener dry-run.
    CONFIG_TMP=$(mktemp)
    {
        cat <<EOF
# $CONFIG_FILE
# Escrito por deploy/install.sh. root:root 600.
# No tiene secretos: por eso puede vivir fuera del .env del sitio.
# Se reescribe solo con confirmacion, y con --configure o --check de
# webcmp-deploy. Ver deploy/README-deploy.md.
APP_DIR=$APP_DIR
DEPLOY_USER=$SERVICE_USER
GUNICORN_UNIT=$GUNICORN_UNIT
HUEY_UNIT=$HUEY_UNIT
ENCRYPTION_ENV=$ENCRYPTION_ENV
PYTHON=$VENV_DIR/bin/python
# NO se escribe PIP aqui, a proposito: deploy.sh lo resuelve solo como
# "\$PYTHON -m pip", leyendo PYTHON de la linea de arriba. Escribir PIP=...
# apuntaria al shim bin/pip, que un venv creado con uv no trae. Si alguna vez
# hace falta otro pip, se escribe a mano en este archivo y deploy.sh lo respeta
# como override de una palabra.
PIP_CACHE_DIR=$PIP_CACHE_DIR
MPLCONFIGDIR=$MPLCONFIGDIR
PUBLIC_HOSTNAME=$EXTERNAL_HOSTNAME
PROXY_MODE=$PROXY_MODE
EOF
        if [ "$PROXY_MODE" = external ]; then
            # El health check atraviesa el Nginx local por HTTP: es el camino
            # real de esta topologia, y detecta un vhost roto o un /media/ sin
            # servir. Antes iba directo al socket de Gunicorn, que dejaba en
            # verde un Nginx mal configurado.
            #
            # NO se define HEALTHCHECK_UNIX_SOCKET: deploy.sh le da prioridad
            # sobre la URL y el --resolve, y volveriamos al camino de antes.
            cat <<EOF
HEALTHCHECK_URL=http://$EXTERNAL_HOSTNAME/
HEALTHCHECK_RESOLVE=$EXTERNAL_HOSTNAME:$HTTP_PORT:127.0.0.1
EOF
        else
            cat <<EOF
# Sin HEALTHCHECK_UNIX_SOCKET: el health check entra a 127.0.0.1:$HTTPS_PORT con
# el SNI de $EXTERNAL_HOSTNAME y atraviesa Nginx, TLS y el socket.
HEALTHCHECK_URL=https://$EXTERNAL_HOSTNAME/
HEALTHCHECK_RESOLVE=$EXTERNAL_HOSTNAME:$HTTPS_PORT:127.0.0.1
EOF
            if [ -n "$HEALTHCHECK_INSECURE_VALUE" ]; then
                cat <<EOF
# El certificado es autofirmado. Con esto el health check deja de verificar el
# TLS: detecta menos, y es lo unico que detecta un certificado vencido.
# NUNCA en produccion real.
HEALTHCHECK_INSECURE=$HEALTHCHECK_INSECURE_VALUE
EOF
            fi
        fi
    } > "$CONFIG_TMP"
    install_if_changed "$CONFIG_TMP" "$CONFIG_FILE" 0600 root root
    rm -f "$CONFIG_TMP"
    echo "  escrito."
fi

# --- C.11 Andamiaje de deploy automatico ---------------------------------

# El andamiaje que el README anterior hacia a mano: usuario de despliegue,
# keypair, sudoers validado y webcmp-deploy en /usr/local/sbin.
setup_deploy_scaffold() {
    local tmp pubkey

    log "Andamiaje de deploy automatico (usuario $DEPLOY_LOGIN_USER)"

    if id "$DEPLOY_LOGIN_USER" >/dev/null 2>&1; then
        echo "  el usuario $DEPLOY_LOGIN_USER ya existe."
    else
        # -m: home propio, porque la clave publica que hay que copiar a GitHub
        # vive ahi y el operador no tiene por que hacer sudo cat de un 600. NO es
        # el usuario de servicio: este tiene shell y sudo acotado, aquel tiene
        # nologin y ninguna capacidad.
        run useradd --create-home --shell /bin/bash "$DEPLOY_LOGIN_USER"
        echo "  creado con shell /bin/bash."
    fi

    # La clave NUNCA se regenera si ya existe: quien la tiene publicada en GitHub
    # como secret dejaria de poder conectarse, y el sintoma (workflow en rojo por
    # "Permission denied (publickey)") no dice "rotaron la clave del servidor".
    if [ -f "$DEPLOY_SSH_KEY" ]; then
        echo "  $DEPLOY_SSH_KEY ya existe (NO se regenera)."
    else
        run ssh-keygen -t ed25519 -N '' -C "$DEPLOY_LOGIN_USER@$(hostname -s)" \
            -f "$DEPLOY_SSH_KEY" \
            || die "No se pudo generar el keypair en $DEPLOY_SSH_KEY."
        DEPLOY_SSH_KEY_CREATED=1
        echo "  keypair ed25519 generado en $DEPLOY_SSH_KEY."
    fi
    # La privada la lee ssh como el usuario de despliegue, asi que su dueno es ese
    # usuario y el modo 600. No se usa install_if_changed porque el origen y el
    # destino son el mismo archivo y `install` se niega ("are the same file").
    # El `-f` no es defensivo: en dry-run `ssh-keygen` no corre y el archivo no
    # existe, asi que `stat` fallaba con "cannot statx" en medio del plan. Un
    # error real en el dry-run es ruido que tapa los de verdad.
    if [ -f "$DEPLOY_SSH_KEY" ] \
        && [ "$(stat -c '%U:%G %a' "$DEPLOY_SSH_KEY")" != "$DEPLOY_LOGIN_USER:$DEPLOY_LOGIN_USER 600" ]; then
        run chown "$DEPLOY_LOGIN_USER:$DEPLOY_LOGIN_USER" "$DEPLOY_SSH_KEY"
        run chmod 0600 "$DEPLOY_SSH_KEY"
    fi
    run chown "$DEPLOY_LOGIN_USER:$DEPLOY_LOGIN_USER" "$DEPLOY_SSH_KEY.pub" 2>/dev/null || true
    run chmod 0644 "$DEPLOY_SSH_KEY.pub" 2>/dev/null || true

    # La clave publica tiene que quedar en el authorized_keys del usuario de
    # despliegue. El par se genera ACA y no en la maquina de quien despliega, asi
    # que si nadie instala el `.pub`, sshd no tiene contra que comparar y el
    # workflow falla con "Permission denied (publickey)" aunque el secret tenga
    # la clave correcta. El mensaje del final pedia pegar la PUBLICA en el
    # secret, que es al reves: GitHub se autentica con la privada y el servidor
    # compara con la publica.
    if [ -f "$DEPLOY_SSH_KEY.pub" ]; then
        local auth_keys="$DEPLOY_HOME/.ssh/authorized_keys"
        ensure_dir "$DEPLOY_HOME/.ssh" 0700 "$DEPLOY_LOGIN_USER" "$DEPLOY_LOGIN_USER"
        if [ ! -f "$auth_keys" ]; then
            run install -o "$DEPLOY_LOGIN_USER" -g "$DEPLOY_LOGIN_USER" \
                -m 0600 /dev/null "$auth_keys"
        fi
        # `grep -qxF` compara la linea entera: re-correr el instalador no puede
        # dejar la misma clave repetida, y una clave que sea prefijo de otra no
        # cuenta como presente.
        if grep -qxF "$(cat "$DEPLOY_SSH_KEY.pub")" "$auth_keys" 2>/dev/null; then
            echo "  la clave publica ya esta en $auth_keys."
        else
            run_sh "cat '$DEPLOY_SSH_KEY.pub' >> '$auth_keys'"
            echo "  clave publica agregada a $auth_keys."
        fi
    fi

    # El script de deploy va a /usr/local/sbin, propiedad de root, y NO a
    # $APP_DIR/deploy/deploy.sh: el checkout lo escribe $SERVICE_USER, y un
    # sudoers que apunte ahi es una escalada trivial (basta editar el archivo).
    if [ -f "$APP_DIR/deploy/deploy.sh" ] || need_checkout 'se instalaria webcmp-deploy desde deploy/deploy.sh'; then
        install_if_changed "$APP_DIR/deploy/deploy.sh" "$SCRIPT_PATH" 0755 root root
    fi

    # El sudoers se renderiza y SE VALIDA antes de instalarse. Un sudoers invalido
    # hace que sudo descarte el archivo entero con "garbage at end of line", y el
    # deploy falla con un "command not allowed" que parece de configuracion de CI.
    if [ -f "$APP_DIR/deploy/sudoers/webcmp-deploy" ] \
        || need_checkout 'el sudoers se renderizaria desde deploy/sudoers/webcmp-deploy'
    then
        tmp=$(mktemp)
        render_template "$APP_DIR/deploy/sudoers/webcmp-deploy" "$tmp" \
            "/usr/local/sbin/webcmp-deploy<>$SCRIPT_PATH" \
            "deploy ALL=(root)<>$DEPLOY_LOGIN_USER ALL=(root)" \
            || { rm -f "$tmp"; die "No se pudo renderizar el sudoers."; }
        visudo -c -f "$tmp" >/dev/null 2>&1 \
            || { visudo -c -f "$tmp"; rm -f "$tmp"; die "El sudoers renderizado no es valido: no se instalo."; }
        install_if_changed "$tmp" "$DEPLOY_SUDOERS" 0440 root root
        rm -f "$tmp"
        echo "  sudoers en $DEPLOY_SUDOERS, validado con visudo -c."
    fi

    if [ -f "$DEPLOY_SSH_KEY" ]; then
        echo "  la clave PRIVADA esta en $DEPLOY_SSH_KEY (no se imprime: es el secret)."
    fi
    cat <<EOF

  Clave PRIVADA del usuario de despliegue. Es lo que va al secret
  DEPLOY_SSH_KEY de GitHub (Settings > Environments > production); la publica
  ya quedo instalada en $DEPLOY_HOME/.ssh/authorized_keys de este servidor.

    sudo cat $DEPLOY_SSH_KEY

  OJO: esa es la privada, no la publica. Pegar $DEPLOY_SSH_KEY.pub en el secret
  es el error natural y falla con "Permission denied (publickey)", que no dice
  cual de las dos claves esta mal. La privada no se imprime aca a proposito: el
  scrollback de una sesion SSH es un lugar donde no conviene dejarla.

  Para una instalacion real conviene generar el par en la maquina desde la que
  se despliega y dejar aca solo la publica: una privada que nacio en el servidor
  es una llave mas que hay que rotar si el servidor se ve comprometido.

  Si se pega con DEPLOY_HOST, DEPLOY_USER y DEPLOY_SSH_KNOWN_HOSTS, el workflow
  puede conectarse. Ver deploy/README-deploy.md.
EOF
}

# --- C.11b Boton "Actualizar" del panel (change 025-update-web-deploy) ------
#
# Tres piezas, cada una con el privilegio minimo que necesita:
#
#   /var/lib/webcmp             deploy:webcmp 2775 -> el panel escribe la
#                               peticion, el ejecutor escribe el estado, y
#                               ninguno de los dos necesita privilegios extra.
#   webcmp-autodeploy.path      systemd observa el archivo y arranca el
#                               servicio cuando aparece.
#   webcmp-autodeploy.service   corre como el usuario de despliegue, que ya
#                               tiene la UNICA regla de sudoers existente.
#
# El proceso web (`webcmp`, shell /bin/false) jamas ejecuta nada: escribe un
# archivo. Esa es la razon de todo el diseno, y por eso este cambio no agrega ni
# una linea de sudoers ni un endpoint que ejecute comandos.
setup_autodeploy() {
    local tmp

    log "Boton 'Actualizar' del panel (ejecutor: $DEPLOY_LOGIN_USER)"

    # 2775 y no 0755: el proceso web tiene que PODER crear el archivo de
    # peticion dentro. El bit setgid (el 2) hace que el archivo nazca con grupo
    # $SERVICE_GROUP lo cree quien lo cree, asi que el ejecutor puede borrar la
    # peticion que escribio webcmp y el panel puede leer el estado que escribio
    # deploy. Sin el, cada lado crea archivos que el otro no puede tocar.
    ensure_dir "$AUTODEPLOY_STATE_DIR" 2775 "$DEPLOY_LOGIN_USER" "$SERVICE_GROUP"

    if [ -f "$APP_DIR/deploy/autodeploy.sh" ] \
        || need_checkout 'se instalaria webcmp-autodeploy desde deploy/autodeploy.sh'
    then
        # root:root en /usr/local/sbin, NUNCA dentro del checkout: ese
        # directorio lo escribe $SERVICE_USER, y un ejecutor que corre con sudo
        # y vive en un directorio escribible por el proceso web es la escalada
        # mas directa que existe.
        install_if_changed "$APP_DIR/deploy/autodeploy.sh" "$AUTODEPLOY_SCRIPT" 0755 root root
    fi

    if [ -f "$APP_DIR/deploy/systemd/webcmp-autodeploy.service" ] \
        || need_checkout 'se renderizaria webcmp-autodeploy.service'
    then
        tmp=$(mktemp)
        render_template "$APP_DIR/deploy/systemd/webcmp-autodeploy.service" "$tmp" \
            "/srv/webcmp<>$APP_DIR" \
            "User=deploy<>User=$DEPLOY_LOGIN_USER" \
            "Group=webcmp<>Group=$SERVICE_GROUP" \
            || { rm -f "$tmp"; die "No se pudo renderizar webcmp-autodeploy.service."; }
        install_if_changed "$tmp" "/etc/systemd/system/$AUTODEPLOY_UNIT.service" 0644 root root
        rm -f "$tmp"
    fi

    if [ -f "$APP_DIR/deploy/systemd/webcmp-autodeploy.path" ] \
        || need_checkout 'se renderizaria webcmp-autodeploy.path'
    then
        tmp=$(mktemp)
        # El `Unit=` del .path se renderiza tambien: si alguien cambia
        # AUTODEPLOY_UNIT, un .path que siga apuntando al nombre viejo queda
        # habilitado y nunca dispara nada, sin un solo error que lo delate.
        render_template "$APP_DIR/deploy/systemd/webcmp-autodeploy.path" "$tmp" \
            "/srv/webcmp<>$APP_DIR" \
            "Unit=webcmp-autodeploy.service<>Unit=$AUTODEPLOY_UNIT.service" \
            || { rm -f "$tmp"; die "No se pudo renderizar webcmp-autodeploy.path."; }
        install_if_changed "$tmp" "/etc/systemd/system/$AUTODEPLOY_UNIT.path" 0644 root root
        rm -f "$tmp"
    fi

    run systemctl daemon-reload
    # `enable --now` deja el .path vigilando. No se arranca el .service: se
    # dispara cuando aparece la peticion, y arrancarlo ahora desplegaria sin que
    # nadie lo haya pedido.
    run systemctl enable --now "$AUTODEPLOY_UNIT.path"

    echo "  peticion: $AUTODEPLOY_STATE_DIR/deploy-request"
    echo "  ejecutor: $AUTODEPLOY_SCRIPT (usuario $DEPLOY_LOGIN_USER)"
    echo "  estado:   journalctl -u $AUTODEPLOY_UNIT"
}

if [ "$SKIP_DEPLOY_SCAFFOLD" -eq 1 ]; then
    log "Andamiaje de deploy automatico: omitido (--skip-deploy-scaffold)"
    log "Boton 'Actualizar' del panel: omitido (necesita el usuario $DEPLOY_LOGIN_USER)"
else
    setup_deploy_scaffold
    setup_autodeploy
fi

# --- C.12 Arranque de los servicios --------------------------------------

# El `step` va FUERA del if a proposito: con el .env incompleto la seccion igual
# se visita e igual informa (dice que no arrancan y que hacer). Si el `step`
# dependiera de la rama, en ese camino la barra quedaria en 92% para siempre,
# colgada en un numero que no llego a 100% y sin explicacion de por que.
step 4 "Arranque de los servicios"

if [ -n "$PENDING_CHANGES" ]; then
    log "No se arrancan $GUNICORN_UNIT ni $HUEY_UNIT: el .env esta incompleto."
    warn "Cuando se completen los valores:"
    printf '  sudo systemctl enable --now %s %s\n' "$GUNICORN_UNIT" "$HUEY_UNIT" >&2
else
    run systemctl enable --now "$GUNICORN_UNIT"
    run systemctl enable --now "$HUEY_UNIT"
    sleep 2
    # GUNICORN_ENABLED mide que el servicio quedo ACTIVO, no que se le dio enable:
    # `enable --now` puede salir 0 y dejar el unit caido, y el resumen no tiene
    # que afirmar "arrancado" cuando lo que paso fue que quedo inactivo.
    if run systemctl is-active --quiet "$GUNICORN_UNIT"; then
        GUNICORN_ENABLED=1
    else
        warn "$GUNICORN_UNIT quedo inactivo (journalctl -u $GUNICORN_UNIT)."
    fi
    run systemctl is-active --quiet "$HUEY_UNIT" || warn "$HUEY_UNIT quedo inactivo (journalctl -u $HUEY_UNIT)."
    # Nginx corre en los dos modos: tambien en `external`, donde sirve /media/,
    # /static/ y hace proxy al socket.
    run systemctl restart nginx
    run systemctl is-active --quiet nginx || warn "nginx quedo inactivo (journalctl -u nginx)."
fi

# --- C.13 Resumen --------------------------------------------------------

progress_clear
echo
if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: no se cambio nada."
else
    # Los "hechos" son los contadores que el propio instalador fue anotando. Nada
    # de esto se deduce de un flag global tipo CHANGED, que decia "si, algo" y
    # no le decia al operador nada: si fallo a mitad, el texto lo decia igual que
    # si no hubiera fallado. Un resumen que no puede mentir tiene que salir de
    # cosas que solo se cuentan cuando ocurrieron.
    cat <<EOF

  Instalado en $(( ( $(date +%s) - INSTALL_STARTED_AT ) / 60 )) min.
  Se aplicaron $STEPS_DONE de $STEPS_TOTAL pasos.

EOF
    [ "$CHANGED" -eq 0 ] && printf '  No habia nada que cambiar: todo ya estaba como debia.\n'
    [ "$DEPLOY_SSH_KEY_CREATED" -eq 1 ] && printf '  Se creo la clave del usuario de despliegue (%s).\n' "$DEPLOY_SSH_KEY.pub"
    [ "$SUPERUSER_CREATED" -eq 1 ] && printf '  Superusuario %s creado.\n' "$SUPERUSER_USERNAME"
    [ "$GUNICORN_ENABLED" -eq 1 ] && printf '  webcmp habilitado y arrancado.\n'
    [ -f "$ENV_FILE" ] && printf '  Configuracion en %s.\n' "$ENV_FILE"

    echo
    echo "  Siguiente paso:"
    printf '       sudo %s --check\n' "$SCRIPT_PATH"
fi

# Checklist final con TODOs que SI pueden quedar sin resolver.
PROTOCOL_SCHEME=${PROTOCOL_SCHEME:-http}
if [ "$PROXY_MODE" = nginx-local ] && [ "$TLS_MODE" != none ]; then
    PROTOCOL_SCHEME=https
elif [ "$PROXY_MODE" = nginx-local ] && [ "$TLS_MODE" = none ]; then
    PROTOCOL_SCHEME=http
elif [ "$PROXY_MODE" = external ]; then
    PROTOCOL_SCHEME=${PROTOCOL_SCHEME:-https}
fi
if [ "$DRY_RUN" -eq 0 ]; then
    printf '       %s://%s/\n\n' "${PROTOCOL_SCHEME:-http}" "${PUBLIC_HOSTNAME:-www.meteocamaguey.cu}"
fi

if [ "$DRY_RUN" -eq 0 ] && [ "$INTERACTIVE" -eq 1 ] && [ "${#ANSWER_SOURCES[@]}" -gt 0 ]; then
    echo
    echo "Respuestas (los secretos van enmascarados):"
    printf '  %s\n' "${ANSWER_SOURCES[@]}"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    exit 0
fi

cat <<EOF

  ======================================================================
  QUE FALTA (esta parte NO la automatiza ningun script)
  ======================================================================

  1. Secrets de GitHub (Settings > Environments > production):

       DEPLOY_HOST              IP o hostname de este servidor
       DEPLOY_USER              $DEPLOY_LOGIN_USER
       DEPLOY_SSH_KEY           contenido de $DEPLOY_SSH_KEY (la privada; la
                                publica ya esta en $DEPLOY_HOME/.ssh/authorized_keys)
       DEPLOY_SSH_KNOWN_HOSTS   una linea, con el puerto si no es 22:

           ssh-keyscan -p 22 -H <host> 2>/dev/null | sed 's/^/|1 h=|/' > known_hosts

     A mano, DESPUES de un deploy de prueba:

           ssh-keyscan -H <host> 2>/dev/null | ssh-keygen -lf -

     Comparar ese fingerprint con el del del servidor. Un \`ssh-keyscan\` sin
     verificar devuelve lo que conteste ese puerto, incluido el de un atacante.

  2. Verificar el sitio y el deploy:

       sudo $SCRIPT_PATH --check
       sudo $SCRIPT_PATH \$(git -C $APP_DIR rev-parse HEAD)

     El rollback automatico no se conoce hasta que se lo vio funcionar una vez:
     probalo desplegando un commit anterior y confirmando que el sitio vuelve.

  3. Archivos que quedaron con material de clave:

       $ENV_FILE              (600 $SERVICE_USER)   SECRET_KEY cifrada, DB_PASS
       $ENCRYPTION_ENV        (600 root)             ENCRYPTION_KEY
       $CONFIG_FILE           (600 root)             sin secretos
EOF
