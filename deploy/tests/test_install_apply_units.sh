# Instalador del boton "Aplicar cambios" (deploy/install-apply-units.sh).
#
# Lo que importa probar es el contrato que sostiene el diseno:
#
#   1. El render sustituye LITERALMENTE. `webcmp` como patron tambien matchearia
#      `webcmp-huey`, y un unit con el nombre del otro servicio no falla al
#      instalarse: falla cuando alguien pulsa el boton.
#   2. El script se niega a habilitar el .path con una peticion en disco, porque
#      PathExists= dispara ese reinicio en el mismo instante, fuera de la ventana
#      de mantenimiento.
#
# La instalacion real (modos, dueno root, idempotencia) se ejercita solo como
# root: `install -o root -g root` no se puede probar desde una cuenta normal, y
# un test que se saltea en silencio es peor que uno que no existe.
#
# Se ejecuta con `source` desde run.sh; no se corre solo.

APPLY_INSTALL_SH=$REPO_ROOT/deploy/install-apply-units.sh

# Los units de origen se llaman como en el repo (`webcmp-apply.*`); solo cambia
# el nombre del DESTINO. Buscar `$APPLY_UNIT` dentro del checkout falla con
# cualquier nombre configurado, y el script tiene que servir para renombrarlos.
assert_not_contains 'no busca el unit de origen con el nombre del destino' \
    'deploy/systemd/$APPLY_UNIT' "$(cat "$APPLY_INSTALL_SH")"

apply_pure=$(require_block unit-files "$APPLY_INSTALL_SH")
apply_main=$(require_block install-apply-units "$APPLY_INSTALL_SH")

apply_tmp=$(mktemp -d)

# --- Render: sustitucion literal, sin regex -------------------------------
#
# El caso que importa: reemplazar `webcmp` NO puede tocar `webcmp-huey`, y
# reemplazar `Unit=webcmp-apply.service` no puede dejar medio nombre cambiado.

mkdir -p "$apply_tmp/units"
printf '%s\n' \
    'WorkingDirectory=/srv/webcmp' \
    'Environment="UNITS=webcmp.service webcmp-huey.service"' \
    'PathExists=/var/lib/webcmp/apply-request' \
    'Unit=webcmp-apply.service' \
    'Group=webcmp' \
    '' > "$apply_tmp/sample.unit"

(
    eval "$apply_pure"
    render_unit "$apply_tmp/sample.unit" "$apply_tmp/units/rendered" \
        '/srv/webcmp<>/opt/cmp' \
        '/var/lib/webcmp/apply-request<>/opt/cmp/state/apply-request' \
        'Group=webcmp<>Group=cmpgrp' \
        'Unit=webcmp-apply.service<>Unit=cmp-reload.service'
) || true

rendered=$(cat "$apply_tmp/units/rendered" 2>/dev/null || true)
assert_contains 'render: cambia la ruta del checkout' 'WorkingDirectory=/opt/cmp' "$rendered"
assert_contains 'render: cambia el grupo del servicio' 'Group=cmpgrp' "$rendered"
assert_contains 'render: cambia el Unit= del .path' 'Unit=cmp-reload.service' "$rendered"
assert_contains 'render: mueve la peticion con el directorio de estado' \
    'PathExists=/opt/cmp/state/apply-request' "$rendered"
assert_contains 'render: no rompe el nombre del otro servicio' \
    'UNITS=webcmp.service webcmp-huey.service' "$rendered"
assert_eq 'render: conserva la linea en blanco final' '6' \
    "$(wc -l < "$apply_tmp/units/rendered" 2>/dev/null || printf 0)"

# --- Peticion sin recoger -------------------------------------------------
#
# Con el archivo presente, habilitar el .path dispara el reinicio de inmediato.

mkdir -p "$apply_tmp/state"

(
    eval "$apply_pure"
    REQUEST_FILE=$apply_tmp/state/apply-request
    refuse_pending_request
) >"$apply_tmp/refuse-ok.log" 2>&1
assert_eq 'refuse: sin peticion en disco sigue adelante' 0 "$?"

printf '{"requested_by": "omar"}\n' > "$apply_tmp/state/apply-request"

(
    eval "$apply_pure"
    REQUEST_FILE=$apply_tmp/state/apply-request
    refuse_pending_request
) >"$apply_tmp/refuse.log" 2>&1
refuse_rc=$?

assert_eq 'refuse: con peticion en disco se niega' 1 "$refuse_rc"
assert_contains 'refuse: dice donde esta la peticion' \
    "$apply_tmp/state/apply-request" "$(cat "$apply_tmp/refuse.log")"
assert_contains 'refuse: explica como destrabarlo' 'rm -f' "$(cat "$apply_tmp/refuse.log")"
rm -f "$apply_tmp/state/apply-request"

# --- Instalacion completa (solo como root) --------------------------------

if [ "$(id -u)" -ne 0 ]; then
    printf '  omitido  instalacion real de las unidades: la suite no corre como root\n'
else
    mkdir -p "$apply_tmp/app/deploy/systemd" "$apply_tmp/sbin" "$apply_tmp/bin" "$apply_tmp/units"
    cp "$REPO_ROOT/deploy/apply-env.sh" "$apply_tmp/app/deploy/apply-env.sh"
    cp "$REPO_ROOT/deploy/systemd/webcmp-apply.service" "$apply_tmp/app/deploy/systemd/"
    cp "$REPO_ROOT/deploy/systemd/webcmp-apply.path" "$apply_tmp/app/deploy/systemd/"

    cat > "$apply_tmp/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
exit 0
STUB
    chmod +x "$apply_tmp/bin/systemctl"

    apply_install_run() {
        (
            APP_DIR=$apply_tmp/app
            APPLY_UNIT=webcmp-apply
            APPLY_SCRIPT=$apply_tmp/sbin/webcmp-apply-env
            APPLY_STATE_DIR=$apply_tmp/state
            UNIT_DIR=$apply_tmp/units
            SYSTEMCTL=$apply_tmp/bin/systemctl
            GUNICORN_UNIT=webcmp
            HUEY_UNIT=webcmp-huey
            SERVICE_GROUP=webcmp
            DEPLOY_LOGIN_USER=webcmp
            DRY_RUN=0
            REQUEST_FILE=$apply_tmp/state/apply-request
            STUB_LOG=$apply_tmp/systemctl.log
            export STUB_LOG
            eval "$apply_pure"
            eval "$apply_main"
            install_apply_units
        ) >"$apply_tmp/out.log" 2>&1
    }

    apply_install_run
    assert_eq 'instalacion: sale 0' 0 "$?"
    assert_eq 'instalacion: el ejecutor queda root:root 755' 'root:root 755' \
        "$(stat -c '%U:%G %a' "$apply_tmp/sbin/webcmp-apply-env" 2>/dev/null || true)"
    assert_eq 'instalacion: el directorio de estado queda 2775' '2775' \
        "$(stat -c '%a' "$apply_tmp/state" 2>/dev/null || true)"
    assert_contains 'instalacion: recarga systemd' 'daemon-reload' \
        "$(cat "$apply_tmp/systemctl.log")"
    assert_contains 'instalacion: habilita el .path y no el .service' \
        'enable --now webcmp-apply.path' "$(cat "$apply_tmp/systemctl.log")"

    before=$(stat -c '%Y %s' "$apply_tmp/sbin/webcmp-apply-env")
    sleep 1
    apply_install_run
    assert_eq 'instalacion: la segunda corrida no reescribe el ejecutor' "$before" \
        "$(stat -c '%Y %s' "$apply_tmp/sbin/webcmp-apply-env")"

    printf '{"requested_by": "omar"}\n' > "$apply_tmp/state/apply-request"
    apply_install_run
    assert_eq 'instalacion: se niega con una peticion en disco' 1 "$?"
    rm -f "$apply_tmp/state/apply-request"
fi

rm -rf "$apply_tmp"
