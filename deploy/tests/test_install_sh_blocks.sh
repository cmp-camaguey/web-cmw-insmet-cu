# Pruebas de los bloques de deploy/install.sh que se pueden aislar del sistema.
#
# Cada bloque se extrae entre sus marcadores `# >>> TEST-BLOCK: <nombre>` y se
# ejecuta en un subshell con stubs de las funciones del instalador (`warn`,
# `die`, `can_prompt`, `ask_yes_no`). Se prueba el codigo real, no una copia: si
# alguien cambia el heredoc del validador o la toma del lock, estas pruebas lo
# ven.

INSTALL_SH=$REPO_ROOT/deploy/install.sh
TEST_TMP=$(mktemp -d "${TMPDIR:-/tmp}/webcmp-deploy-tests.XXXXXX")

# shellcheck disable=SC2064  # se expande ahora a proposito: TEST_TMP es fijo
trap "rm -rf '$TEST_TMP'" EXIT

# Python con `cryptography`: el del venv si existe, si no el del PATH (en CI es
# el de setup-python con requirements/test.txt, que lo incluye via base.txt).
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY_CRYPTO=$REPO_ROOT/.venv/bin/python
else
    PY_CRYPTO=$(command -v python3 || command -v python || true)
fi
if [ -n "$PY_CRYPTO" ] && "$PY_CRYPTO" -c 'import cryptography' 2>/dev/null; then
    ok 'Python con cryptography disponible'
    HAVE_CRYPTO=1
else
    fail 'Python con cryptography disponible' 'un Python con cryptography' "${PY_CRYPTO:-ninguno}"
    HAVE_CRYPTO=0
fi

# ---------------------------------------------------------------------------
# Validador del par SECRET_KEY/ENCRYPTION_KEY
# ---------------------------------------------------------------------------
#
# Tres codigos de salida y una decision por cada uno:
#   0 = el par descifra                     -> conservar
#   1 = el par NO descifra o falta una mitad -> preguntar/regenerar
#   2 = no se pudo verificar                -> conservar y avisar
# El bug que estas pruebas fijan: antes todo caia en el `except Exception` con
# codigo 2 y el `if` de bash solo miraba el 1, asi que la regeneracion nunca se
# disparaba.

if VALIDATOR_BLOCK=$(require_block env-pair-validator "$INSTALL_SH"); then
    VALIDATOR_DIR=$TEST_TMP/validator
    mkdir -p "$VALIDATOR_DIR"
    # El bloque invoca `$VENV_DIR/bin/python`. Se le da un "venv" falso con un
    # wrapper que executa el Python real: un symlink NO sirve, porque Python
    # resuelve el enlace y pierde el `site-packages` del venv (import
    # cryptography falla y todo saldria 2). En produccion `$VENV_DIR/bin/python`
    # es un venv de verdad, asi que el wrapper reproduce lo que importa: que el
    # interprete tenga cryptography.
    mkdir -p "$VALIDATOR_DIR/venv/bin"
    if [ "$HAVE_CRYPTO" -eq 1 ]; then
        printf '#!/bin/sh\nexec %s "$@"\n' "$PY_CRYPTO" > "$VALIDATOR_DIR/venv/bin/python"
        chmod +x "$VALIDATOR_DIR/venv/bin/python"

        "$PY_CRYPTO" - "$VALIDATOR_DIR" <<'PY'
import sys
from pathlib import Path

from cryptography.fernet import Fernet

tmp = Path(sys.argv[1])
key = Fernet.generate_key().decode()
secret = Fernet(key.encode()).encrypt(b'x' * 50).decode()
(tmp / 'encryption.env').write_text(f'# comentario\nENCRYPTION_KEY={key}\n')
(tmp / 'good.env').write_text(f'SECRET_KEY={secret}\n')
(tmp / 'plain.env').write_text('SECRET_KEY=texto-plano-sin-cifrar\n')
(tmp / 'badkey.env').write_text('ENCRYPTION_KEY=no-es-una-clave-fernet\n')
(tmp / 'empty.env').write_text('# sin SECRET_KEY\n')
other = Fernet.generate_key().decode()
(tmp / 'encryption-otra.env').write_text(f'ENCRYPTION_KEY={other}\n')
PY
    fi

    # Ejecuta el bloque real. Stubs: `die` sale 90 para distinguir "abortado a
    # proposito" de una falla del arnes; `ask_yes_no` responde lo que se le pide.
    # Los stubs leen las variables de `run_validator` (el subshell las hereda),
    # no sus propios argumentos: el bloque los llama sin pasar nada.
    run_validator() {  # <env_file> <enc_file> <interactivo> <respuesta> <dry_run>
        local env_file=$1 enc_file=$2 interactive=$3 answer=$4 dry_run=$5
        (
            set -uo pipefail
            warn() { printf 'AVISO: %s\n' "$*" >&2; }
            die() { printf 'ERROR: %s\n' "$*" >&2; exit 90; }
            can_prompt() { [ "$interactive" -eq 1 ] && [ "$dry_run" -eq 0 ]; }
            ask_yes_no() {
                printf 'PREGUNTA: %s (respuesta: %s)\n' "$2" "$answer" >&2
                [ "$answer" = si ]
            }
            ENV_FILE=$env_file
            ENCRYPTION_ENV=$enc_file
            DRY_RUN=$dry_run
            VENV_DIR=$VALIDATOR_DIR/venv
            PYTHON_BIN=$VALIDATOR_DIR/venv/bin/python
            ENV_REGENERATED=0
            eval "$VALIDATOR_BLOCK"
            printf 'REGENERATED=%s\n' "$ENV_REGENERATED"
        )
    }

    if [ "$HAVE_CRYPTO" -eq 1 ]; then
        vdir=$VALIDATOR_DIR

        out=$(run_validator "$vdir/good.env" "$vdir/encryption.env" 0 si 0 2>&1); rc=$?
        assert_eq 'par valido: sale 0' 0 "$rc"
        assert_contains 'par valido: conserva el .env' 'REGENERATED=0' "$out"

        out=$(run_validator "$vdir/good.env" "$vdir/encryption-otra.env" 0 si 0 2>&1); rc=$?
        assert_eq 'clave de otra instalacion: sale 0' 0 "$rc"
        assert_contains 'clave de otra instalacion: regenera' 'REGENERATED=1' "$out"

        out=$(run_validator "$vdir/good.env" "$vdir/no-existe.env" 0 si 0 2>&1); rc=$?
        assert_contains 'falta encryption.env: regenera' 'REGENERATED=1' "$out"

        out=$(run_validator "$vdir/empty.env" "$vdir/encryption.env" 0 si 0 2>&1); rc=$?
        assert_contains 'falta SECRET_KEY: regenera' 'REGENERATED=1' "$out"

        out=$(run_validator "$vdir/good.env" "$vdir/badkey.env" 0 si 0 2>&1); rc=$?
        assert_contains 'clave Fernet malformada: regenera' 'REGENERATED=1' "$out"

        out=$(run_validator "$vdir/plain.env" "$vdir/encryption.env" 0 si 0 2>&1); rc=$?
        assert_contains 'SECRET_KEY en texto plano: regenera' 'REGENERATED=1' "$out"

        out=$(run_validator "$vdir" "$vdir/encryption.env" 0 si 0 2>&1); rc=$?
        assert_contains 'ruta ilegible: conserva y avisa' 'REGENERATED=0' "$out"
        assert_contains 'ruta ilegible: dice que no pudo verificar' 'No se pudo verificar' "$out"

        # Interactivo: la persona decide. "no" aborta sin tocar nada (con el par
        # roto el instalador no puede seguir), "si" regenera.
        out=$(run_validator "$vdir/good.env" "$vdir/encryption-otra.env" 1 no 0 2>&1); rc=$?
        assert_eq 'par roto + respuesta no: aborta' 90 "$rc"
        assert_contains 'par roto + respuesta no: explica la salida' 'restaurá la mitad que falta' "$out"

        out=$(run_validator "$vdir/good.env" "$vdir/encryption-otra.env" 1 si 0 2>&1); rc=$?
        assert_eq 'par roto + respuesta si: sale 0' 0 "$rc"
        assert_contains 'par roto + respuesta si: regenera' 'REGENERATED=1' "$out"

        # Sin TTY no hay a quien preguntar: regenera avisando.
        out=$(run_validator "$vdir/good.env" "$vdir/encryption-otra.env" 0 si 0 2>&1); rc=$?
        assert_contains 'par roto sin terminal: regenera avisando' 'Sin terminal' "$out"

        # --dry-run no valida ni decide: el bloque entero se salta.
        out=$(run_validator "$vdir/good.env" "$vdir/encryption-otra.env" 0 si 1 2>&1); rc=$?
        assert_contains '--dry-run: no decide regenerar' 'REGENERATED=0' "$out"
    fi
fi

# ---------------------------------------------------------------------------
# Lock compartido con deploy.sh
# ---------------------------------------------------------------------------
#
# Es el mismo archivo que usa deploy.sh (`/var/lock/webcmp-deploy.lock`): si
# fueran dos locks distintos no se excluirian y el problema seguiria igual.

if LOCK_BLOCK=$(require_block deploy-lock "$INSTALL_SH"); then
    run_lock() {  # <lockfile> <dry_run>
        (
            set -uo pipefail
            die() { printf 'ERROR: %s\n' "$*" >&2; exit 90; }
            DEPLOY_LOCKFILE=$1
            DRY_RUN=$2
            eval "$LOCK_BLOCK"
        )
    }

    lock_free=$TEST_TMP/free.lock
    lock_held=$TEST_TMP/held.lock
    lock_ready=$TEST_TMP/held.ready

    out=$(run_lock "$lock_free" 0 2>&1); rc=$?
    assert_eq 'lock libre: se adquiere' 0 "$rc"
    assert_contains 'lock libre: lo informa' 'lock adquirido' "$out"

    # Un deploy corriendo = otro proceso tiene el lock. Se mantiene tomado desde
    # un subshell y se avisa con un archivo, sin sleeps a ciegas.
    (
        exec 9>"$lock_held"
        flock -n 9 || exit 1
        : > "$lock_ready"
        sleep 10
    ) &
    lock_holder=$!
    for _ in {1..100}; do
        [ -e "$lock_ready" ] && break
        sleep 0.05
    done

    out=$(run_lock "$lock_held" 0 2>&1); rc=$?
    assert_eq 'lock tomado: aborta' 90 "$rc"
    assert_contains 'lock tomado: dice que hay un despliegue' 'Ya hay un despliegue en curso' "$out"

    out=$(run_lock "$lock_held" 1 2>&1); rc=$?
    assert_eq 'lock tomado + --dry-run: no aborta' 0 "$rc"
    assert_not_contains 'lock tomado + --dry-run: no lo toma' 'lock adquirido' "$out"

    kill "$lock_holder" 2>/dev/null
    wait "$lock_holder" 2>/dev/null
fi

# ---------------------------------------------------------------------------
# Respaldo antes de regenerar el .env
# ---------------------------------------------------------------------------
#
# `--force` reescribe el .env COMPLETO desde la plantilla: sin respaldo, un par
# de claves roto se lleva por delante cualquier valor afinado a mano.

if BACKUP_BLOCK=$(require_block env-backup "$INSTALL_SH"); then
    run_backup() {  # <dir> <dry_run>
        (
            set -uo pipefail
            die() { printf 'ERROR: %s\n' "$*" >&2; exit 90; }
            ENV_FILE=$1/.env
            ENCRYPTION_ENV=$1/encryption.env
            DRY_RUN=$2
            eval "$BACKUP_BLOCK"
        )
    }

    backup_dir=$TEST_TMP/backup
    mkdir -p "$backup_dir"
    # Contenido con claves que NO parecen secretos: el respaldo tiene que
    # conservar cualquier valor afinado a mano, no solo credenciales, y asi
    # el fixture no dispara detect-secrets. Los puntos del valor de
    # ENCRYPTION_KEY son a proposito: detect-secrets calcula la entropia de la
    # cadena ENTERA entre comillas sobre el alfabeto base64, y un valor solo
    # con letras y guiones supera el limite de 4.5. Un punto queda fuera de ese
    # alfabeto y corta el match, en vez de depender del umbral.
    printf 'FTP_OBS_HOST=afinado-a-mano\nLDAP_SERVER_URI=ldap://dc.example.cu:389\n' \
        > "$backup_dir/.env"
    printf 'ENCRYPTION_KEY=clave.falsa.de.prueba\n' > "$backup_dir/encryption.env"
    chmod 0600 "$backup_dir/.env" "$backup_dir/encryption.env"
    cp "$backup_dir/.env" "$TEST_TMP/esperado.env"

    out=$(run_backup "$backup_dir" 0 2>&1); rc=$?
    assert_eq 'respaldo: sale 0' 0 "$rc"

    env_bak=$(find "$backup_dir" -maxdepth 1 -name '.env.bak.*' -print -quit)
    assert_file 'respaldo: deja el .env respaldado' "$env_bak"
    if [ -n "$env_bak" ]; then
        assert_eq 'respaldo: el .bak tiene el contenido original' \
            "$(cat "$TEST_TMP/esperado.env")" "$(cat "$env_bak")"
        assert_eq 'respaldo: el .bak conserva el modo 600' 600 "$(stat -c %a "$env_bak")"
    fi

    enc_bak=$(find "$backup_dir" -maxdepth 1 -name 'encryption.env.bak.*' -print -quit)
    assert_file 'respaldo: deja el encryption.env respaldado' "$enc_bak"

    assert_no_file 'respaldo: borra el .env original' "$backup_dir/.env"
    assert_no_file 'respaldo: borra el encryption.env original' "$backup_dir/encryption.env"

    # --dry-run informa y no toca nada.
    dry_dir=$TEST_TMP/backup-dry
    mkdir -p "$dry_dir"
    printf 'FTP_OBS_HOST=afinado-a-mano\n' > "$dry_dir/.env"
    printf 'ENCRYPTION_KEY=clave.falsa.de.prueba\n' > "$dry_dir/encryption.env"

    out=$(run_backup "$dry_dir" 1 2>&1); rc=$?
    assert_eq 'respaldo --dry-run: sale 0' 0 "$rc"
    assert_contains 'respaldo --dry-run: muestra el cp' 'cp -p' "$out"
    assert_file 'respaldo --dry-run: no borra el .env' "$dry_dir/.env"
    assert_eq 'respaldo --dry-run: no crea .bak' 0 \
        "$(find "$dry_dir" -maxdepth 1 -name '.env.bak.*' | wc -l)"
fi

# ---------------------------------------------------------------------------
# Commit desplegado (/etc/webcmp/deployed)
# ---------------------------------------------------------------------------
#
# El panel lo lee en cada visita al dashboard
# (apps/dashboard/views/dashboard/update.py): `.strip()` + `^[0-9a-f]{40}$`, y
# espera 0644. Un archivo con otro formato deja el aviso de actualizacion muerto
# sin decir por que.

if STATE_BLOCK=$(require_block deployed-state "$INSTALL_SH"); then
    # `runuser` es lo unico que no se puede probar sin root: se reemplaza por
    # una funcion que descarta `-u <usuario> --` y ejecuta el resto. Se prueba la
    # logica del bloque (que escriba, con que formato y con que permisos), no el
    # cambio de usuario.
    run_state() {  # <repo> <state_file> <gunicorn> <dry_run>
        (
            set -uo pipefail
            warn() { printf 'AVISO: %s\n' "$*" >&2; }
            runuser() { shift 3; "$@"; }
            APP_DIR=$1
            DEPLOYED_STATE=$2
            GUNICORN_ENABLED=$3
            DRY_RUN=$4
            SERVICE_USER=webcmp
            eval "$STATE_BLOCK"
        )
    }

    state_repo=$TEST_TMP/repo
    git init -q "$state_repo"
    git -C "$state_repo" -c user.email=test@example.com -c user.name=test \
        commit -q --allow-empty -m inicial
    state_sha=$(git -C "$state_repo" rev-parse HEAD)

    state_file=$TEST_TMP/deployed
    out=$(run_state "$state_repo" "$state_file" 1 0 2>&1); rc=$?
    assert_eq 'estado: sale 0 con el sitio sirviendo' 0 "$rc"
    assert_file 'estado: escribe el archivo' "$state_file"
    if [ -f "$state_file" ]; then
        assert_eq 'estado: contiene el HEAD del checkout' "$state_sha" "$(cat "$state_file")"
        assert_eq 'estado: una sola linea, con salto final' 1 "$(wc -l < "$state_file")"
        assert_eq 'estado: modo 0644' 644 "$(stat -c %a "$state_file")"
    fi
    assert_no_file 'estado: no deja el .tmp' "$state_file.tmp"

    state_off=$TEST_TMP/deployed-off
    out=$(run_state "$state_repo" "$state_off" 0 0 2>&1); rc=$?
    assert_eq 'estado: sale 0 con el sitio caido' 0 "$rc"
    assert_no_file 'estado: no escribe si Gunicorn no quedo activo' "$state_off"
    assert_contains 'estado: dice por que no lo escribe' 'no quedo sirviendo' "$out"

    state_dry=$TEST_TMP/deployed-dry
    out=$(run_state "$state_repo" "$state_dry" 1 1 2>&1); rc=$?
    assert_eq 'estado --dry-run: sale 0' 0 "$rc"
    assert_no_file 'estado --dry-run: no escribe' "$state_dry"
    assert_contains 'estado --dry-run: muestra el comando' '[dry-run]' "$out"
fi
