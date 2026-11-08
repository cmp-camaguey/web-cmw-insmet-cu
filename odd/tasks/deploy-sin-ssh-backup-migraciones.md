# Deploy sin SSH, respaldo antes de migrar y gate de migraciones

Objetivo: adaptar el despliegue al proveedor que prohibe SSH al contenedor, respaldar la
base antes de aplicar migraciones y bloquear en el merge `dev -> main` las migraciones
que puedan perder datos.
Branch: `feat/deploy-sin-ssh-backup-migraciones`.
Delivery: single-pr.
Test runner: `bash deploy/tests/run.sh`, `python manage.py test apps.core` y
`python scripts/check_migrations.py --base origin/main --head HEAD`.

## Specs

- **S1** — "ajusta el install para no ssh". `deploy/install.sh` deja de generar el par
  `/etc/webcmp/deploy_key*` y deja de escribir `authorized_keys`, y deja de imprimir
  instrucciones para cargar `DEPLOY_SSH_KEY` / `DEPLOY_SSH_KNOWN_HOSTS`. Se conservan el
  usuario `deploy`, el sudoers y `/usr/local/sbin/webcmp-deploy` porque el boton
  "Actualizar" del panel los usa. La actualizacion es pull: el servidor resuelve
  `refs/heads/main` y aplica el commit localmente. No se deshabilita sshd
  automaticamente: el instalador puede estar corriendo sobre esa sesion. Se documenta.
- **S2** — "backup para las actualizaciones con migraciones que afecten a la db". Antes
  del `migrate`, si `migrate --check` reporta pendientes, `webcmp-deploy` toma
  `pg_dump --format=custom` en `/var/backups/webcmp/db/` (root:root 0700/0600), conserva
  `DB_BACKUP_KEEP` (default 10), imprime el comando de restauracion y **aborta el deploy
  si el respaldo falla**. Sin migraciones pendientes no se respalda.
- **S3** — "verificar la migraciones a la hora de merge de dev a main que no produscan
  perdidas de datos corrupcion de la deb". Un job de CI en PRs con base `main` corre
  `scripts/check_migrations.py`: falla si el PR agrega operaciones destructivas o no
  verificables, o si modifica/borra/renombra una migracion ya publicada. Escape
  auditado: `# data-safety: reviewed: <motivo>`.
- **S4** — Help, README y sudoers dejan de prometer una clave SSH de despliegue.
- **S5** — Tests: `deploy/tests/` actualizado y `apps/core/tests/test_check_migrations_script.py`
  nuevo. `make test-deploy` verde.

## Tasks

| ID | Specs | Ruta | Estado |
| --- | --- | --- | --- |
| T1 | S1, S4 | inline | hecho: no queda `ssh-keygen`, `authorized_keys` ni `ssh-keyscan` en `install.sh` |
| T2 | S2, S4 | inline | hecho: respaldo en `deploy.sh`, `postgresql-client` en `install.sh`, claves en el `deploy.env` generado |
| T3 | S3, S5 | inline | hecho: `scripts/check_migrations.py` + 16 tests |
| T4 | S3 | inline | hecho: job `Migrations safety (dev -> main)` en `ci.yml` |
| T5 | S5 | inline | hecho: README raiz, `README-deploy`, `--help`, sudoers, AGENTS.md y `deploy.env.example` al dia |

## Log

### L1 — pedido original

> ajusta el install para no ssh y backup para las actualizaciones con migraciones que afecten a la db y verificar la migraciones a la hora de merge de dev a main que no produscan perdidas de datos corrupcion de la deb etc.

Contexto: el contenedor se entrega como salva a un proveedor que prohibe SSH. La
actualizacion real es el boton "Actualizar" del panel (`deploy/autodeploy.sh`), que
resuelve `refs/heads/main` por HTTPS y llama a `webcmp-deploy` por sudo local.

### L2 — implementacion y evidencia

**S1.** `deploy/install.sh`: se fueron `DEPLOY_SSH_KEY`, `DEPLOY_SSH_KEY_CREATED`, el
`ssh-keygen`, el bloque de `authorized_keys` y el mensaje que pedia cargar la privada en
GitHub. El usuario `deploy`, el sudoers y `/usr/local/sbin/webcmp-deploy` se conservan
porque el boton los usa. El bloque "QUE FALTA" ahora explica la salida HTTPS que si hace
falta y deja `systemctl disable --now ssh` como decision del operador, no del instalador
(podria cortar la sesion en la que corre).

**S2.** `deploy/deploy.sh`: `pending_migrations()` (`migrate --check` invertido),
`db_dump()` (pg_dump `--format=custom` como root, `-B` para no dejar `__pycache__` de
root en el checkout, conexion tomada de `settings.DATABASES`) y `prune_db_backups()`
(conserva `DB_BACKUP_KEEP`, default 10). El paso va **despues** de los dos gates y
**antes** de `migrate`, aborta si el dump falla, borra el archivo a medias, y el rollback
nombra el dump. `--check` valida `pg_dump` y el directorio. `install.sh` instala
`postgresql-client` y escribe `DB_BACKUP_DIR`/`DB_BACKUP_KEEP` en el `deploy.env`.

**S3.** `scripts/check_migrations.py` (stdlib puro, no importa Django): analiza el AST de
las migraciones nuevas y clasifica operaciones. Bloquea `RemoveField`, `DeleteModel`,
`RunSQL`, `RunPython`, `SeparateDatabaseAndState` y cualquier operacion desconocida;
avisa sobre el resto. Historia ya publicada (modificada, borrada o renombrada) bloquea
**sin** escape. El escape auditado es `# data-safety: reviewed: <motivo>` y un motivo
vacio no cuenta. Sale 0/1/2 (2 = no pudo analizar, tambien rojo).

**S4.** `deploy/README-deploy.md`, `deploy/sudoers/webcmp-deploy` y `AGENTS.md`: fuera el
keypair y los secrets de deploy; adentro la conectividad de salida, el respaldo, el gate
y el noveno required check de `main`.

**T4.** Job `Migrations safety (dev -> main)` en `.github/workflows/ci.yml`, con
`fetch-depth: 0` y fetch explicito de la base. Sin `setup-python` ni `pip install`: el
script es stdlib.

**Verificacion observada**

- `bash deploy/tests/run.sh` -> `98 ok, 0 falla(s)` (incluye
  `test_deploy_sh_backup.sh`, nuevo, y las aserciones de que `install.sh` ya no genera
  claves).
- `python manage.py test apps.core.tests.test_check_migrations_script` -> 16 tests OK.
- `python manage.py test apps.core.tests.test_generate_env_script
  apps.core.tests.test_install_script_booleans
  apps.core.tests.test_check_migrations_script` -> 32 tests OK.
- `python manage.py check` -> sin problemas.
- **Pendiente / no ejecutado**: `python manage.py test apps.core` completo falla en este
  entorno antes de correr un solo test con `FATAL: password authentication failed for
  user "webcmp_dev"` (el `DB_PASS` del `.env` local no coincide con el rol de PostgreSQL).
  Es del entorno, no del cambio: ningun archivo tocado lee esa configuracion.
- `deploy/deploy.env.example` se edito **por shell**: la herramienta deniega escritura
  sobre rutas `.env*` y el usuario autorizo explicitamente la excepcion. El archivo se
  valida con `bash -n` y con un `source` en subshell (no tiene secretos: es una
  plantilla versionada).

### L3 — correccion tras la verificacion independiente

La verificacion del diff completo levanto dos defectos que la implementacion habia
dejado pasar, y los dos son de la misma clase: el texto prometia algo que el codigo
ya no hace.

1. **S4 estaba a medias.** `install.sh --help` (y la sinopsis del encabezado),
   `deploy/README-deploy.md` y el `README.md` raiz seguian hablando de "su keypair",
   de cargar `DEPLOY_SSH_KEY` y de `ssh-keyscan`, cuando el instalador ya no genera
   ninguna clave. Se corrigieron los tres, y el bloque 8 del README raiz se reescribio
   entero al modelo pull: ya no hay secrets de GitHub ni `deploy-production.yml` como
   camino vigente. El orden del deploy documentado ahora incluye el respaldo.
2. **La restauracion documentada no podia leer el dump.** Decia
   `sudo -u webcmp pg_restore`, pero el dump es `root:root 0600` dentro de un
   directorio `0700`, que es justamente lo que el mismo parrafo explica. Se cambio a
   `sudo pg_restore -h <host> -U <usuario> -W`, con el sitio parado.

Ademas, dos avisos del verificador se resolvieron como bloqueo:

- **El escape tapaba lo que el gate no puede verificar.** `# data-safety: reviewed:`
  convertia en aviso una migracion con sintaxis rota o una operacion desconocida, y
  el script salia 0 — contra su propio docstring. Ahora el escape solo cubre
  operaciones conocidas de `BLOCKING_OPERATIONS`; lo que no se puede leer o
  clasificar bloquea siempre.
- Un renombre de archivo que no es migracion ya no se reporta como historia.

**Pruebas nuevas** (5 en `test_check_migrations_script.py`, 3 aserciones de shell en
`test_deploy_sh_backup.sh` y `test_shell_scripts.sh`). Para el cambio del escape se
verifico RED -> GREEN contra el script anterior (`git show 51035657`): el mismo repo
de prueba con marcador y sintaxis rota daba `rc=0` antes y `rc=1` despues.

**Evidencia nueva:** `bash deploy/tests/run.sh` -> `106 ok, 0 falla(s)`;
`test_check_migrations_script` -> 21 tests OK; `ruff check` y `ruff format --check`
limpios; `detect-secrets` limpio; `manage.py check` sin problemas.

**Advertencias que quedaron sin cambiar a proposito**

- Si `migrate` falla a mitad, `migrated=1` no llega a ponerse y el rollback revierte
  codigo sin nombrar el dump. El dump igual se imprime en el paso 5, que corre
  siempre antes de migrar.
