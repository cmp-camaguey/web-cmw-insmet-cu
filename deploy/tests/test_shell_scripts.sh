# Sintaxis de todos los scripts y contrato de linea de comandos de los que se
# pueden invocar sin privilegios. Barato, y atrapa lo que `bash -n` atrapa.

INSTALL_SH=$REPO_ROOT/deploy/install.sh
DEPLOY_SH=$REPO_ROOT/deploy/deploy.sh

SCRIPTS=(
    deploy/install.sh
    deploy/deploy.sh
    deploy/autodeploy.sh
    deploy/tests/run.sh
    deploy/tests/lib.sh
    deploy/tests/test_install_sh_blocks.sh
    deploy/tests/test_shell_scripts.sh
    run_huey.sh
    .githooks/pre-commit
)

for script in "${SCRIPTS[@]}"; do
    if syntax_error=$(bash -n "$REPO_ROOT/$script" 2>&1); then
        ok "bash -n $script"
    else
        fail "bash -n $script" 'que parsee' "$syntax_error"
    fi
done

# `--help` es el unico camino que promete salir 0 sin root y sin TTY: si se
# rompe, el operador se queda sin la unica forma de mirar el contrato antes de
# correr nada.
help_out=$(bash "$INSTALL_SH" --help 2>&1)
help_rc=$?
assert_eq 'install.sh --help sale 0' 0 "$help_rc"
assert_contains 'install.sh --help imprime el uso' 'Variables de entorno' "$help_out"

# Una opcion desconocida no se ignora en silencio: el instalador muta el host y
# un typo en un flag no puede terminar en una corrida real.
bad_out=$(bash "$INSTALL_SH" --no-existe 2>&1)
bad_rc=$?
assert_eq 'install.sh rechaza una opcion desconocida' 1 "$bad_rc"
assert_contains 'install.sh nombra la opcion rechazada' '--no-existe' "$bad_out"

deploy_help=$(bash "$DEPLOY_SH" --help 2>&1)
deploy_help_rc=$?
assert_eq 'deploy.sh --help sale 0' 0 "$deploy_help_rc"
assert_contains 'deploy.sh --help explica el uso' 'webcmp-deploy <commit-sha>' "$deploy_help"

# El preflight de root va ANTES de cualquier mutacion. Se comprueba el codigo y
# el mensaje, no solo "falla": un exit 1 por una variable sin definir tambien
# seria 1 y no probaria nada.
if [ "$(id -u)" -eq 0 ]; then
    printf '  omitido  install.sh y deploy.sh sin root: la suite corre como root\n'
else
    noroot_out=$(bash "$INSTALL_SH" --dry-run 2>&1)
    noroot_rc=$?
    assert_eq 'install.sh sin root sale 1' 1 "$noroot_rc"
    assert_contains 'install.sh sin root lo explica' 'como root' "$noroot_out"

    deploy_noroot_out=$(bash "$DEPLOY_SH" 2>&1)
    deploy_noroot_rc=$?
    assert_eq 'deploy.sh sin root sale 1' 1 "$deploy_noroot_rc"
    assert_contains 'deploy.sh sin root lo explica' 'necesita root' "$deploy_noroot_out"
fi

# El bloque "QUE FALTA" imprime el comando con el que el operador arma
# DEPLOY_SSH_KNOWN_HOSTS. Tenia un `sed 's/^/|1 h=|/'` de mas que prefijaba cada
# linea: ssh no parsea ese archivo y el deploy falla con "Host key verification
# failed", un error que no dice "el secret esta mal formado".
#
# No se ejecuta ssh-keyscan: la suite no puede depender de que el puerto 22 de un
# host externo responda. Se fija la forma del comando, que es lo que se rompio.
known_hosts_line=$(grep -F 'ssh-keyscan -p 22 -H <host>' "$INSTALL_SH")
assert_contains 'install.sh imprime el ssh-keyscan de DEPLOY_SSH_KNOWN_HOSTS' \
    '2>/dev/null > known_hosts' "$known_hosts_line"
assert_not_contains 'install.sh no prefija el known_hosts con el sed roto' \
    '|1 h=|' "$known_hosts_line"

# El README documentaba la corrida interactiva como `curl ... | sudo bash -s`,
# que el propio instalador rechaza: en el pipe el stdin del script es la
# tuberia, no hay TTY, y el guard de `-t 0` aborta ANTES de tocar nada. La
# instruccion contradecia al codigo y el operador lo descubria corriendo.
#
# Solo dos caminos sobreviven al pipe: `--help` (sale antes del guard) y
# `--non-interactive` (no hay preguntas que responder). Cualquier otro bloque
# ```bash del README que mande la corrida por pipe es el bug de vuelta.
README_DEPLOY=$REPO_ROOT/deploy/README-deploy.md
readme_pipe=$(awk '
    /^```bash/ { inside = 1; next }
    /^```/ { inside = 0; next }
    inside && /bash -s/ { print }
' "$README_DEPLOY")
bad_pipe=''
while IFS= read -r line; do
    [ -n "$line" ] || continue
    case "$line" in
        *--help*|*--non-interactive*) ;;
        *) bad_pipe=$line ;;
    esac
done <<<"$readme_pipe"
assert_eq 'README-deploy no manda la corrida interactiva por pipe' '' "$bad_pipe"
assert_contains 'README-deploy descarga el instalador antes de ejecutarlo' \
    'curl -fsSL -o /tmp/install.sh' "$(cat "$README_DEPLOY")"

# La sinopsis de `--help` ofrecia el pipe para CUALQUIER opcion, incluida la
# corrida interactiva que ese mismo pipe rompe.
help_out=$(bash "$INSTALL_SH" --help 2>&1)
assert_not_contains 'install.sh --help no ofrece el pipe para toda opcion' \
    '| sudo bash -s -- [opciones]' "$help_out"
assert_contains 'install.sh --help muestra la descarga antes de ejecutar' \
    'sudo bash install.sh [opciones]' "$help_out"

# El aviso de `--dry-run` decia "<generada, escrita en el .env, no impresa>"
# para TODA clave generada. Es verdad para DB_PASS y mentira para
# SUPERUSER_PASSWORD, que no se escribe en ningun archivo (solo queda el hash en
# la base). Es el mismo defecto que ya se corrigio en `recover_env_password` y
# que dejo a un operador buscando una clave en el unico lugar donde no puede
# estar. Dos mensajes distintos, uno por destino.
generated_msgs=$(grep -c '\[dry-run\] %s=<generada' "$INSTALL_SH")
assert_eq 'install.sh distingue el destino de cada secreto generado' 2 "$generated_msgs"
install_src=$(cat "$INSTALL_SH")
assert_contains 'install.sh condiciona el aviso a la clave generada' \
    'if [ "$key" = SUPERUSER_PASSWORD ]' "$install_src"
assert_contains 'install.sh aclara que la clave del superusuario no se guarda' \
    'no se guarda en ningun archivo' "$install_src"
