#!/usr/bin/env bash
# Regresion del bug de systemd que tumbo un deploy real (2026-11-11).
#
# `systemd-run --pipe` no es solo "mostrar la salida": pide al gestor que pase
# descriptores por D-Bus al arrancar la unidad transitoria. En systemd 259 eso
# falla de forma intermitente con
#
#     Failed to start transient service unit: Connection reset by peer
#
# y no siempre: en el deploy que lo destapo, `--pipe` funciono tres veces
# (makemigrations, check --deploy, pg_dump) y murio en la cuarta (migrate). Un
# fallo intermitente en el paso que no admite "casi" es peor que uno limpio,
# porque el deploy avanza hasta la base de datos antes de morir.
#
# La prueba es estatica a proposito. Reproducir la carrera pide systemd 259 y un
# deploy real; lo que si se puede fijar es la decision — no depender de `--pipe`
# y capturar la salida con `append:` — para que nadie la reintroduzca "porque
# asi se ve la salida en el log".
#
# Se ejecuta con `source` desde run.sh; no se corre solo.

DEPLOY_SH=$REPO_ROOT/deploy/deploy.sh

# --- La decision: ninguna unidad transitoria usa --pipe ---------------------
#
# Se miran solo las lineas que invocan `systemd-run`. El comentario que explica
# el bug nombra `--pipe` a proposito, y buscarlo en todo el archivo daria una
# falsa alarma que empuja a borrar la explicacion en vez de mantener el arreglo.
pipe_lines=$(grep -n 'systemd-run' "$DEPLOY_SH" | grep -F -- '--pipe' || true)
assert_eq 'ningun systemd-run de deploy.sh usa --pipe' '' "$pipe_lines"

assert_contains 'deploy.sh sigue esperando a la unidad (--wait)' \
    'systemd-run --quiet --wait' "$(cat "$DEPLOY_SH")"

# --- La salida se captura sin descriptores por D-Bus ------------------------
assert_contains 'la salida de la unidad se captura con append: (stdout)' \
    'StandardOutput=append:' "$(cat "$DEPLOY_SH")"
assert_contains 'la salida de la unidad se captura con append: (stderr)' \
    'StandardError=append:' "$(cat "$DEPLOY_SH")"

# --- run_in_unit: propaga el codigo y vuelca la salida ----------------------
#
# Lo que se puede romper en silencio: que devuelva el codigo de `cat` (siempre 0)
# en vez del de la unidad. Un `migrate` fallido que sale 0 no aborta el deploy y
# deja el sitio en el commit nuevo con la base a medias.
unit_body=$(require_block run-in-unit "$DEPLOY_SH")

# unit_probe <codigo> <mensaje>: corre `run_in_unit` con un `systemd-run` falso
# que escribe el mensaje en el archivo que le piden y sale con <codigo>.
# Devuelve la salida volcada seguida de `rc=<codigo>`.
unit_probe() {
    local code=$1 message=$2
    (
        systemd-run() {
            local arg out=''
            for arg in "$@"; do
                case $arg in
                    StandardOutput=append:*) out=${arg#StandardOutput=append:} ;;
                esac
            done
            [ -n "$out" ] && printf '%s\n' "$message" >>"$out"
            # `code` y no `rc`: `rc` es el local de `run_in_unit`, y bash resuelve
            # los locales por alcance dinamico. Devolver `$rc` desde aca leeria el
            # 0 de la funcion que la llama y el caso "falla la unidad" pasaria.
            return "$code"
        }
        eval "$unit_body"
        run_in_unit --uid=x --gid=x true
        printf 'rc=%d' "$?"
    )
}

assert_eq 'run_in_unit devuelve el codigo de la unidad, no el de cat' \
    'rc=7' "$(unit_probe 7 'boom' | tail -n 1)"
assert_eq 'run_in_unit devuelve 0 cuando la unidad pasa' \
    'rc=0' "$(unit_probe 0 'ok' | tail -n 1)"
assert_contains 'run_in_unit vuelca al log la salida de la unidad' \
    'boom' "$(unit_probe 7 'boom')"
