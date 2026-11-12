#!/usr/bin/env bash
# Pruebas del progreso que deploy/deploy.sh publica para la barra del panel.
#
# Se ejercita la funcion real, extraida del script entre los marcadores
# TEST-BLOCK, no una copia que puede quedar desactualizada.
#
# Lo que importa no es el formato del JSON sino el contrato: que publique el paso
# en curso, que la escritura sea atomica (el panel lee el archivo cada dos
# segundos) y que un fallo al escribir NUNCA aborte un despliegue que estaba
# funcionando. Eso ultimo es lo unico que puede convertir una barra de progreso
# en un deploy roto.
#
# Se ejecuta con `source` desde run.sh; no se corre solo.

DEPLOY_SH=$REPO_ROOT/deploy/deploy.sh

progress_body=$(require_block deploy-progress "$DEPLOY_SH")

# Corre la funcion real con PROGRESS_FILE dentro de <dir>.
# progress_probe <dir> <paso>
progress_probe() {
    local dir=$1 step=$2
    (
        PROGRESS_FILE=$dir/deploy-progress.json
        eval "$progress_body"
        progress "$step"
    )
}

tmp=$(mktemp -d)

# --- Publica el paso, con marca de tiempo -----------------------------------

progress_probe "$tmp" migrate
progress_out=$(cat "$tmp/deploy-progress.json" 2>/dev/null || true)
assert_contains 'progress: el archivo lleva el paso en curso' '"step": "migrate"' "$progress_out"
assert_contains 'progress: el archivo lleva updated_at' '"updated_at"' "$progress_out"

# --- Atomico: no quedan temporales y el modo es legible ---------------------

leftovers=$(find "$tmp" -name 'deploy-progress.json.*' | wc -l)
assert_eq 'progress: no deja archivos temporales' '0' "$leftovers"
assert_eq 'progress: el archivo es legible por el proceso web' '644' \
    "$(stat -c '%a' "$tmp/deploy-progress.json" 2>/dev/null || printf 'sin archivo')"

# --- Cada paso pisa al anterior --------------------------------------------

progress_probe "$tmp" code
progress_probe "$tmp" static
progress_out=$(cat "$tmp/deploy-progress.json" 2>/dev/null || true)
assert_contains 'progress: el paso nuevo reemplaza al anterior' '"step": "static"' "$progress_out"
assert_not_contains 'progress: no quedan dos pasos en el archivo' '"code"' "$progress_out"

# --- Best-effort: directorio inexistente o sin permiso -----------------------

missing=$tmp/no-existe
progress_probe "$missing" migrate
missing_rc=$?
assert_eq 'progress: sin directorio devuelve 0' '0' "$missing_rc"
assert_eq 'progress: sin directorio no crea nada' 'no' \
    "$([ -e "$missing" ] && printf 'si' || printf 'no')"

# Con `set -e` en el script real, un fallo aca mataria el deploy. Se comprueba
# que la funcion no propague el error, con el directorio de solo lectura.
if [ "$(id -u)" -eq 0 ]; then
    printf '  omitido  progress sin permisos de escritura: la suite corre como root\n'
else
    readonly_dir=$tmp/readonly
    mkdir -p "$readonly_dir"
    chmod 0500 "$readonly_dir"
    (
        PROGRESS_FILE=$readonly_dir/deploy-progress.json
        eval "$progress_body"
        set -e
        progress migrate
        printf 'sobrevivio'
    ) >"$tmp/out" 2>&1
    rc=$?
    assert_eq 'progress: sin permiso de escritura no aborta el deploy' '0' "$rc"
    assert_contains 'progress: sin permiso sigue adelante en silencio' 'sobrevivio' \
        "$(cat "$tmp/out")"
    chmod 0700 "$readonly_dir"
fi

# --- Todas las secciones anuncian su paso ------------------------------------
#
# Un `progress` que se pierde al editar una seccion deja la barra congelada en el
# paso anterior, que es peor que no tener barra: dice "respaldando" mientras el
# script instala dependencias.

for step in code deps checks backup migrate static restart healthcheck rollback; do
    assert_contains "deploy.sh anuncia el paso '$step'" "progress $step" \
        "$(cat "$DEPLOY_SH")"
done

# --- El rollback se anuncia ANTES de revertir --------------------------------
#
# Al reves, la barra seguiria en el paso que fallo mientras el script ya esta
# revirtiendo: dice "instalando dependencias" cuando en realidad esta volviendo
# atras. Se comparan las lineas para no depender de la redaccion.

rollback_line=$(grep -n '^    progress rollback$' "$DEPLOY_SH" | cut -d: -f1)
revert_line=$(grep -n 'Se revierte el codigo' "$DEPLOY_SH" | cut -d: -f1)
assert_eq 'rollback: anuncia el paso antes de revertir' 'si' \
    "$([ -n "$rollback_line" ] && [ -n "$revert_line" ] && [ "$rollback_line" -lt "$revert_line" ] && printf 'si' || printf 'no')"

rm -rf "$tmp"
