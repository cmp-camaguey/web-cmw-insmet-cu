# Ejecutor del boton "Aplicar cambios" del panel de entorno
# (deploy/apply-env.sh, change 033-entorno-servidor).
#
# Lo que importa probar es el contrato que sostiene el diseno: el script no
# obedece al archivo de peticion, deja un estado que el panel puede leer, y
# distingue un reinicio bueno de uno fallido. Todo con un `systemctl` de mentira:
# la suite no reinicia servicios de la maquina donde corre.

APPLY_SH=$REPO_ROOT/deploy/apply-env.sh

apply_tmp=$(mktemp -d)
apply_bin=$apply_tmp/bin
mkdir -p "$apply_bin"

cat > "$apply_bin/systemctl" <<'STUB'
#!/usr/bin/env bash
# Stub: anota la invocacion y copia el estado que habia en ese momento, para
# poder comprobar que `running` se escribe ANTES de reiniciar y no despues.
printf '%s\n' "$*" > "$STUB_CALL"
cp "$STATUS_FILE" "$STUB_STATUS_AT_CALL" 2>/dev/null || true
exit "${STUB_RC:-0}"
STUB
chmod +x "$apply_bin/systemctl"

# Corre el script contra el stub y deja el codigo de salida en `apply_rc`.
#   apply_run <directorio-de-trabajo> [VAR=valor ...]
apply_run() {
    local dir=$1
    shift
    env STATUS_FILE="$dir/status.json" \
        REQUEST_FILE="$dir/request" \
        LOCKFILE="$dir/apply.lock" \
        SYSTEMCTL="$apply_bin/systemctl" \
        UNITS='webcmp.service webcmp-huey.service' \
        STUB_CALL="$dir/call" \
        STUB_STATUS_AT_CALL="$dir/at-call.json" \
        "$@" \
        bash "$APPLY_SH" >"$dir/log" 2>&1
    apply_rc=$?
}

# Lee un campo del JSON de estado. Un JSON roto imprime vacio en vez de reventar
# la corrida: eso ya lo reporta el assert que compara el valor.
apply_field() {
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]))' \
        "$1" "$2" 2>/dev/null
}

# --- Camino feliz ---------------------------------------------------------

apply_case=$apply_tmp/ok
mkdir -p "$apply_case"
printf '{"requested_by": "omar", "requested_at": "2026-11-10T12:00:00+00:00"}\n' \
    > "$apply_case/request"

apply_run "$apply_case"
assert_eq 'apply-env sale 0 cuando el reinicio funciona' 0 "$apply_rc"
assert_eq 'apply-env reinicia las dos unidades' \
    'restart webcmp.service webcmp-huey.service' "$(cat "$apply_case/call" 2>/dev/null)"
assert_eq 'apply-env consume la peticion' 'no' \
    "$([ -e "$apply_case/request" ] && printf 'si' || printf 'no')"
assert_eq 'apply-env marca el reinicio como terminado' ok \
    "$(apply_field "$apply_case/status.json" status)"
assert_eq 'apply-env conserva quien lo pidio' omar \
    "$(apply_field "$apply_case/status.json" requested_by)"
assert_eq 'apply-env registra el instante de fin' 'si' \
    "$([ "$(apply_field "$apply_case/status.json" finished_at)" != None ] \
        && printf 'si' || printf 'no')"
# El estado se escribe ANTES del restart: si el sitio se cae a mitad del
# reinicio, el panel tiene que encontrar "en curso", no el resultado viejo.
assert_eq 'apply-env escribe running antes de reiniciar' running \
    "$(apply_field "$apply_case/at-call.json" status)"

# --- Reinicio fallido -----------------------------------------------------

apply_case=$apply_tmp/fail
mkdir -p "$apply_case"
printf '{"requested_by": "omar"}\n' > "$apply_case/request"

apply_run "$apply_case" STUB_RC=1
assert_eq 'apply-env sale distinto de cero cuando el reinicio falla' 1 "$apply_rc"
assert_eq 'apply-env marca el fallo en el estado' failed \
    "$(apply_field "$apply_case/status.json" status)"
assert_contains 'apply-env explica el fallo en el estado' 'systemctl restart' \
    "$(apply_field "$apply_case/status.json" error)"
assert_eq 'apply-env consume la peticion aunque falle' 'no' \
    "$([ -e "$apply_case/request" ] && printf 'si' || printf 'no')"

# --- El archivo de peticion no manda --------------------------------------

# Un proceso web comprometido puede escribir cualquier cosa ahi. Lo unico que el
# script toma es el nombre, y saneado: ni la comilla rompe el JSON ni el
# `status`/`command` plantados llegan al estado que lee el panel.
apply_case=$apply_tmp/inject
mkdir -p "$apply_case"
printf '{"requested_by": "mal\\"icioso", "status": "failed", "command": "reboot"}\n' \
    > "$apply_case/request"

apply_run "$apply_case"
assert_eq 'apply-env sobrevive a una peticion manipulada' 0 "$apply_rc"
assert_eq 'apply-env corta el nombre en la primera comilla' mal \
    "$(apply_field "$apply_case/status.json" requested_by)"
assert_eq 'apply-env no acepta el estado que trae la peticion' ok \
    "$(apply_field "$apply_case/status.json" status)"
assert_eq 'apply-env reinicia solo lo que declara el unit' \
    'restart webcmp.service webcmp-huey.service' "$(cat "$apply_case/call" 2>/dev/null)"

# --- Sin peticion ---------------------------------------------------------

# systemd puede disparar la unidad a mano (`systemctl start webcmp-apply`) y el
# script tiene que hacer lo mismo, sin inventar un autor.
apply_case=$apply_tmp/norequest
mkdir -p "$apply_case"

apply_run "$apply_case"
assert_eq 'apply-env funciona sin archivo de peticion' 0 "$apply_rc"
assert_eq 'apply-env no inventa un autor' '' \
    "$(apply_field "$apply_case/status.json" requested_by)"

# --- Un solo ejecutor -----------------------------------------------------

# `PathExists=` no vuelve a disparar mientras el archivo siga ahi, y el panel lo
# crea con O_EXCL. Si un segundo disparo reiniciara, el panel diria "en curso"
# dos veces por la misma pulsacion.
apply_case=$apply_tmp/locked
mkdir -p "$apply_case"
printf '{"requested_by": "omar"}\n' > "$apply_case/request"

flock -x "$apply_case/apply.lock" -c 'sleep 3' &
apply_lock_pid=$!
sleep 1
apply_run "$apply_case"
assert_eq 'apply-env con el lock tomado sale 0' 0 "$apply_rc"
assert_contains 'apply-env avisa que descarto la peticion' 'descartada' \
    "$(cat "$apply_case/log" 2>/dev/null)"
assert_eq 'apply-env con el lock tomado no reinicia' 'no' \
    "$([ -e "$apply_case/call" ] && printf 'si' || printf 'no')"
assert_eq 'apply-env con el lock tomado consume la peticion' 'no' \
    "$([ -e "$apply_case/request" ] && printf 'si' || printf 'no')"
wait "$apply_lock_pid" 2>/dev/null

# --- Sin systemctl --------------------------------------------------------

# Si el binario no existe, el panel no puede quedarse con el estado anterior:
# hay que dejar un fallo escrito, no un silencio.
apply_case=$apply_tmp/nosystemctl
mkdir -p "$apply_case"
printf '{"requested_by": "omar"}\n' > "$apply_case/request"

apply_run "$apply_case" SYSTEMCTL="$apply_tmp/no-existe"
assert_eq 'apply-env sin systemctl sale distinto de cero' 1 "$apply_rc"
assert_eq 'apply-env sin systemctl deja el fallo escrito' failed \
    "$(apply_field "$apply_case/status.json" status)"

rm -rf "$apply_tmp"
