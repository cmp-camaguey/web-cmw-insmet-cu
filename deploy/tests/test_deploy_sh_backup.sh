#!/usr/bin/env bash
# Pruebas del respaldo previo a migraciones de deploy/deploy.sh.
#
# Lo que se prueba es la decision ("hay algo pendiente?") y la rotacion, porque
# las dos se pueden romper en silencio: si `pending_migrations` se invierte, el
# deploy deja de respaldar y nadie lo nota hasta que hace falta el dump. El
# `pg_dump` en si necesita PostgreSQL y systemd, y eso se prueba en el servidor.
#
# Se ejecuta con `source` desde run.sh; no se corre solo.

DEPLOY_SH=$REPO_ROOT/deploy/deploy.sh

# --- La inversion de `migrate --check` -------------------------------------
#
# `migrate --check` sale 1 cuando HAY pendientes y 0 cuando el esquema esta al
# dia, que es al reves de lo que uno lee de memoria. Se ejercita la funcion real
# con `manage` interceptado.
pending_body=$(require_block pending-migrations "$DEPLOY_SH")

pending_probe() {
    local rc=$1
    (
        manage() { return "$rc"; }
        eval "$pending_body"
        if pending_migrations; then printf 'hay'; else printf 'no'; fi
    )
}

assert_eq 'pending_migrations: 1 de migrate --check significa pendientes' \
    'hay' "$(pending_probe 1)"
assert_eq 'pending_migrations: 0 de migrate --check significa al dia' \
    'no' "$(pending_probe 0)"
assert_eq 'pending_migrations: otro fallo cuenta como pendiente (falla cerrado)' \
    'hay' "$(pending_probe 2)"

# --- Rotacion ---------------------------------------------------------------

prune_body=$(require_block prune-db-backups "$DEPLOY_SH")

# prune_probe <KEEP> <archivo>...
#
# Crea un directorio temporal, le da a cada archivo un mtime creciente en el
# orden recibido (el ultimo es el mas nuevo), corre `prune_db_backups` y devuelve
# lo que quedo, ordenado y separado por comas.
prune_probe() {
    local keep=$1
    shift
    local dir file i=0
    dir=$(mktemp -d)
    for file in "$@"; do
        i=$((i + 1))
        : >"$dir/$file"
        # mtime explicito: el orden de creacion ya bastaria en la mayoria de los
        # filesystems, pero fijarlo evita depender de la resolucion del reloj.
        touch -d "2024-01-01 00:00:$(printf '%02d' "$i")" "$dir/$file"
    done
    (
        DB_BACKUP_DIR=$dir
        DB_BACKUP_KEEP=$keep
        eval "$prune_body"
        prune_db_backups
    )
    ls -1 "$dir" 2>/dev/null | sort | paste -sd, -
    rm -rf "$dir"
}

assert_eq 'respaldo: conserva los N mas nuevos y borra los viejos' \
    'c.dump,d.dump' "$(prune_probe 2 a.dump b.dump c.dump d.dump)"
assert_eq 'respaldo: con menos respaldos que KEEP no borra ninguno' \
    'a.dump,b.dump' "$(prune_probe 10 a.dump b.dump)"
assert_eq 'respaldo: KEEP basura cae al default de 10' \
    'a.dump,b.dump,c.dump' "$(prune_probe 'x' a.dump b.dump c.dump)"
assert_eq 'respaldo: KEEP=0 se sube a 1, nunca borra todo' \
    'c.dump' "$(prune_probe 0 a.dump b.dump c.dump)"
assert_eq 'respaldo: un directorio vacio no rompe la rotacion' \
    '' "$(prune_probe 3)"
assert_eq 'respaldo: no toca archivos que no son .dump' \
    'a.dump,notas.txt' "$(prune_probe 1 a.dump notas.txt)"

# --- Orden y falla cerrada --------------------------------------------------

# El unico invariante que hace util al respaldo: tiene que tomarse ANTES de
# `migrate`. Un dump posterior a la migracion destructiva guarda la base ya
# rota.
backup_line=$(grep -n '^if pending_migrations; then' "$DEPLOY_SH" | cut -d: -f1)
migrate_line=$(grep -n '^log "Aplicando migraciones"' "$DEPLOY_SH" | cut -d: -f1)
if [ -n "$backup_line" ] && [ -n "$migrate_line" ] && [ "$backup_line" -lt "$migrate_line" ]; then
    ok 'respaldo: se toma antes de aplicar migraciones'
else
    fail 'respaldo: se toma antes de aplicar migraciones' \
        'el respaldo antes de "Aplicando migraciones"' \
        "respaldo=$backup_line migrate=$migrate_line"
fi

deploy_src=$(cat "$DEPLOY_SH")
assert_contains 'respaldo: si pg_dump falla, el deploy aborta sin migrar' \
    'El respaldo de la base fallo. NO se aplicaron migraciones.' "$deploy_src"
assert_contains 'respaldo: un .dump a medias se borra' \
    'rm -f "$db_dump_file"' "$deploy_src"
assert_contains 'respaldo: el dump queda 0600' \
    'chmod 0600 "$db_dump_file"' "$deploy_src"
assert_contains 'respaldo: el directorio es 0700 de root' \
    'install -d -m 0700 -o root -g root "$DB_BACKUP_DIR"' "$deploy_src"
assert_contains 'respaldo: la conexion sale de settings.DATABASES' \
    'db = settings.DATABASES["default"]' "$deploy_src"
assert_contains 'respaldo: el rollback nombra el dump' \
    'Respaldo previo a las migraciones: $db_dump_file' "$deploy_src"
assert_contains 'respaldo: --check lo valida' \
    '    check_db_backup' "$deploy_src"

# El deploy.env de una instalacion nueva nombra las dos claves. Los defaults del
# script ya funcionan sin ellas, pero un archivo que no las menciona hace que el
# respaldo parezca no existir hasta que hace falta.
install_src=$(cat "$REPO_ROOT/deploy/install.sh")
assert_contains 'respaldo: install.sh escribe DB_BACKUP_DIR en deploy.env' \
    'DB_BACKUP_DIR=/var/backups/webcmp/db' "$install_src"
assert_contains 'respaldo: install.sh escribe DB_BACKUP_KEEP en deploy.env' \
    'DB_BACKUP_KEEP=10' "$install_src"
# Sin pg_dump el deploy no migra (falla cerrado). Instalarlo en el instalador es
# lo que hace que esa decision sea segura en un contenedor minimo.
assert_contains 'respaldo: install.sh instala postgresql-client' \
    'postgresql-client' "$install_src"

# La plantilla del camino manual: si no las nombra, el respaldo parece no existir
# para quien instala sin el instalador.
example_src=$(cat "$REPO_ROOT/deploy/deploy.env.example")
assert_contains 'respaldo: deploy.env.example documenta DB_BACKUP_DIR' \
    'DB_BACKUP_DIR=/var/backups/webcmp/db' "$example_src"
assert_contains 'respaldo: deploy.env.example documenta DB_BACKUP_KEEP' \
    'DB_BACKUP_KEEP=10' "$example_src"
