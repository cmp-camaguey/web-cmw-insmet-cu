# Helpers de las pruebas de los scripts de deploy. Se hace `source` desde run.sh.
#
# Sin dependencias mas alla de lo que el propio instalador ya exige: bash,
# coreutils, git, flock y un Python con `cryptography` (requirements/prod.txt).
#
# Contrato: cada `assert_*` suma a PASS o a FAIL y nunca aborta la corrida. Un
# test que no corre porque el anterior fallo es un test que no existe.

PASS=0
FAIL=0
FAILED_NAMES=()

ok() {
    PASS=$((PASS + 1))
    printf '  ok     %s\n' "$1"
}

fail() {
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$1")
    printf '  FALLA  %s\n' "$1"
    printf '         esperado: %s\n' "$2"
    printf '         obtenido: %s\n' "$3"
}

assert_eq() {
    if [ "$2" = "$3" ]; then
        ok "$1"
    else
        fail "$1" "$2" "$3"
    fi
}

assert_contains() {
    case "$3" in
        *"$2"*) ok "$1" ;;
        *) fail "$1" "que contenga '$2'" "$3" ;;
    esac
}

assert_not_contains() {
    case "$3" in
        *"$2"*) fail "$1" "que NO contenga '$2'" "$3" ;;
        *) ok "$1" ;;
    esac
}

assert_file() {
    if [ -f "$2" ]; then ok "$1"; else fail "$1" "que exista $2" 'no existe'; fi
}

assert_no_file() {
    if [ -e "$2" ]; then fail "$1" "que NO exista $2" 'existe'; else ok "$1"; fi
}

# extract_block <nombre> <archivo>
#
# Imprime las lineas entre `# >>> TEST-BLOCK: <nombre>` y
# `# <<< TEST-BLOCK: <nombre>`. Los marcadores existen para que las pruebas no
# dependan de numeros de linea: mover el bloque dentro del script no las rompe,
# borrar el marcador si (y eso se ve como una falla, no como un test vacio).
extract_block() {
    local name=$1 file=$2
    awk -v name="$name" '
        index($0, "# >>> TEST-BLOCK: " name) { inside = 1; next }
        index($0, "# <<< TEST-BLOCK: " name) { inside = 0; next }
        inside
    ' "$file"
}

# require_block <nombre> <archivo>
#
# Igual que extract_block, pero una falla cuenta: un bloque que desaparecio no
# puede pasar como "cero casos que probar".
require_block() {
    local name=$1 file=$2 body
    body=$(extract_block "$name" "$file")
    if [ -z "$body" ]; then
        fail "bloque TEST-BLOCK '$name' presente en ${file#"$REPO_ROOT"/}" \
            'encontrar los dos marcadores' 'no se encontro el bloque'
        return 1
    fi
    printf '%s\n' "$body"
}
