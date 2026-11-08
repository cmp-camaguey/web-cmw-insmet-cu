# AGENTS.md — Centro Meteorológico Provincial Camagüey

## Stack

Django 5.2 + DRF + drf-spectacular (OpenAPI), Python 3.14 (CI y ruff apuntan a `py314`). Virtual env `.venv/`. UI: Tabler vía Django Templates; raíz `templates/` con `layouts/` e `includes/`, y páginas en `apps/<app>/templates/pages/`. DB: **PostgreSQL en los tres perfiles** (dev, test y prod); SQLite está retirado y un `.env` con `DB_ENGINE=sqlite3` falla cerrado. Apps dentro de `apps/`.

**Restricción de frontend:** JS 100% vanilla, **SIN Node.js, SIN bundlers ni paso de build**. Las librerías de terceros se vendorean como `*.min.js` y se sirven directo. Por legado, Tabler (CSS y JS) vive en `static/dist/css/` y `static/dist/js/` (ver `templates/includes/base/head.html` y `scripts.html`); el resto (Tempus Dominus, Popper, jQuery, DataTables, amcharts, highcharts, etc.) va en `static/dist/libs/`. No introducir npm, transpilación ni tooling JS en el proyecto.

## Misión, alcance y límites duros

**Misión:** portal web del Centro Meteorológico Provincial Camagüey (CMP) — portal público meteorológico + dashboard administrativo (pronósticos, avisos, servicios comerciales, facturación, publicaciones) + API REST para terceros.

**No es:** una SPA / frontend desacoplado (React/Vue/Angular), una red social o foro, ni un proyecto OSS genérico — está diseñado específicamente para el CMP Camagüey.

**Límites duros (no negociables):**
- No commitear secretos ni datos: `.env`, `db.sqlite3` ni `media/` (ya excluidos en `.gitignore`).
- No introducir dependencias npm, bundlers o frameworks JS (todo es Django Templates + Tabler + JS vanilla vendoreado; Tabler en `static/dist/css` + `static/dist/js`, las demás libs en `static/dist/libs/`).
- No quitar `FileHandlerMixin` de modelos con `FileField`/`ImageField` (pérdida de archivos en media).
- No cambiar `default_permissions = ()` sin redefinir los 4 permisos custom (`view_*`/`add_*`/`change_*`/`delete_*`).
- No saltarse el flujo SDD para features (proposal → design → tasks → implementación → archive en `openspec/changes/`).

## Comandos

```bash
# Requiere PostgreSQL local: ver "PostgreSQL en desarrollo (obligatorio)" en README.md
source .venv/bin/activate
pip install -r requirements/base.txt && python manage.py makemigrations migrate
python manage.py collectstatic --link --no-input
python manage.py add_stations_data createsuperuser
python manage.py runserver                    # desarrollo
python manage.py test apps.<app>              # testing selectivo (label completo, ej: apps.meteo)
python manage.py test                         # full suite (antes de commit)
make test-deploy                              # pruebas de los scripts de deploy (deploy/tests/)
PRODUCTION=true python manage.py runserver    # producción local (usa DB real, SSL, etc.)
./run_huey.sh &                               # worker de correos/PDF (Huey)
```

## Dependencias — un archivo por entorno

Todo bajo `requirements/`. Cada archivo es autocontenido: se instala con **un** `pip install -r`.

| Archivo | Qué instala |
|---|---|
| `requirements/base.txt` | Runtime de la app. No incluye gunicorn ni django-debug-toolbar. |
| `requirements/prod.txt` | `base.txt` + `gunicorn` (servidor WSGI). |
| `requirements/dev.txt` | `base.txt` + tooling (ruff, bandit, pip-audit, djlint, pre-commit, django-debug-toolbar, setuptools, wheel). |
| `requirements/test.txt` | `base.txt` + `django-debug-toolbar`. |

```bash
pip install -r requirements/dev.txt    # desarrollo
pip install -r requirements/test.txt   # solo correr tests
pip install -r requirements/prod.txt   # producción
```

`django-debug-toolbar` está a propósito en `dev.txt` **y** en `test.txt`: es una herramienta de desarrollo (`DEBUG=True`) pero `apps/core/tests/test_debug_toolbar.py` afirma que está instalada, y en CI corre con `DEBUG=False`. **Nunca** moverla a `base.txt`: eso mandaría una dependencia de desarrollo a producción.

## Pre-commit

Los hooks de pre-commit garantizan Ruff, djlint y detect-secrets en cada commit (`.pre-commit-config.yaml`). El hook viaja **versionado** en `.githooks/pre-commit` y se activa con `core.hooksPath`:

```bash
make setup                          # venv + deps + hooks, todo en un comando
make hooks                          # solo activar el hook (git config core.hooksPath .githooks)
.venv/bin/pre-commit run --all-files   # ejecutar todos los hooks una vez
```

> **⚠️ `pre-commit install` y `core.hooksPath` son MUTUAMENTE EXCLUYENTES.** `core.hooksPath` hace que git ignore `.git/hooks` por completo, así que correr ambos te deja depurando un hook que git nunca invoca. `make setup` corre `pre-commit install --install-hooks` **solo** para poblar los entornos de los hooks; la activación la hace `git config core.hooksPath .githooks`. Usa SIEMPRE `--install-hooks` (no `pre-commit install` a secas): los entornos de hooks (ruff, djlint, detect-secrets) se descargan desde PyPI en la primera ejecución; si se deja para el primer commit, un fallo de red puede colgar el commit y **pre-commit stash los archivos modificados sin stagear en un patch temporal que puede perderse si el proceso se interrumpe**.
>
> El hook en `.githooks/pre-commit` prefiere `$root/.venv/bin/pre-commit` y cae a `PATH`, así que funciona sin activar el venv.
>
> Si la instalación falla con `Could not find a version that satisfies ...` / `No matching distribution found` / timeouts, el problema suele ser el proxy de red (verificar con `pip config list`). Es intermitente: reintentar `pre-commit install --install-hooks` suele resolver. Para hacerlo más resiliente, añade a `~/.pip/pip.conf` (o `~/.config/pip/pip.conf`):
> ```ini
> [global]
> timeout = 120
> retries = 10
> ```
> Y antes de cualquier `pre-commit run` / commit con hooks, asegúrate de que el working tree esté limpio o haz `git stash` manual: pre-commit guarda los archivos modificados sin stagear en un patch temporal antes de correr los hooks y, si el proceso muere (Ctrl-C, timeout, crash), esos cambios quedan fuera del working tree y el patch se pierde con `pre-commit clean`.

- Si `detect-secrets` reporta un falso positivo nuevo, actualizar el baseline con `detect-secrets scan --exclude-files "static/|staticfiles/|.*\.min\.js$|.*\.map$|\.venv/" > .secrets.baseline` y commitear el `.secrets.baseline`.
- El mismo job `pre-commit` corre en CI (`.github/workflows/ci.yml`).

## Flujo de ramas (`dev` → `main`)

Dos ramas de larga vida y nada más:

| Rama | Rol | Reglas |
|---|---|---|
| `main` | Producción. Sólo recibe promociones desde `dev` y hotfixes. | Ruleset `Protect main` activo. Cada merge dispara CI y, si queda verde, `deploy.yml` despliega a producción. |
| `dev` | Integración. Aquí aterrizan todas las features. | Ruleset `Protect dev` activo: sin force push, sin borrado, y merge sólo con los required status checks en verde. **Sin aprobación obligatoria.** |

Ciclo normal:

1. Rama corta desde `dev`: `feat/nnn-descripcion`, `fix/nnn-descripcion`, `chore/descripcion`.
2. PR **a `dev`**. No necesita aprobación: el gate son los required status checks de `ci.yml` + `security.yml`. Cada uno mergea lo suyo sin esperar al otro.
3. Al fusionarse, `cleanup` borra la rama corta.
4. **Promoción por lote**: PR `dev` → `main` cuando `dev` está verde. No se promociona feature por feature; `dev` acumula y `main` recibe un bloque cerrado.
5. **La promoción sí necesita una persona.** `.github/CODEOWNERS` + `require_code_owner_review` en `Protect main` exigen la aprobación de `@orl92` o `@yoelvismr`; la de un bot no cuenta, porque un bot no puede ser code owner. Es **una** aprobación por release, no por PR.
6. Al fusionarse la promoción, CI corre en `main` y `deploy-production.yml` intenta desplegar. **Hoy no despliega nada**: el job tiene `if: vars.DEPLOY_ENABLED == 'true'` y esa variable no existe mientras no haya servidor, así que termina en gris (`skipped`). Etiquetar el commit de merge del release: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.
7. **Inmediatamente después, mergear `main` de vuelta a `dev`** (PR `main` → `dev`). El merge de release crea en `main` un commit que `dev` no tiene, y con `strict_required_status_checks_policy: true` la siguiente promoción saldría `BEHIND` aunque el contenido sea idéntico: la comprobación es de ascendencia de commits, no de árboles. Este paso aplica a **cada release**, no solo a los hotfixes.

Hotfix (el único caso que entra directo a `main`):

1. Rama desde `main`, PR a `main`, CI verde, merge, deploy.
2. **Inmediatamente** mergear `main` de vuelta a `dev` (PR `main` → `dev`). Si se omite, `dev` deja de representar lo que corre en producción y la siguiente promoción arrastra conflictos. `cleanup` no borra `main` ni `dev`, así que ese PR de vuelta es seguro.

Secuencia de activación (el orden importa):

- `require_code_owner_review` en `Protect main` **sólo protege cuando `.github/CODEOWNERS` ya está en `main`**, porque GitHub lee ese archivo de la rama base del PR. Activarlo antes no rompe nada (sin archivo no hay code owners que exigir), pero tampoco protege. Orden: mergear a `dev` → promover a `main` con una aprobación humana → activar la regla.
- Los `required_status_checks` están activos en **las dos** ramas, con los mismos 8 contextos: `Detect affected apps`, `Tests (Django)`, `Lint (Ruff)`, `Templates (djlint)`, `Pre-commit hooks`, `Bandit SAST`, `Security (pip-audit + migrations)`, `Deploy check (check --deploy)`. Si un PR renombra o borra un job, el check requerido nunca reporta y el merge queda bloqueado: **fail-closed**.
- `strict_required_status_checks_policy`: **`true` en `main`, `false` en `dev`**. En `main` el release tiene que haberse probado contra lo último que hay en la rama. En `dev` no: con PRs semanales de Dependabot y `allow_update_branch: false`, exigir ponerse al día deja cada bump `BEHIND` esperando una actualización manual.
- Que `dev` tenga required checks **no es opcional aunque no exigiera aprobación**: GitHub sólo permite habilitar auto-merge (`enablePullRequestAutoMerge`) en un PR cuya rama base tenga reglas de protección con required status checks. Sin ellos, `gh pr merge --auto` falla con `Pull request Branch does not have required protected branch rules` y `dependabot-auto-merge.yml` termina en rojo.

## CI — tests selectivos

El job `test` de CI NO corre la suite completa en cada PR (tarda demasiado):

- **PR**: un job `detect` (`dorny/paths-filter@v3`) detecta qué apps cambiaron (`apps/<app>/**`) y el job `test` corre solo `python manage.py test apps.<app> ...` para las afectadas.
- **Push a main / merge**: siempre corre la suite completa (`python manage.py test`) como red de seguridad.
- Cambios globales (`config/**`, `manage.py`, `requirements/**`, `pyproject.toml`, `.github/**`, `templates/**`, `static/**`) fuerzan la suite completa.
- Los labels de app deben ir como ruta completa (`apps.meteo`, no `meteo`): en Django 5.2 el label corto ya no resuelve el paquete de tests.

## Aprobación de PRs y limpieza de ramas

Resumen: **`dev` no exige aprobación, `main` sí.** No hay bot aprobando PRs, y es deliberado.

| Rama | Aprobación obligatoria | Quién |
|---|---|---|
| `dev` | No | — (sólo required status checks) |
| `main` | Sí, 1 | `@orl92` o `@yoelvismr`, vía `CODEOWNERS` + `require_code_owner_review` |

### Por qué no un bot que apruebe

Hubo un workflow (`auto-approve.yml`) que aprobaba los PR de una lista blanca con la identidad `github-actions[bot]`. Se descartó por un problema de fondo, no de implementación:

**el CI que da el verde lo define el propio PR.** Un run disparado por `pull_request` ejecuta el `ci.yml` de la rama del PR, así que una cuenta habilitada podía vaciar un job, conservar su nombre, obtener verde y recibir la aprobación del bot. Los `required_status_checks` cierran la mitad fácil —borrar o renombrar un job deja el check sin reportar y bloquea el merge— pero no la difícil: dejar el nombre y vaciar el cuerpo.

Y hay un límite que ninguna automatización cruza: **nada detecta que el autor borró el test que lo habría atrapado.** Un gate que vive dentro del repo lo puede desarmar quien puede mergear. Por eso la exigencia de persona se coloca donde el radio de daño es real —la frontera de producción— y no repartida por rutas ni simulada por un bot.

### CODEOWNERS

`.github/CODEOWNERS` asigna `*` a `@orl92` y `@yoelvismr`, y se asigna a sí mismo un dueño explícito para que un PR no pueda vaciarlo y desarmar el control en el release siguiente. GitHub lee el archivo de la **rama base** del PR, así que la copia que cuenta es la de `main`. Basta la aprobación de **uno** de los dos.

Consecuencia útil: un colaborador futuro con `write` puede mergear en `dev`, pero **no puede aprobar nada hacia producción**, porque la code-owner review exige una de esas dos cuentas. No hay lista blanca en código que mantener; el control es declarativo y lo aplica la plataforma.

### Limpieza de ramas (`.github/workflows/cleanup-branch.yml`)

Único job que sobrevive. Al fusionarse un PR en `main` o `dev`, borra la rama de cabeza con `DELETE /git/refs/heads/<rama>` sólo si: el PR viene del mismo repo (no un fork), `main`/`dev` no es la rama de cabeza (son de larga vida), el último run de `ci.yml` para ese `head.sha` terminó en `success`, la rama no está protegida, y el borrado realmente ocurrió. Un `404` (rama ya inexistente) se distingue de cualquier otro error de API, que falla en rojo en vez de fingir éxito.

- `pull_request: closed` usa el workflow de la rama **base** del PR, así que la limpieza de PRs hacia `dev` rige en cuanto el archivo esté en `dev`. A diferencia de `workflow_run`, no depende de la rama por defecto.
- `delete_branch_on_merge` sigue en `false` a propósito: la limpieza la hace el workflow, no GitHub. Si algún día se activa el setting, el job termina sin error cuando la rama ya no existe (y si el borrado falla pero la rama sigue ahí, el job sí falla en rojo).
- Ningún workflow aprueba ya PRs, así que *Allow GitHub Actions to create and approve pull requests* **no se necesita** y conviene tenerlo **desactivado** en `cmp-camaguey` → Settings → Actions → General: es una capacidad que ningún flujo usa y amplía lo que un workflow comprometido podría hacer. `dependabot-auto-merge.yml` sólo hace `gh pr merge --auto`, nunca `gh pr review`.
- **Dependabot apunta a `dev`**, no a la rama por defecto. `.github/dependabot.yml` fija `target-branch: dev` en los tres ecosistemas: si apuntara a `main`, un bump de dependencia entraría directo a producción saltándose la rama de integración, y con `require_code_owner_review` el `gh pr merge --auto` se quedaría esperando una aprobación humana por cada bump minor. Los bumps viajan dentro de la promoción por lote, donde ya hay una persona mirando.

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
| `openspec/changes/` | SDD features (OpenSpec); gentle-ai lee este directorio |

## Convenciones

- **UI Framework**: exclusivamente Tabler.io (Bootstrap 5). No usar otros frameworks CSS/UI.
- **Permisos**: `default_permissions = ()` + 4 personalizados: `view_*`, `add_*`, `change_*`, `delete_*` (en español). NO asumas que existen por defecto.
- **Middleware**: `CheckUserProfileMiddleware` → `MaintenanceModeMiddleware` (bloquea no-superusers excepto `/login/` y `/logout/`: sin la segunda, un usuario no-superuser que quedó con sesión abierta no puede cerrarla durante el mantenimiento)
- **Auth**: LDAP opcional (`LDAP3Backend`) → fallback `ModelBackend`; Profile se crea por señal `post_save`; usuarios LDAP con `is_ldap=True`
- **UUIDs** en URLs de modelos expuestos (no `pk`)
- **FileHandlerMixin** obligatorio en todo modelo que tenga FileField/ImageField
- **Soft delete** en modelos de negocio con datos sensibles (Customer, Service, ServiceSubscription, Invoice, Contract, Certificate). NO forzar en modelos auxiliares/transaccionales (InvoiceItem, ForecastRegions, EmailRecipient).
  - **`Warning` se borra físicamente, a propósito.** Estuvo en esta lista por error: el código nunca lo implementó así, y la decisión es que no lo haga. La protección contra un borrado accidental es el permiso `delete_warning` asignado por rol, no `record_active`. Si algún día hace falta historial de avisos, va en un modelo de auditoría, no reciclando el borrado lógico.
- **Paginación**: `paginate_by = 10` (convención uniforme) en vistas SIN DataTables. Las vistas con DataTables cargan todos los registros y delegan la paginación al cliente — NO llevan `paginate_by`. Esto aplica por igual a listas públicas de servicios por cards (catálogo comercial y Mis Servicios), que usan `paginate_by = 10` para proteger la UX móvil; los tests codifican ese valor.
- **Migrations** versionadas: `makemigrations` + commit en el mismo PR; el gate es `makemigrations --check --dry-run`
- **Estáticos**: `static/` dev, `staticfiles/` prod con WhiteNoise
- **Idioma**: español (`es-mx`, `America/Havana`)
- **Tema**: Tabler (Bootstrap 5), iconos meteorológicos PNG en `static/dist/img/weather_icon/`
- **URLs**: toda app con URLs usa `app_name` en urls.py y names estandarizados (`app_name:list`, `create`, `detail`, `update`, `delete`, `pdf`). Templates usan `{% url 'app_name:name' %}`, vistas usan `reverse_lazy('app_name:name')`.
- **Templates (formato)**: indentación de 2 espacios, formateados con djlint (`profile = "django"`, config en `pyproject.toml`). Verificar con `djlint . --reformat --check` y `djlint . --lint` antes de commit (hooks de pre-commit). Los templates de correo en `*/emails/` están excluidos (whitespace-sensitive): NO reindentarlos a mano ni con djlint.
- **Apps**: todas las apps Django viven en `apps/`. Importar como `from apps.commercial.models import ...`, nunca como `from commercial.models import ...`.

## Skills

La lista `<available_skills>` del sistema es la fuente autoritativa de skills cargadas en la sesión. Las skills de proyecto viven en `.opencode/skills/` (viajan con el repo en cualquier clon); las globales en `~/.config/opencode/skills/`.

> **Uso automático**: usa MCPs y skills de forma proactiva cuando la tarea lo requiera, sin esperar a que el usuario los pida. Ver reglas globales en `~/.config/opencode/AGENTS.md`.

### Skills del proyecto (`.opencode/skills/`)
- `using-agent-skills` — árbol de decisión para elegir la skill correcta según la tarea
- `django-expert` — modelos, ORM, DRF, auth, tests, performance Django
- `frontend-design` — diseño visual con identidad (Tabler.io)
- `web-design-guidelines` — auditoría de accesibilidad y UI

### Skills globales (opencode)
- **SDD**: `sdd-init`, `sdd-explore`, `sdd-propose`, `sdd-spec`, `sdd-design`, `sdd-tasks`, `sdd-apply`, `sdd-verify`, `sdd-archive`, `sdd-onboard`
- **Git/PR**: `branch-pr`, `chained-pr`, `work-unit-commits`, `issue-creation`, `systemic-issue-triage`
- **Revisión**: `judgment-day`, `rdd-defect-workflow`, `go-testing`, `gentle-ai-bench`
- **Meta**: `skill-creator`, `skill-improver`, `skill-registry`, `cognitive-doc-design`, `comment-writer`, `customize-opencode`

### MCP
- `context7` — documentación actualizada de librerías/frameworks (Django, DRF, drf-spectacular, etc.). Usarlo siempre que se necesiten APIs, ejemplos o configuración de una librería; formato `use library /django/django`
- `engram` — memoria persistente del proyecto (decisiones, descubrimientos, convenciones)
- `tabler` — server MCP de Tabler.io (proyecto comunitario, MIT) para buscar íconos, componentes, layouts, paleta y docs de Tabler. Es un server stdio local (`npx -y tabler-mcp-server`) configurado en el opencode del agente, NO en el repo. Las sugerencias de CDN que genera se ignoran, porque el proyecto vendorea Tabler en `static/dist/css/` y `static/dist/js/` (legacy; ver `templates/includes/base/head.html` y `scripts.html`).

## Flujo SDD (gentle-ai / OpenSpec)

El proyecto ya existía antes de adoptar gentle-ai; la herramienta se usa para refinar y evolucionar el código de forma trazable. El código ya construido es el repo en sí; los **cambios activos** (work pendiente) viven en `openspec/changes/` como features OpenSpec. No se versiona historial de features previas.

Cada cambio nuevo sigue este flujo (layout **OpenSpec**; gentle-ai lee `openspec/changes/`):

1. `openspec/changes/NNN-nombre/proposal.md` (motivación + solución + criterios de aceptación)
2. `design.md` (plan técnico) + `tasks.md` (checkboxes `- [ ]`/`- [x]`) + `specs/NNN-nombre/spec.md` (requisitos delta)
3. Implementar un task a la vez
4. Si hay cambios de modelo: `python manage.py makemigrations`
5. Verificar: `python manage.py check && python manage.py test apps.<app>`; crear test si falta
6. Marcar tasks `[x]` y commitear (incluir número y nombre del cambio)
7. Al cerrar: `gentle-ai sdd-archive` promueve los specs y archiva el cambio

> El bootstrap (`openspec/config.yaml`, `.atl/skill-registry.md`) lo genera `gentle-ai` vía el skill `sdd-init`.

Antes de codificar, si hace falta clarificar, preguntar: ¿tipo de cambio? ¿app(s) afectada(s)? ¿cambios de DB? ¿URLs/permisos? ¿templates? ¿criterios de aceptación? ¿número de feature?

## Checklist proyecto-específico

Estos items NO los cubren los skills genéricos. Verificarlos siempre:

- [ ] `default_permissions = ()` + 4 permisos custom (view/add/change/delete) en español
- [ ] Kwarg `uuid` (no `pk`) en URLs de modelos con UUIDField
- [ ] FileHandlerMixin + `file_fields` definido si hay FileField/ImageField
- [ ] Soft delete: filtrar `record_active=True` (usa el manager por defecto, no `all_objects`)
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
- Iconos: webfont Tabler (`<i class="icon ti ti-*">`); iconos meteorológicos PNG en `static/dist/img/weather_icon/`.
- Modales: mecanismo nativo de Tabler (data API / `Bootstrap.Modal`).

## Tempus Dominus (date pickers)

- Pickers inicializados en `static/dist/js/tempus-init.js`, **una sola vez** en `DOMContentLoaded` sobre los `[data-tempus]` presentes.
- `tempus-init.js` define sus globals (`window.pickerDateToIso`, `window.formatPickerDate`, `window.formatPickerDateTime`) y `window.tempusDominus._instances` DENTRO de `DOMContentLoaded`; los scripts de plantilla corren antes → usan fallback local y difieren la suscripción al picker.
- **Restricción conocida:** no hay observer para nodos agregados tras la carga. Si en el futuro se inyecta un `[data-tempus]` por JS (AJAX/htmx/fetch), inicializarlo con `initPicker(input)` (función en `tempus-init.js`) o añadir un `MutationObserver` que llame `initPicker` para cada nodo nuevo, **con guarda anti-doble-init** (`if (window.tempusDominus && window.tempusDominus._instances && window.tempusDominus._instances[input.id]) return;`). Ver comentario en `tempus-init.js`.

## Verificación antes de commit

**Gentle AI ya cubre los hooks (`pre-commit`) y la revisión del diff (RDD/4R). No los repitas acá**: no lances un subagente de revisión si la revisión nativa está activa, y no agregues pasos de hook — se configuran en `.githooks/pre-commit` y `Makefile`.

Lo que gentle-ai **no** cubre es la verificación funcional (`review validate --gate` solo valida sintaxis y reporta política; no ejecuta nada). Córrela según lo que cambió, no por ritual:

| Cambio | Verificación |
|---|---|
| Código Python (modelos, vistas, permisos) | `python manage.py test` |
| Scripts shell (`deploy/`, `run_huey.sh`, hooks) | `make test-deploy` |
| `requirements/` con **versiones cambiadas** | `python manage.py test` (el comportamiento puede cambiar) |
| `requirements/` solo pines, sin cambio de versión | `pip install --dry-run -r requirements/<env>.txt` |
| Solo workflows / Makefile / hooks | YAML parseable + `sh -n .githooks/pre-commit` + `make -n <target>` |
| Solo docs / markdown | nada |

`python manage.py check` es barato (~1s): correlo siempre.

## API + Deploy + Locale

- **API**: DRF con `DjangoModelPermissionsOrAnonReadOnly`; rate limit 100/h anon, 1000/h user; schema en `/api/schema/`
- **Deploy**: Nginx + Gunicorn vía **systemd** (no supervisor, no `gunicorn.sh`); unidades y ejemplo de Nginx versionados en `deploy/systemd/` y `deploy/nginx/`. Estáticos por Nginx en `/static/`, media en `/media/`; socket `unix:/run/webcmp/gunicorn.sock` (vía `RuntimeDirectory` del unit, no `/tmp`). Worker de Huey en su propio unit.
- **Locale**: `LANGUAGE_CODE = 'es-mx'`, `TIME_ZONE = 'America/Havana'`
