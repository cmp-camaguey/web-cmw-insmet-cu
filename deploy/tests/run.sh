#!/usr/bin/env bash
# Pruebas de los scripts de deploy (`deploy/install.sh`, `deploy/deploy.sh`,
# `deploy/autodeploy.sh`, `run_huey.sh`, `.githooks/pre-commit`).
#
# Que cubre: la sintaxis de todos los scripts y la logica que se puede aislar
# del sistema operativo — el validador del par SECRET_KEY/ENCRYPTION_KEY, el
# lock compartido con deploy.sh, la escritura del commit desplegado y el respaldo
# previo a regenerar el .env. Los bloques se extraen del propio install.sh entre
# marcadores `# >>> TEST-BLOCK: <nombre>`, asi que se prueba el codigo que se
# ejecuta, no una copia que puede quedar desactualizada.
#
# Que NO cubre, y conviene decirlo: no corre el instalador. Necesita root,
# systemd, apt, PostgreSQL y Nginx, y muta la maquina; ese camino se prueba en un
# servidor, no en CI. Tampoco corre `deploy.sh` de verdad: hace `git checkout`,
# `pip install`, `migrate` y `systemctl restart` sobre el host.
#
# Uso:
#   bash deploy/tests/run.sh        # o: make test-deploy
set -uo pipefail

TESTS_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$TESTS_DIR/../.." && pwd)
export TESTS_DIR REPO_ROOT

# shellcheck source=lib.sh
. "$TESTS_DIR/lib.sh"

for test_file in "$TESTS_DIR"/test_*.sh; do
    printf '\n== %s ==\n' "$(basename "$test_file")"
    # shellcheck source=/dev/null
    . "$test_file"
done

printf '\n%d ok, %d falla(s)\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'fallaron:\n'
    printf '  - %s\n' "${FAILED_NAMES[@]}"
    exit 1
fi
