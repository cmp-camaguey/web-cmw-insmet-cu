# Centro Meteorológico Provincial Camagüey

Sistema web del **Centro Meteorológico Provincial Camagüey** (CMP): portal público
meteorológico, panel administrativo y API REST para terceros.

**Producción:** [https://www.meteocamaguey.cu](https://www.meteocamaguey.cu)

Django 5.2 + Django REST Framework sobre PostgreSQL, interfaz con
[Tabler](https://tabler.io/), servidor Gunicorn detrás de Nginx y un worker de Huey
para correos y PDFs. Interfaz y configuración en español (`es-mx`, `America/Havana`).

---

## Índice

- [Arranque rápido](#arranque-rápido) — entorno de desarrollo en 3 pasos
- [Cómo funciona la configuración](#cómo-funciona-la-configuración) — perfiles y claves
- [Organización del proyecto](#organización-del-proyecto) — qué hace cada app
- [Convenciones que te van a morder](#convenciones-que-te-van-a-morder) — las reglas duras del proyecto
- [Tests](#tests) — cómo correrlos
- [Probar el worker y los correos](#probar-el-worker-y-los-correos) — PDF + email de punta a punta
- [Despliegue en producción](#despliegue-en-producción) — qué hay en `deploy/` y dónde está el manual
- [Contribuir](#contribuir) — flujo de trabajo del proyecto
- [Licencia](#licencia)

---

## Arranque rápido

Requiere **Python 3.14** (el CI la fija y ruff apunta a `py314`).

```bash
git clone https://github.com/cmp-camaguey/webcmp.git
cd webcmp

# Dependencias del sistema para PDFs (wkhtmltopdf)
sudo apt install libcairo2-dev pkg-config python3-dev wkhtmltopdf

# Compilador: varios pins de requirements/prod.txt no tienen rueda en 3.14.
sudo apt install build-essential

# WeasyPrint (PDF) carga libpango por cffi. El nombre es libpango1.0-dev.
sudo apt install libpango1.0-dev

make setup
```

`make setup` crea el venv, instala las dependencias de desarrollo y tests, genera el
`.env` con un par de claves usable y activa los hooks de git. Después hay que
crear la base local —se hace una sola vez, en la sección siguiente— y arrancar:

```bash
python manage.py migrate             # las migraciones ya vienen versionadas
python manage.py add_stations_data   # Stations, Towns, Provinces
python manage.py createsuperuser
python manage.py runserver
```

En `http://127.0.0.1:8000/`. El perfil de desarrollo usa PostgreSQL y correo a
consola.

| Target | Qué hace |
| --- | --- |
| `make setup` | venv + dependencias + `.env` + hooks |
| `make env` | Genera el `.env` solo si falta (nunca pisa el existente) |
| `make env-force` | Regenera el `.env` conservando el par de claves |
| `make test` | Suite completa |
| `make lint` | `ruff check` + `ruff format --check` |
| `make djlint` | Lint de templates |
| `make audit` | `pip-audit` sobre las dependencias de runtime |
| `make check` | `manage.py check` |

El worker de Huey corre aparte, en otra terminal:

```bash
./run_huey.sh
```

Para probar el flujo de facturas y correos de punta a punta (con sus
dependencias y su preflight), ver [Probar el worker y los correos](#probar-el-worker-y-los-correos).

<details>
<summary>Instalación manual (si no usas <code>make</code>)</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
python scripts/generate_env.py --development
git config core.hooksPath .githooks
```

</details>

### PostgreSQL en desarrollo (obligatorio)

El proyecto corre sobre **PostgreSQL en los tres perfiles**. Si falta
`DB_ENGINE`, `manage.py` falla al arrancar.

```bash
# 1. Rol y base locales (una vez). El rol necesita CREATEDB: la suite crea y
#    destruye su propia base de tests.
sudo -u postgres psql <<'SQL'
CREATE USER webcmp_dev WITH PASSWORD 'CAMBIAR_ESTA_CLAVE' CREATEDB;  -- pragma: allowlist secret
CREATE DATABASE webcmp_dev OWNER webcmp_dev;
SQL

# 2. Credenciales en el .env de desarrollo
DB_ENGINE=postgresql
DB_NAME=webcmp_dev
DB_USER=webcmp_dev
DB_PASS=CAMBIAR_ESTA_CLAVE
DB_HOST=127.0.0.1
DB_PORT=5432

# 3. Migrar y correr
python manage.py migrate
python manage.py runserver
```

`python scripts/generate_env.py --development` escribe ese bloque por vos.

### Dependencias: un archivo por entorno

Cada archivo es autocontenido (incluye `base.txt` con `-r base.txt`).

| Archivo | Instala |
| --- | --- |
| `requirements/base.txt` | Runtime de la app. Sin gunicorn ni herramientas de desarrollo. |
| `requirements/dev.txt` | `base.txt` + ruff, bandit, pip-audit, djlint, pre-commit, django-debug-toolbar |
| `requirements/test.txt` | `base.txt` + django-debug-toolbar |
| `requirements/prod.txt` | `base.txt` + gunicorn, whitenoise y redis |

```bash
pip install -r requirements/dev.txt   # desarrollo
pip install -r requirements/test.txt  # solo correr tests
pip install -r requirements/prod.txt  # producción
```

---

## Cómo funciona la configuración

`config/settings/` es un paquete con perfiles. El dispatcher
(`config/settings/__init__.py`) elige el perfil antes de importar nada:

| Condición | Perfil | Para qué |
| --- | --- | --- |
| `PRODUCTION` en el entorno | `production` | Servidor |
| `DEBUG=True` | `dev` | Desarrollo con `.env` |
| sin `.env` | `testing` | Suite de tests |

`PRODUCTION` gana sobre `DEBUG`. Los tres perfiles importan `base.py` y solo
declaran sus diferencias.

### Las claves se generan fuera de Django

El generador de claves es un script suelto: `manage.py` importa los settings antes de despachar el comando y no puede arrancar sin `.env`.

```bash
python scripts/generate_env.py --development   # PostgreSQL local, correo a consola
python scripts/generate_env.py --production    # PostgreSQL, SMTP, fail-closed
python scripts/generate_env.py --production --rotate-keys   # invalida sesiones
```

`base.load_secret_key()` levanta `ImproperlyConfigured` si no hay material descifrable: no hay fallback a una clave aleatoria. El perfil `testing` inyecta un par determinista, y por eso la suite corre en un clon limpio.

### La `ENCRYPTION_KEY` no va en el `.env`

El `.env` guarda la `SECRET_KEY` cifrada con `ENCRYPTION_KEY`. En producción la clave de descifrado va aparte, en `/etc/webcmp/encryption.env` (`600`, root), y systemd la carga con `EnvironmentFile=-`. Un `.env` filtrado no entrega nada por sí solo.

---

## Organización del proyecto

Las apps viven en `apps/` y se importan como `from apps.<app>.models import ...`.

| App | Responsabilidad |
| --- | --- |
| `apps/home` | Páginas públicas: tiempo, modelos, satélites, servicios, institución |
| `apps/dashboard` | Vista agregada del panel |
| `apps/meteo` | Pronósticos, avisos, reportes meteorológicos, geografía y estaciones |
| `apps/commercial` | Clientes, servicios, suscripciones, facturación, contratos, certificados |
| `apps/publications` | Publicaciones científicas y autores |
| `apps/user_auth` | Perfil, login, usuarios, grupos, LDAP opcional |
| `apps/api` | API REST: estaciones, observaciones, pronósticos. Docs en `/api/doc/` |
| `apps/core` | Mixins, error views, configuración del sitio, envío de correo, utilidades |

`config/` tiene los settings, urls raíz y WSGI/ASGI. `templates/` contiene los
layouts base y los includes; cada app tiene sus páginas en
`apps/<app>/templates/pages/`. `static/` en desarrollo, `staticfiles/` en producción
vía WhiteNoise.

Las convenciones detalladas —modelos de dominio, permisos, estructura de URLs,
Huey— están en [`AGENTS.md`](AGENTS.md), que es la referencia técnica del proyecto.

---

## Convenciones que te van a morder

Estas reglas no son estilo: rompen cosas si no las seguís.

**No hay Node.js.** Ni bundlers, ni paso de build, ni `package.json`. Todo el
frontend es Django Templates + Tabler + JavaScript vanilla ya vendoreado. Tabler
(CSS y JS) está en `static/dist/css/` y `static/dist/js/`; el resto de librerías
en `static/dist/libs/`. Un PR que agregue npm no se mergea.

**Las migraciones se versionan.** Si cambias un modelo, corres `makemigrations` y
commiteas el archivo generado en el mismo PR. El diff de la migración es donde se
revisa un `ALTER` destructivo; CI y deploy fallan con
`makemigrations --check --dry-run` si el cambio de modelo llega sin su migración.

**Los permisos no son los de Django.** Los modelos de negocio usan
`default_permissions = ()` y definen cuatro permisos custom (`view_*`, `add_*`,
`change_*`, `delete_*`) en español. No asumas que existen por defecto.

**Los modelos con archivos usan `FileHandlerMixin`.** No lo quites: es lo que evita
perder archivos al reubicar en `media/`.

**Soft delete en modelos de negocio con datos sensibles** (Customer, Service,
ServiceSubscription, Invoice, Contract, Certificate). Se consulta por el manager
por defecto, que ya filtra `record_active=True`. `Warning` se borra físicamente:
su protección es el permiso `delete_warning`.

**URLs por `uuid`, no por `pk`,** en los modelos expuestos.

**Los templates usan indentación de 2 espacios** y djlint los formatea
(`make djlint`). Los templates de correo en `*/emails/` están excluidos porque son
whitespace-sensitive.

**El worker de Huey corre como servicio aparte.** Los correos y los PDFs se
generan ahí (`deploy/systemd/webcmp-huey.service`), no dentro del proceso web.

---

## Tests

```bash
python manage.py test                    # suite completa
python manage.py test apps.meteo         # una app
python manage.py test apps.meteo.tests.test_forecasts
```

El CI no corre la suite completa en cada PR: detecta qué apps cambiaron y corre
solo esas. Los cambios en `config/`, `templates/`, `static/`, `requirements/` o los
workflows fuerzan la suite completa, igual que un push a `main`.

Antes de abrir un PR:

```bash
python manage.py check && python manage.py test
```

Los hooks de pre-commit (`ruff`, `djlint`, `detect-secrets`) corren solos en cada
commit. Viajan versionados en `.githooks/pre-commit` y `make setup` los activa con
`core.hooksPath`. Para correrlos sobre todo el repo:

```bash
.venv/bin/pre-commit run --all-files
```

> `pre-commit install` y `core.hooksPath` son mutuamente excluyentes: `core.hooksPath`
> hace que git ignore `.git/hooks` por completo, así que correr ambos te deja
> depurando un hook que git nunca invoca. `make setup` usa
> `pre-commit install --install-hooks` **solo** para poblar los entornos de los hooks;
> la activación la hace `core.hooksPath`.

---

## Probar el worker y los correos

El flujo de factura (PDF + correo) depende de tres cosas que fallan en silencio:
el binario `wkhtmltopdf`, el worker corriendo y el backend de correo. Si falta
alguna, Huey reintenta y la factura queda con `email_sent=True` sin haber salido.

Este comando valida las dependencias antes de tocar la base:

```bash
# Crea cliente, factura e item de prueba y ejecuta la tarea en este proceso
python manage.py send_test_invoice --demo --escribir-en-dev

# Deja la tarea en la cola para que la tome ./run_huey.sh
python manage.py send_test_invoice --demo --escribir-en-dev --encolar

# Procesa una factura que ya existe
python manage.py send_test_invoice <uuid>

# Borra lo que dejaron las corridas con --demo
python manage.py send_test_invoice --purge-demo
```

> `wkhtmltopdf` no se instala con `pip`: es una dependencia del sistema. En
> Debian/Ubuntu, `sudo apt install wkhtmltopdf`. `pdfkit` es solo un wrapper; sin
> el binario, la tarea muere *antes* de enviar el correo.

### Ver el correo, no un "enviado" falso

El backend por defecto en desarrollo es `console`: imprime el mensaje y devuelve
éxito igual, así que `email_sent=True` no significa que el correo haya salido. Para
verlo de verdad, poné en tu `.env`:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.filebased.EmailBackend
```

Cada correo queda como archivo en `tmp/emails/` (ignorado por git, porque lleva
datos de clientes). Para cambiar la ruta: `EMAIL_FILE_PATH=...`.

Con SMTP real usá las variables de producción y el `EMAIL_BACKEND` de Django que
corresponda. `dev` acepta cualquiera; `production` rechaza `console`, `filebased` y
`locmem` (los tres aceptan el mensaje y lo descartan) y te pide regenerar el `.env`
con `python scripts/generate_env.py --production`.

---

## Despliegue en producción

Para un servidor **nuevo**, en Ubuntu 26.04 LTS con Python 3.14 nativo.

```
# TLS en esta máquina (PROXY_MODE=nginx-local)
Nginx (TLS, estáticos, media) → Gunicorn (systemd) → PostgreSQL
                                              └──→ worker Huey (systemd)

# TLS en un proxy externo (PROXY_MODE=external)
NPM (otra máquina, TLS) → Nginx del servidor (HTTP, estáticos, media)
                        → Gunicorn (socket unix) → PostgreSQL
```

Nginx se instala en las dos topologías: cambia quién termina el TLS, no si hay
Nginx. Gunicorn escucha solo en `/run/webcmp/gunicorn.sock` y Nginx sirve
`/static/` y `/media/`; con `DEBUG=False` Django no publica `/media/`.

Redis es opcional, pero `scripts/generate_env.py --production` escribe
`USE_REDIS_CACHE=True`: instalalo o poné `False` en el `.env`. Sin Redis el caché
vive en cada worker de Gunicorn.

Todo lo que se instala en el servidor está versionado en `deploy/`:

| Archivo | Para qué |
| --- | --- |
| `deploy/install.sh` | Instalador del servidor completo: sistema, usuario, base, app, Nginx y andamiaje de deploy. Idempotente, corre una vez por servidor |
| `deploy/deploy.sh` | La lógica del deploy, en un solo lugar. Se instala como `/usr/local/sbin/webcmp-deploy` |
| `deploy/deploy.env.example` | Config del deploy → `/etc/webcmp/deploy.env` |
| `deploy/systemd/*.service` | Gunicorn y el worker Huey |
| `deploy/nginx/*.conf.example` | Los dos vhosts: con TLS local y sin TLS |
| `deploy/sudoers/webcmp-deploy` | El sudoers acotado a un único comando |

**El paso a paso y la operación viven en
[`deploy/README-deploy.md`](deploy/README-deploy.md):** instalación manual de la
aplicación (pasos 1 a 7), andamiaje del deploy, el botón "Actualizar" del panel,
el rollback, las notificaciones, la franja de aviso y los errores que solo
aparecen en producción.

---

## Contribuir

El proyecto registra los cambios con impacto: se describen en
`openspec/changes/<NNN-nombre>/` antes de implementarse. Las convenciones que hay
que respetar están en `AGENTS.md`.

Un cambio en OpenSpec son cuatro archivos, y el `tasks.md` es el que se va
marcando:

```text
openspec/changes/030-lo-que-sea/
├── proposal.md              # por qué y qué resuelve
├── design.md                # cómo, con las decisiones técnicas
├── tasks.md                 # checkboxes; se implementa una tarea a la vez
└── specs/030-lo-que-sea/spec.md
```

Cuando el cambio llega a producción, los specs se promueven y el directorio se
mueve a `openspec/changes/archive/<fecha>-<nombre>/`.

### Ramas, PRs y CI

Dos ramas de larga vida: `dev` integra, `main` es producción. Las features entran
por `dev` con PR en squash; `main` sólo recibe promociones por lote (merge commit)
y hotfixes. `dev` no exige aprobación —el gate son los required status checks—,
`main` sí exige una aprobación humana vía `.github/CODEOWNERS`.

El ciclo completo, los required checks y el criterio de aprobación están en
[`AGENTS.md`](AGENTS.md#flujo-de-ramas-dev--main).

Autores: el Instituto de Meteorología de Cuba.

## Licencia

MIT. Ver [LICENSE](LICENSE).

Centro Meteorológico Provincial Camagüey — Instituto de Meteorología de Cuba.
