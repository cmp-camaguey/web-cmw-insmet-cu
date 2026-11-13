# AGENTS.md — Centro Meteorológico Provincial Camagüey

## Stack

Django 5.2 + DRF + drf-spectacular (OpenAPI), Python 3.14 (CI y ruff apuntan a `py314`), entorno virtual en `.venv/`.

- **Base de datos**: PostgreSQL en los tres perfiles (dev, test y prod). `DB_ENGINE=sqlite3` se rechaza al arrancar.
- **UI**: Tabler (Bootstrap 5) vía Django Templates. Raíz `templates/` con `layouts/` e `includes/`; páginas en `apps/<app>/templates/pages/`.
- **Frontend**: JS vanilla vendoreado. Sin Node.js, sin bundlers, sin paso de build. Tabler en `static/dist/css/` y `static/dist/js/`; el resto de librerías en `static/dist/libs/`.
- **Apps**: todas dentro de `apps/`. Importar `from apps.commercial.models import ...`.

## Misión y límites duros

**Misión:** portal público meteorológico + dashboard administrativo (pronósticos, avisos, servicios comerciales, facturación, publicaciones) + API REST para terceros del Centro Meteorológico Provincial Camagüey.

**No es:** una SPA, una red social o foro, ni un proyecto OSS genérico.

Límites no negociables:

- No commitear secretos ni datos: `.env`, `media/`, `db.sqlite3`.
- No introducir npm, bundlers ni frameworks JS.
- No quitar `FileHandlerMixin` de modelos con `FileField`/`ImageField`.
- No cambiar `default_permissions = ()` sin redefinir los 4 permisos custom.
- No saltarse el flujo OpenSpec para features.

## Comandos

```bash
source .venv/bin/activate
pip install -r requirements/dev.txt
python manage.py makemigrations migrate
python manage.py collectstatic --link --no-input
python manage.py add_stations_data
python manage.py createsuperuser
python manage.py runserver                    # desarrollo
python manage.py test apps.<app>              # label completo: apps.meteo
python manage.py test                         # suite completa, antes de commit
make test-deploy                              # pruebas de deploy/tests/
PRODUCTION=true python manage.py runserver    # perfil producción en local
./run_huey.sh &                               # worker de correos/PDF
```

La base local se prepara una vez: ver [`docs/1-instalacion.md`](docs/1-instalacion.md#2-base-de-datos).

## Dependencias — un archivo por entorno

Cada archivo se instala con un solo `pip install -r`. La tabla de qué trae cada uno
está en [`docs/1-instalacion.md`](docs/1-instalacion.md#dependencias-por-entorno).

`django-debug-toolbar` va en `dev.txt` y en `test.txt`, nunca en `base.txt`: es tooling de desarrollo y `apps/core/tests/test_debug_toolbar.py` exige que esté instalado.

## Pre-commit

Los hooks viven en `.pre-commit-config.yaml` y se activan con `core.hooksPath`:

```bash
make setup                             # venv + deps + hooks
make hooks                             # solo activar el hook
.venv/bin/pre-commit run --all-files   # correr todo una vez
```

- `pre-commit install` y `core.hooksPath` son excluyentes: `core.hooksPath` hace que git ignore `.git/hooks`. `pre-commit install --install-hooks` sirve solo para descargar los entornos, no para activar el hook.
- Los pasos personales de cada máquina (revisores de código, chequeos locales) van en `.githooks/pre-commit.local`: `.githooks/pre-commit` lo ejecuta si existe y el archivo está en `.gitignore`, así el proyecto no depende de que esté instalado.
- Falso positivo nuevo de `detect-secrets`: regenerar el baseline y commitear `.secrets.baseline`.

```bash
detect-secrets scan --exclude-files "static/|staticfiles/|.*\.min\.js$|.*\.map$|\.venv/" > .secrets.baseline
```

## Flujo de ramas (`dev` → `main`)

| Rama | Rol | Reglas |
|---|---|---|
| `main` | Producción | `Protect main`: aprobación humana obligatoria. El merge dispara CI y deploy |
| `dev` | Integración | `Protect dev`: sin force push, sin borrado, merge solo con los required checks en verde |

1. Rama corta desde `dev`: `feat/nnn-descripcion`, `fix/nnn-descripcion`, `chore/descripcion`.
2. PR a `dev` con **squash merge**. Sin aprobación: el gate son los status checks.
3. Al fusionarse, `cleanup-branch.yml` borra la rama corta.
4. Promoción por lote: PR `dev` → `main` con **merge commit**, nunca squash.
5. La promoción necesita una aprobación de `@orl92` o `@yoelvismr` (`CODEOWNERS` + `require_code_owner_review`). Es una por release.
6. Etiquetar el merge del release: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.
7. **Después de cada release o hotfix, PR `main` → `dev`.** Sin ese paso la siguiente promoción sale `BEHIND` aunque el contenido sea idéntico.

Un hotfix sale de `main` y vuelve a `dev` igual que un release.

## CI

- **PR**: `dorny/paths-filter` detecta las apps cambiadas (`apps/<app>/**`) y corre solo esos labels.
- **Push a `main` y merges**: suite completa.
- **Cambios globales** (`config/**`, `manage.py`, `requirements/**`, `pyproject.toml`, `.github/**`, `templates/**`, `static/**`): suite completa.
- Los labels van como ruta completa (`apps.meteo`, no `meteo`).

Required checks en las dos ramas: *Detect affected apps*, *Tests (Django)*, *Lint (Ruff)*, *Templates (djlint)*, *Pre-commit hooks*, *Bandit SAST*, *Security (pip-audit + migrations)*, *Deploy check (check --deploy)* y *Migrations safety (dev -> main)*. Renombrar o borrar un job deja el check sin reportar y bloquea el merge.

`Migrations safety (dev -> main)` corre en todo PR y bloquea migraciones destructivas o cambios a migraciones publicadas. Escape auditado: `# data-safety: reviewed: <motivo>` en el archivo.

`strict_required_status_checks_policy`: `true` en `main`, `false` en `dev`.

Dependabot apunta a `dev` en los tres ecosistemas (`target-branch: dev`).

## Trabajo en paralelo

`git commit` usa la rama checked out, no la que crees estar usando.

- Si vas a escribir mientras otro trabaja en la misma copia, usa un worktree: `git worktree add ../<repo>-worktrees/<nombre> -b <tipo>/<descripcion> dev`.
- Antes de commitear: `git branch --show-current`.
- No cambies de rama en un checkout compartido con el árbol sucio.
- Al terminar: `git worktree remove <ruta>`.
- En un worktree no hay `.venv/`; para commitear, presta el del checkout principal: `PATH=<checkout-principal>/.venv/bin:$PATH git commit ...`.
- Una rama mergeada por squash no es ancestro de `dev`: consulta el PR con `gh pr list --head <rama> --state all`.

## Apps + Modelos

| App | Responsabilidad |
|---|---|
| `config/` | settings, urls root, wsgi/asgi, email backend, Huey |
| `apps/api/` | REST endpoints: `/api/doc/`, `/api/redoc/`, stations, observations, forecasts |
| `apps/user_auth/` | Profile, login/logout, users, groups, LDAP auth (`LDAP3Backend`), password management |
| `apps/core/` | FileHandlerMixin, SoftDeleteModel, utils, error views, CompanySettings, SiteConfiguration, EmailRecipientList/Recipient, templatetags, context_processors, middleware, mail_send |
| `apps/dashboard/` | Solo DashboardView principal (vista agregada del panel) |
| `apps/commercial/` | Customer, Service, ServiceSubscription, Invoice/InvoiceItem, Contract, Certificate — servicios comerciales y facturación |
| `apps/meteo/` | Forecasts/ForecastRegions/ForecastExtendedDay, Warning (con warning_type), WeatherReport (today/tomorrow/commentary/note), Province, Town, Station |
| `apps/home/` | Páginas públicas: tiempo, modelos, satélites, servicios, institución |
| `apps/publications/` | ScientificPublication, Author — publicaciones científicas |
| `openspec/changes/` | SDD features (OpenSpec) |

## Convenciones

- **UI**: exclusivamente Tabler.io (Bootstrap 5).
- **Permisos**: `default_permissions = ()` + 4 personalizados: `view_*`, `add_*`, `change_*`, `delete_*` (en español). No existen por defecto.
- **Middleware**: `CheckUserProfileMiddleware` → `MaintenanceModeMiddleware`. El segundo deja pasar `/login/` y `/logout/` para que un usuario con sesión abierta pueda cerrarla durante el mantenimiento.
- **Auth**: LDAP opcional (`LDAP3Backend`) → fallback `ModelBackend`. `Profile` se crea por señal `post_save`; los usuarios LDAP llevan `is_ldap=True`.
- **UUIDs** en URLs de modelos expuestos (no `pk`).
- **FileHandlerMixin** obligatorio en todo modelo con `FileField`/`ImageField`.
- **Soft delete** en modelos de negocio con datos sensibles: Customer, Service, ServiceSubscription, Invoice, Contract, Certificate. No forzarlo en modelos auxiliares o transaccionales (InvoiceItem, ForecastRegions, EmailRecipient). Se consulta por el manager por defecto, que filtra `record_active=True`.
- **`Warning` se borra físicamente**: su protección es el permiso `delete_warning`, no `record_active`.
- **Paginación**: `paginate_by = 10` en vistas sin DataTables (incluye el catálogo comercial y Mis Servicios). Las vistas con DataTables no llevan `paginate_by`: paginan en el cliente.
- **Migrations** versionadas: `makemigrations` + commit en el mismo PR. El gate es `makemigrations --check --dry-run`.
- **Estáticos**: `static/` en dev, `staticfiles/` en prod con WhiteNoise.
- **Idioma**: español (`es-MX`, `America/Havana`).
- **Iconos meteorológicos**: PNG en `static/dist/img/weather_icon/`.
- **URLs**: toda app con URLs define `app_name` y los names `list`, `create`, `detail`, `update`, `delete`, `pdf`. Templates con `{% url 'app_name:name' %}`, vistas con `reverse_lazy('app_name:name')`.
- **Templates**: indentación de 2 espacios, formateados con djlint (`profile = "django"`, config en `pyproject.toml`). Los de `*/emails/` están excluidos (whitespace-sensitive): no reindentarlos.
- **Apps**: importar como `from apps.commercial.models import ...`, nunca `from commercial.models import ...`.

## Flujo de cambios (OpenSpec)

Los cambios activos viven en `openspec/changes/` como features OpenSpec. No se versiona historial de features previas.

1. `openspec/changes/NNN-nombre/proposal.md` (motivación + solución + criterios de aceptación)
2. `design.md` (plan técnico) + `tasks.md` (checkboxes `- [ ]`/`- [x]`) + `specs/NNN-nombre/spec.md` (requisitos delta)
3. Implementar un task a la vez
4. Si hay cambios de modelo: `python manage.py makemigrations`
5. Verificar: `python manage.py check && python manage.py test apps.<app>`; crear test si falta
6. Marcar tasks `[x]` y commitear (incluir número y nombre del cambio)
7. Al cerrar: promover los specs a `openspec/specs/` y archivar el directorio del cambio en `openspec/changes/archive/` (local, fuera de git)

Antes de codificar, define: tipo de cambio, apps afectadas, cambios de base de datos, URLs y permisos, templates y criterios de aceptación.

## Documentación

Los documentos describen el sistema que corre hoy. Cómo se llegó hasta acá lo guardan `git log` y los PRs; no hace falta escribirlo dos veces.

- Escribí para quien tiene que operar o modificar el sistema ahora: qué es, cómo se usa, qué rompe si se cambia.
- No narres decisiones ni alternativas descartadas. No expliques cómo era el código antes, ni qué bug hubo, ni en qué fecha.
- Sin referencias a cambios cerrados, a versiones anteriores del propio documento ni a las herramientas con las que se trabajó.
- Si un párrafo no cambia lo que el lector hace, sobra.
- Antes de agregar una sección, comprobá que no exista en otro archivo: enlazá en vez de repetir. Un documento que crece sin control deja de leerse.
- El historial de features cerradas no se versiona: los specs promovidos viven en `openspec/specs/` y el resto se archiva fuera de git.

Al borrar o mover un archivo o un directorio, buscá quién lo menciona antes de cerrar el cambio:

```bash
grep -rn '<ruta>' --include='*.py' --include='*.md' --include='*.yaml' . | grep -v '^./.venv'
```

## Checklist proyecto-específico

Ningún lint detecta estos items. Verificarlos siempre:

- [ ] `default_permissions = ()` + 4 permisos custom (view/add/change/delete) en español
- [ ] Kwarg `uuid` (no `pk`) en URLs de modelos con UUIDField
- [ ] FileHandlerMixin + `file_fields` definido si hay FileField/ImageField
- [ ] Soft delete: filtrar `record_active=True` (manager por defecto, no `all_objects`)
- [ ] Tareas/imports Huey registrados en `AppConfig.ready()`
- [ ] Migraciones versionadas y `makemigrations --check --dry-run` en verde

## Modelo de dominio (referencia rápida)

- **Forecasts**: 3 regiones (north/interior/south) × 3 períodos (mañana/tarde/noche) + 5 días extendido + datos astronómicos (luna, sol, UV). `ForecastRegions` y `ForecastExtendedDay` normalizados.
- **Warning**: modelo unificado con `warning_type` (early/tropical_cyclone/storm); `uuid`, `user`, `summary`, `file` (PDF), `valid_until`, `email_recipient_list`.
- **WeatherReport**: por tipo (today/tomorrow/commentary/note) con PDF y lista de correo.
- **Province / Town / Station**: geografía y estaciones meteorológicas.
- **Customer**: datos fiscales cubanos (REEUP, NIT, cuenta bancaria), `client_type` (natural/jurídica).
- **Service**: público o comercial; precio (CUP), código, PDF e imagen.
- **ServiceSubscription**: soft delete (`record_active`); estados `requested → pending → paid → expired`.
- **Invoice / InvoiceItem**: facturación con cálculo automático de importe.
- **Contract / Certificate**: servicios comerciales con PDF.
- **CompanySettings**: singleton (`pk=1`); datos fiscales de la empresa.
- **SiteConfiguration**: flag `maintenance_mode`.
- **Profile**: vinculado a User por señal `post_save`; `newsletter` sincronizado con `EmailRecipientList`.
- **ScientificPublication / Author**: publicaciones con coautores, ORCID, PDF.

## Layouts base (UI)

- `templates/layouts/base.html` (dashboard), `home.html` (público), `base-auth.html` (login), `form.html`, `list.html`, `maintenance.html`.
- Iconos: webfont Tabler (`<i class="icon ti ti-*">`).
- Modales: mecanismo nativo de Tabler (data API / `Bootstrap.Modal`).

## Tempus Dominus (date pickers)

- Los pickers se inicializan en `static/dist/js/tempus-init.js`, una sola vez en `DOMContentLoaded`, sobre los `[data-tempus]` presentes.
- `tempus-init.js` define sus globals (`window.pickerDateToIso`, `window.formatPickerDate`, `window.formatPickerDateTime`) y `window.tempusDominus._instances` dentro de `DOMContentLoaded`. Los scripts de plantilla corren antes: usan fallback local y difieren la suscripción al picker.
- No hay observer para nodos agregados tras la carga. Un `[data-tempus]` inyectado por JS se inicializa con `initPicker(input)`, con guarda anti-doble-init: `if (window.tempusDominus && window.tempusDominus._instances && window.tempusDominus._instances[input.id]) return;`

## Verificación antes de commit

Los hooks (`ruff`, `djlint`, `detect-secrets`) se configuran en `.githooks/pre-commit` y en el `Makefile`. Si `make setup` ya corrió, se ejecutan solos.

La verificación funcional se corre según lo que cambió:

| Cambio | Verificación |
|---|---|
| Código Python (modelos, vistas, permisos) | `python manage.py test` |
| Scripts shell (`deploy/`, `run_huey.sh`, hooks) | `make test-deploy` |
| `requirements/` con versiones cambiadas | `python manage.py test` |
| `requirements/` solo pines | `pip install --dry-run -r requirements/<env>.txt` |
| Solo workflows / Makefile / hooks | YAML parseable + `sh -n .githooks/pre-commit` + `make -n <target>` |
| Solo docs / markdown | nada |

`python manage.py check` corre siempre.

Para revisar un diff, usa el punto de rama como frontera:
`git diff $(git merge-base dev <rama>)...<rama>`.

## API + Deploy + Locale

- **API**: DRF con `DjangoModelPermissionsOrAnonReadOnly`; rate limit 100/h anónimo, 1000/h autenticado; schema en `/api/schema/`.
- **Deploy**: Nginx + Gunicorn vía **systemd**; unidades y vhosts versionados en `deploy/systemd/` y `deploy/nginx/`. Estáticos por Nginx en `/static/`, media en `/media/`. Socket `unix:/run/webcmp/gunicorn.sock`. El worker de Huey tiene su propio unit.
- **Locale**: `LANGUAGE_CODE = 'es-mx'`, `TIME_ZONE = 'America/Havana'`.
