# `missing_units` de deploy.sh: lo que el deploy no puede instalar y si puede
# comprobar (deploy.sh, TEST-BLOCK `missing-units`).
#
# El caso real que motiva esto: el servidor se actualizo a un commit que pedia
# `webcmp-apply.path`, la unidad no estaba instalada, el deploy termino "ok" y el
# boton "Aplicar cambios" del panel escribio su peticion en un directorio que
# nadie vigilaba. La pagina no mostraba nada, que es indistinguible de "nunca se
# pidio".
#
# Se prueba con arboles temporales: la suite no lee ni escribe /etc/systemd.
#
# Se ejecuta con `source` desde run.sh; no se corre solo.

DEPLOY_SH_BLOCKS=$REPO_ROOT/deploy/deploy.sh
units_block=$(require_block missing-units "$DEPLOY_SH_BLOCKS")

units_tmp=$(mktemp -d)
mkdir -p "$units_tmp/app/deploy/systemd" "$units_tmp/system"
for unit in webcmp.service webcmp-huey.service webcmp-autodeploy.service \
    webcmp-autodeploy.path webcmp-apply.service webcmp-apply.path
do
    : > "$units_tmp/app/deploy/systemd/$unit"
done

# Corre `missing_units` del propio deploy.sh contra los arboles temporales.
#   missing_units_run [VAR=valor ...]
missing_units_run() {
    (
        APP_DIR=$units_tmp/app
        SYSTEMD_UNIT_DIR=$units_tmp/system
        GUNICORN_UNIT=webcmp
        HUEY_UNIT=webcmp-huey
        AUTODEPLOY_UNIT=webcmp-autodeploy
        APPLY_UNIT=webcmp-apply
        for assignment in "$@"; do
            eval "export $assignment"
        done
        eval "$units_block"
        missing_units
    )
}

# --- Todo instalado -------------------------------------------------------

for unit in webcmp.service webcmp-huey.service webcmp-autodeploy.service \
    webcmp-autodeploy.path webcmp-apply.service webcmp-apply.path
do
    : > "$units_tmp/system/$unit"
done
assert_eq 'con todo instalado no reporta faltantes' '' "$(missing_units_run)"

# --- Falta justo el andamiaje del panel -----------------------------------

rm -f "$units_tmp/system/webcmp-apply.path"
assert_eq 'reporta el .path del panel cuando falta' 'webcmp-apply.path' \
    "$(missing_units_run)"

rm -f "$units_tmp/system/webcmp-apply.service"
assert_eq 'reporta las dos piezas del panel cuando faltan' \
    'webcmp-apply.service webcmp-apply.path' "$(missing_units_run | tr '\n' ' ' | sed 's/ $//')"

# --- Nombres renderizados -------------------------------------------------
#
# install.sh renderiza cuatro unidades con el nombre de una variable. Un deploy
# que compare contra el nombre del archivo reportaria un faltante falso en un
# servidor perfectamente instalado, y el operador aprenderia a ignorar el error.

rm -f "$units_tmp/system/"*
for unit in cmpweb.service cmphuey.service cmpapply.service cmpapply.path \
    cmpauto.service cmpauto.path
do
    : > "$units_tmp/system/$unit"
done
assert_eq 'respeta los nombres configurados de las unidades' '' \
    "$(missing_units_run GUNICORN_UNIT=cmpweb HUEY_UNIT=cmphuey \
        AUTODEPLOY_UNIT=cmpauto APPLY_UNIT=cmpapply)"

rm -f "$units_tmp/system/cmpweb.service"
assert_eq 'con nombres configurados reporta el nombre renderizado' 'cmpweb.service' \
    "$(missing_units_run GUNICORN_UNIT=cmpweb HUEY_UNIT=cmphuey \
        AUTODEPLOY_UNIT=cmpauto APPLY_UNIT=cmpapply)"

# --- Sin checkout ---------------------------------------------------------
#
# `deploy.sh` llama a `missing_units` sin comprobar que exista deploy/systemd:
# el guard corre despues del checkout, asi que el directorio esta. Si no esta, no
# hay nada que comparar y el deploy no puede inventar un faltante.

rm -rf "$units_tmp/app"
assert_eq 'sin deploy/systemd no inventa faltantes' '' "$(missing_units_run)"

# --- El guard corre en el lugar donde sirve -------------------------------
#
# Despues del checkout (una unidad nueva recien aparece en el arbol nuevo) y
# antes de pip/migrate/restart (el rollback devuelve el codigo y no deshace nada
# de lo aplicado despues).

deploy_src=$(cat "$DEPLOY_SH_BLOCKS")
checkout_line=$(grep -n 'gitapp checkout --force --quiet "\$target_sha"' "$DEPLOY_SH_BLOCKS" | head -1 | cut -d: -f1)
guard_line=$(grep -n 'missing=$(missing_units)' "$DEPLOY_SH_BLOCKS" | tail -1 | cut -d: -f1)
pip_line=$(grep -n 'Instalando requirements/prod.txt' "$DEPLOY_SH_BLOCKS" | head -1 | cut -d: -f1)
assert_eq 'el guard corre despues del checkout' 'despues' \
    "$(if [ "$guard_line" -gt "$checkout_line" ]; then echo despues; else echo antes; fi)"
assert_eq 'el guard corre antes de instalar requirements' 'antes' \
    "$(if [ "$guard_line" -lt "$pip_line" ]; then echo antes; else echo despues; fi)"
assert_contains 'el guard manda a instalar las piezas' \
    'install-apply-units.sh' "$deploy_src"
assert_contains 'el guard explica por que el deploy no las instala' \
    'copiar units desde ahi seria ejecutar como root' "$deploy_src"

# --- ensure_apply_path ----------------------------------------------------
#
# Instalada no es operativa: el boton escribe su peticion y no pasa nada. El
# deploy no puede instalar la unidad, pero si habilitarla, y sin esto el arreglo
# no llega por el boton "Actualizar", que es la unica via que tiene el operador.

apply_block=$(require_block ensure-apply-path "$DEPLOY_SH_BLOCKS")

apply_dir=$(mktemp -d)
mkdir -p "$apply_dir/system" "$apply_dir/state" "$apply_dir/bin"

cat > "$apply_dir/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$1" in
    is-active) [ "${STUB_ACTIVE:-1}" -eq 1 ] ;;
    start) [ "${STUB_START_RC:-0}" -eq 0 ] ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$apply_dir/bin/systemctl"

#   ensure_apply_path_run [VAR=valor ...]
ensure_apply_path_run() {
    (
        SYSTEMD_UNIT_DIR=$apply_dir/system
        APPLY_UNIT=webcmp-apply
        APPLY_STATE_DIR=$apply_dir/state
        APPLY_REQUEST_FILE=$apply_dir/state/apply-request
        SYSTEMCTL=$apply_dir/bin/systemctl
        STUB_LOG=$apply_dir/systemctl.log
        export STUB_LOG
        log() { printf '==> %s\n' "$*"; }
        die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
        for assignment in "$@"; do
            eval "export $assignment"
        done
        eval "$apply_block"
        ensure_apply_path
    ) >"$apply_dir/out.log" 2>&1
    ensure_rc=$?
    ensure_out=$(cat "$apply_dir/out.log")
}

: > "$apply_dir/systemctl.log"
ensure_apply_path_run
assert_eq 'sin la unidad instalada no toca nada' 0 "$ensure_rc"
assert_not_contains 'sin la unidad instalada no invoca systemctl' \
    'enable' "$(cat "$apply_dir/systemctl.log")"
assert_contains 'sin la unidad instalada lo dice' 'no esta instalado' "$ensure_out"

: > "$apply_dir/system/webcmp-apply.path"
: > "$apply_dir/systemctl.log"
ensure_apply_path_run
assert_eq 'con la unidad instalada sale 0' 0 "$ensure_rc"
assert_contains 'habilita la unidad' 'enable webcmp-apply.path' \
    "$(cat "$apply_dir/systemctl.log")"
assert_contains 'arranca la unidad' 'start webcmp-apply.path' \
    "$(cat "$apply_dir/systemctl.log")"
assert_contains 'lo informa' 'operativo' "$ensure_out"

# Una peticion vieja dispararia el reinicio en cuanto la unidad quede activa, en
# medio del deploy. El reinicio de este deploy ya la cumple: se descarta.
printf '{"requested_by": "omar"}\n' > "$apply_dir/state/apply-request"
: > "$apply_dir/systemctl.log"
ensure_apply_path_run
assert_eq 'con una peticion pendiente sale 0' 0 "$ensure_rc"
assert_eq 'con una peticion pendiente la descarta' 'no' \
    "$(if [ -e "$apply_dir/state/apply-request" ]; then echo si; else echo no; fi)"
assert_contains 'con una peticion pendiente lo explica' \
    'ya la cumple' "$ensure_out"

# Si la unidad no arranca, el boton queda muerto y el deploy no puede decir "ok".
: > "$apply_dir/systemctl.log"
ensure_apply_path_run STUB_START_RC=1
assert_eq 'si no arranca, el deploy falla' 1 "$ensure_rc"
assert_contains 'si no arranca, lo explica' 'no va a reiniciar' "$ensure_out"

: > "$apply_dir/systemctl.log"
ensure_apply_path_run STUB_ACTIVE=0
assert_eq 'si queda inactiva, el deploy falla' 1 "$ensure_rc"
assert_contains 'si queda inactiva, lo explica' 'quedo inactivo' "$ensure_out"

rm -rf "$apply_dir"

rm -rf "$units_tmp"
