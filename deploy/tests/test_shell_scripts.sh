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

# El deploy ya no entra por SSH, asi que el instalador no puede generar un
# keypair ni escribir un authorized_keys. Si alguna de las dos cosas vuelve, es
# un secreto mas viajando dentro de la imagen (que se entrega como salva a un
# proveedor que prohibe SSH) y una privada que hay que rotar en cada copia.
#
# Se inspecciona el FUENTE y no la salida de `--help`: estas rutas solo se
# ejecutan como root, al final de la instalacion.
install_src=$(cat "$INSTALL_SH")
assert_not_contains 'install.sh no genera keypairs SSH' 'ssh-keygen' "$install_src"
assert_not_contains 'install.sh no escribe authorized_keys' 'authorized_keys' "$install_src"
assert_not_contains 'install.sh no imprime ssh-keyscan' 'ssh-keyscan' "$install_src"
assert_contains 'install.sh explica que el boton no usa SSH' \
    'NO usa SSH' "$install_src"
assert_contains 'install.sh nombra la salida HTTPS que si necesita' \
    'salida HTTPS a github.com' "$install_src"
assert_contains 'install.sh deja sshd a decision del operador' \
    'sudo systemctl disable --now ssh' "$install_src"

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

# El instalador ya no genera keypair. Un texto que lo sigue prometiendo manda al
# operador a buscar un archivo que no existe, y en el README raiz a cargar un
# secret de GitHub que ningun workflow lee.
assert_not_contains 'install.sh --help no promete un keypair' 'su keypair' "$help_out"
assert_not_contains 'README-deploy no promete un keypair' \
    'su keypair' "$(cat "$README_DEPLOY")"
assert_not_contains 'README raiz no manda a cargar el .pub del keypair' \
    'keypair que creó install.sh' "$(cat "$REPO_ROOT/README.md")"
