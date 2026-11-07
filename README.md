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
- [Cómo funciona la configuración](#cómo-funciona-la-configuración) — perfiles, claves y por qué falla cerrado
- [Organización del proyecto](#organización-del-proyecto) — qué hace cada app
- [Convenciones que te van a morder](#convenciones-que-te-van-a-morder) — las reglas duras del proyecto
- [Tests](#tests) — cómo correrlos y qué corre CI
- [Probar el worker y los correos](#probar-el-worker-y-los-correos) — PDF + email de punta a punta
- [Despliegue en producción](#despliegue-en-producción) — servidor nuevo, de cero a deploy automático
  - [1. Prerrequisitos de sistema](#1-prerrequisitos-de-sistema)
  - [2. Base de datos](#2-base-de-datos)
  - [3. Código, venv y `.env`](#3-código-venv-y-env)
  - [4. systemd](#4-systemd)
  - [5. Nginx](#5-nginx)
    - [Con TLS en esta máquina](#con-tls-en-esta-máquina)
    - [Detrás de un proxy externo](#detrás-de-un-proxy-externo)
  - [6. Datos iniciales y usuario administrador](#6-datos-iniciales-y-usuario-administrador)
  - [7. Verificar](#7-verificar)
  - [8. Deploy automático con GitHub Actions](#8-deploy-automático-con-github-actions)
  - [9. Operación](#9-operación)
- [Contribuir](#contribuir) — flujo de trabajo del proyecto
- [Licencia](#licencia)

---

## Arranque rápido

Requiere **Python 3.14** (el CI la fija y ruff apunta a `py314`).

```bash
git clone https://github.com/cmp-camaguey/webcmp.git
cd webcmp

# Dependencias del sistema para PDFs (wkhtmltopdf) y MySQL (libmysqlclient)
sudo apt install libcairo2-dev pkg-config python3-dev wkhtmltopdf

# En Python 3.14 no hay ruedas para varios pins de requirements/prod.txt
# (pycairo entre otros) y hay que compilar. Sin esto pip falla con
# "Running cc --version gave [Errno 2] No such file or directory: 'cc'".
sudo apt install build-essential

# WeasyPrint (facturacion en PDF) carga libpango por cffi. Sin esto,
# `manage.py check` muere con "ffi.dlopen('libpango-1.0.so.0')".
# Ojo con el nombre: en Ubuntu 24.04+ es libpango1.0-dev, NO libpango-1.0-dev.
sudo apt install libpango1.0-dev

make setup
```

`make setup` crea el venv, instala las dependencias de desarrollo y tests, genera el
`.env` con un par de claves usable y activa los hooks de git. Faltan dos pasos:

```bash
python manage.py migrate             # las migraciones ya vienen versionadas
python manage.py add_stations_data   # Stations, Towns, Provinces
python manage.py createsuperuser
python manage.py runserver
```

En `http://127.0.0.1:8000/`. El perfil de desarrollo usa SQLite y correo a consola:
cero configuración de infraestructura.

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

El proyecto corre sobre **PostgreSQL en los tres perfiles**. No hay respaldo a
SQLite: si falta `DB_ENGINE`, `manage.py` falla al arrancar en vez de crear un
`db.sqlite3` que mida un motor que no existe en producción.

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

`python scripts/generate_env.py --development` escribe ese bloque por vos. Si un
`.env` viejo dice `DB_ENGINE=sqlite3`, el arranque falla con un mensaje que lo
explica: los datos de `db.sqlite3` **no se convierten solos**, hay que volcarlos
aparte antes de borrar el archivo.

Lo que se valida en SQLite no prueba lo que corre en PostgreSQL: `LIKE` con
mayúsculas, orden de `NULL`, constraints, secuencias y transacciones no se
comportan igual.

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

`config/settings/` es un **paquete con perfiles**, no un monolito. El dispatcher
(`config/settings/__init__.py`) elige el perfil antes de importar nada:

| Condición | Perfil | Para qué |
| --- | --- | --- |
| `PRODUCTION` en el entorno | `production` | Servidor |
| `DEBUG=True` | `dev` | Desarrollo con `.env` |
| sin `.env` | `testing` | Suite de tests |

`PRODUCTION` gana sobre `DEBUG`. Los tres perfiles importan `base.py`, así que
comparten la misma configuración y se comportan igual salvo donde declaran una
diferencia.

### Las claves se generan fuera de Django

El bootstrap de un proyecto Django tiene una trampa: `manage.py` importa los
settings **antes** de despachar el comando, así que un comando que genera la clave
necesita que los settings ya tolerate no tenerla. Por eso el generador es un script
plano, que corre exactamente en el estado donde la aplicación todavía no puede
arrancar:

```bash
python scripts/generate_env.py --development   # SQLite, correo a consola
python scripts/generate_env.py --production    # PostgreSQL, SMTP, fail-closed
python scripts/generate_env.py --production --rotate-keys   # invalida sesiones
```

Y por eso los settings pueden **fallar cerrado**: `base.load_secret_key()` levanta
`ImproperlyConfigured` en cualquier perfil si no hay material descifrable. No hay
fallback a una clave aleatoria ni aviso silencioso — un perfil de producción sin
clave correcta no se distingue del que la tiene, y esa es exactamente la
diferencia que no querés.

El perfil `testing` es la excepción, y es deliberada: inyecta un par determinista
antes de importar `base`, que es lo que permite correr la suite en un clon limpio
sin `.env`.

### La `ENCRYPTION_KEY` no va en el `.env`

El `.env` guarda la `SECRET_KEY` **cifrada con** `ENCRYPTION_KEY`. Si las dos están
en el mismo archivo, un `.env` filtrado entrega el secreto ya descifrado: la clave
con la que se cifró viaja junto al texto cifrado.

En producción el script escribe la clave de descifrado aparte, en
`/etc/webcmp/encryption.env` (`600`, root), y systemd la carga con
`EnvironmentFile=-`. Un `.env` filtrado no entrega nada por sí solo.

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
frontend es Django Templates + Tabler + JavaScript vanilla ya vendoreado. Por
legado, Tabler (CSS y JS) está en `static/dist/css/` y `static/dist/js/`; el resto
de librerías en `static/dist/libs/`. Un PR que agregue npm no se merges.

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
ServiceSubscription, Invoice, Contract, Certificate, Warning). Se consulta por el
manager por defecto, que ya filtra `record_active=True`.

**URLs por `uuid`, no por `pk`,** en los modelos expuestos.

**Los templates usan indentación de 2 espacios** y djlint los formatea
(`make djlint`). Los templates de correo en `*/emails/` están excluidos porque son
whitespace-sensitive.

**El worker de Huey no es decorativo.** Los correos y los PDFs se generan ahí, así
que corre como un servicio aparte (`deploy/systemd/webcmp-huey.service`), no dentro
del proceso web.

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

El flujo de factura (PDF + correo) tiene tres dependencias que fallan en
silencio: el binario `wkhtmltopdf`, el worker corriendo, y el backend de correo.
Sin esto, el síntoma es siempre el mismo y engañoso: Huey reintenta tres veces y
la factura queda con `email_sent=True` sin que haya salido nada de tu máquina.

Este comando te dice **cuál** falta, antes de encolar nada:

```bash
# Chequeo previo: crea datos de prueba solo si todo lo demás está en su sitio
python manage.py send_test_invoice --demo

# Encolar para el worker (que corre aparte: ./run_huey.sh)
python manage.py send_test_invoice --demo

# Ejecutar en este proceso, sin levantar el worker
python manage.py send_test_invoice --demo --now

# Una factura que ya existe
python manage.py send_test_invoice <uuid>
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

Para un servidor **nuevo**, en Ubuntu 26.04 LTS con Python 3.14 nativo. Si venís de
supervisor, hay una nota al final de la sección.

```
# TLS en esta máquina (PROXY_MODE=nginx-local)
Nginx (TLS, estáticos, media) → Gunicorn (systemd) → PostgreSQL
                                              └──→ worker Huey (systemd)

# TLS en un proxy externo (PROXY_MODE=external)
NPM (otra máquina, TLS) → Nginx del servidor (HTTP, estáticos, media)
                        → Gunicorn (socket unix) → PostgreSQL
```

**Nginx se instala en las dos topologías.** La diferencia no es "con Nginx o sin
Nginx", es quién termina el TLS. Y en las dos, Gunicorn escucha únicamente en
`/run/webcmp/gunicorn.sock`: no se expone a la red.

Por qué Nginx es obligatorio incluso cuando el TLS lo termina otro: es el único
que sirve `media/` desde ese filesystem. Con `DEBUG=False`, Django no publica
`/media/` (`config/urls.py` solo agrega esa ruta con el debug toolbar activo) y
WhiteNoise sirve únicamente `staticfiles`. Sin el Nginx local, el logo y el
favicon de todas las páginas dan 404, igual que cada PDF subido, mientras el
resto del sitio responde 200. Un proxy externo no puede cubrirlo: está en otra
máquina y no ve `/srv/webcmp/media`.

Redis es opcional: el caché por defecto corre en memoria del proceso.

> Ojo: `scripts/generate_env.py --production` escribe `USE_REDIS_CACHE=True`, así que
> o instalás Redis (`sudo apt install redis-server && sudo systemctl enable --now
> redis-server`) o ponés `USE_REDIS_CACHE=False` en el `.env`. Con `True` y sin
> servidor Redis, el fallo aparece en la primera escritura al caché, en producción.
>
> Y aunque no uses Redis, el caché en memoria es **por proceso**: con varios workers
> cada uno tiene su copia y no se invalidan entre sí. Para el portal público no es
> grave; para el panel de administración, donde un estado obsoleto se traduce en
> "no veo el cliente que acabo de crear", conviene Redis.

Todo lo que se instala en el servidor está versionado en `deploy/`. Son **ejemplos
deterministas sin secretos**: lo que cambia por instalación son rutas, dominio y
certificados, y eso se edita en el servidor.

| Archivo | Para qué |
| --- | --- |
| `deploy/install.sh` | Instalador del servidor completo: sistema, usuario, base, app, Nginx y andamiaje de deploy. Idempotente, corre una vez por servidor |
| `deploy/deploy.sh` | La lógica del deploy, en un solo lugar. Se instala como `/usr/local/sbin/webcmp-deploy` |
| `deploy/deploy.env.example` | Config del deploy → `/etc/webcmp/deploy.env` |
| `deploy/systemd/*.service` | Gunicorn y el worker Huey |
| `deploy/nginx/webcmp.conf.example` | El vhost de Nginx cuando el TLS se termina en esta máquina |
| `deploy/nginx/webcmp-http.conf.example` | El vhost sin TLS, cuando el TLS lo termina un proxy externo |
| `deploy/sudoers/webcmp-deploy` | El sudoers acotado a un único comando |
| `deploy/README-deploy.md` | Los porqués del diseño, el rollback y los errores que solo salen en producción |

La separación es deliberada: **toda la lógica del deploy vive en
`deploy/deploy.sh`** y el workflow es un cliente SSH delgado que lo invoca. Un
deploy que se puede correr a mano desde el servidor es un deploy que se puede
depurar; uno que solo existe como YAML de GitHub no.

### 1. Prerrequisitos de sistema

```bash
sudo apt update
sudo apt install -y \
  git python3.14 python3.14-venv postgresql nginx redis-server \
  build-essential pkg-config libcairo2-dev libpango1.0-dev \
  python3-dev wkhtmltopdf
```

Faltan tres paquetes en la lista corta, y sin ellos ni `pip install` ni `manage.py`
llegan a empezar:

| Paquete | Por qué |
| --- | --- |
| `build-essential` | Python 3.14 no tiene ruedas para varios pins de `prod.txt` y hay que compilar |
| `libpango1.0-dev` | WeasyPrint (PDF) lo carga por cffi |
| `redis-server` | El `.env` de producción trae `USE_REDIS_CACHE=True` |

Los dos primeros fallan con errores que no dicen qué falta:

```bash
# Sin build-essential
#   Running cc --version gave [Errno 2] No such file or directory: 'cc'

# Sin libpango1.0-dev
#   ffi.dlopen('libpango-1.0.so.0')
```

> Ojo con el nombre: en Ubuntu 24.04+ es `libpango1.0-dev`, **no**
> `libpango-1.0-dev`.

Usuario dedicado del servicio:

```bash
# El servicio NUNCA corre como root, y este usuario no tiene shell a proposito:
# existe para correr Gunicorn y Huey, no para que alguien se loguee.
sudo useradd -r -s /bin/false webcmp
sudo mkdir -p /srv/webcmp && sudo chown webcmp:webcmp /srv/webcmp
```

### 2. Base de datos

El rol de PostgreSQL y el del servicio son el mismo (`webcmp`), y la base es
propiedad de ese rol. `psycopg[binary]` va en `requirements/base.txt`, así que lo
traen los tres entornos: es el driver del motor, no una herramienta de
desarrollo.

```bash
sudo -u postgres psql <<'SQL'
CREATE USER webcmp WITH PASSWORD 'CAMBIAR_ESTA_CLAVE';  -- pragma: allowlist secret
CREATE DATABASE webcmp OWNER webcmp;
\c webcmp
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO webcmp;
SQL
```

El `GRANT` explícito sobre el esquema `public` no es redundante con el `CREATE
DATABASE OWNER`: en PostgreSQL 15+ `PUBLIC` ya no tiene `CREATE` en `public`, pero
sin ese `GRANT` el rol tampoco puede escribirlo, y `migrate` falla al crear la
primera tabla.

Si vas a correr la suite de tests en el mismo servidor, el rol necesita poder
crear la base de prueba:

```bash
sudo -u postgres psql -c 'ALTER ROLE webcmp CREATEDB'
```

Sin eso, `python manage.py test` muere con `permission denied to create database`
después de encontrar los 1184 tests.

Cambiar la contraseña después es en **los dos lados**: el rol y el `DB_PASS` del
`.env`. Si la cambiás en uno solo, cada request muere con `password authentication
failed for user "webcmp"`, que parece un problema de red y no de clave.

```bash
sudo -u postgres psql -c "ALTER USER webcmp WITH PASSWORD 'NUEVA_CLAVE'"
sudo nano /srv/webcmp/.env      # DB_PASS
sudo chown webcmp:webcmp /srv/webcmp/.env && sudo chmod 600 /srv/webcmp/.env
sudo systemctl restart webcmp
```

### 3. Código, venv y `.env`

```bash
sudo -u webcmp git clone https://github.com/cmp-camaguey/webcmp.git /srv/webcmp
cd /srv/webcmp
sudo -u webcmp python3.14 -m venv .venv
sudo -u webcmp .venv/bin/pip install -r requirements/prod.txt

# `generate_env.py` tiene que correr como root: escribe /etc/webcmp/encryption.env,
# que es root con modo 600 y webcmp no puede crear. Correlo como webcmp falla y no
# escribe nada (falla cerrado, lo cual esta bien).
sudo install -d -m 0755 -o root -g root /etc/webcmp
sudo .venv/bin/python scripts/generate_env.py --production

# Las migraciones vienen versionadas en el clone, asi que `migrate` es suficiente.
sudo -u webcmp .venv/bin/python manage.py migrate
sudo -u webcmp .venv/bin/python manage.py collectstatic --no-input
```

> **Los `manage.py` de este bloque son un caso especial.** Corren a secas, sin el
> `systemd-run` que sí usan los pasos 6 y 7, y funcionan solo porque los dos
> comandos de acá (`migrate`, `collectstatic`) no necesitan la
> `ENCRYPTION_KEY`: leen el `.env` como webcmp y el perfil de desarrollo les sirve.
>
> El día que quieras correr **otro** `manage.py` a mano, ese no es el camino. Sin el
> `PRODUCTION=1` del unit caés al perfil de desarrollo y `migrate` se ejecuta
> contra **SQLite**: dice que todo bien y el sitio sigue en PostgreSQL. Para
> cualquier otro comando, el contexto correcto es el del paso 6, que es lo que
> hace `deploy/deploy.sh`.
>
> Y `systemd-run` va con `sudo`, nunca con `sudo -u webcmp`: necesita hablar con el
> systemd del sistema, y el drop de privilegios lo hace `--uid=webcmp`. Con `sudo
> -u webcmp systemd-run` el comando muere con `Failed to start transient service
> unit: Access denied`.

El script escribe dos archivos:

| Archivo | Contiene | Permisos |
| --- | --- | --- |
| `/srv/webcmp/.env` | `SECRET_KEY` cifrada, `DEBUG=False`, `DB_*`, SMTP | `600`, **webcmp** |
| `/etc/webcmp/encryption.env` | `ENCRYPTION_KEY` (clave de descifrado) | `600`, root |

El `.env` es de `webcmp` y no de root porque Django corre como ese usuario y tiene
que poder leerlo. La `ENCRYPTION_KEY` queda en root y el `.env` en 600: entrar al
grupo `webcmp` (lo necesita Nginx, ver el paso 5) no da acceso a ninguna de las dos.

Después de generar el `.env` hay que **cambiar el hostname**: el generador escribe
`EXTERNAL_HOSTNAME=www.meteocamaguey.cu` por default y no tiene flag para pasarlo, así
que hay que editar a mano `EXTERNAL_HOSTNAME`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS` y `CORS_ALLOWED_ORIGINS`. Si no, el sitio responde
`DisallowedHost` a cada petición.

```bash
sudo nano /srv/webcmp/.env
sudo chown webcmp:webcmp /srv/webcmp/.env && sudo chmod 600 /srv/webcmp/.env
sudo chown root:root /etc/webcmp/encryption.env && sudo chmod 600 /etc/webcmp/encryption.env

# `staticfiles/` y `media/` los necesita escribibles por el usuario del servicio,
# y `.cache/` para MPLCONFIGDIR: con `ProtectSystem=full` el worker no puede
# crearse solo ese directorio, y el unit lo declara en `ReadWritePaths=`.
sudo mkdir -p /srv/webcmp/{media,logs,staticfiles,.cache/matplotlib}
sudo chown -R webcmp:webcmp /srv/webcmp/{media,logs,staticfiles,.cache}
```

El `.env` sale con dos cosas que hay que completar antes de que el sitio sirva de
verdad:

| Variable | Qué hacer |
| --- | --- |
| `EXTERNAL_HOSTNAME`, `ALLOWED_HOSTS` | El dominio real |
| `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` | Idem |

Sin esas cuatro, cada petición es un `DisallowedHost`.

Y las de SMTP: `EMAIL_HOST_USER` y `EMAIL_HOST_PASSWORD` salen como
`CHANGE_ME_placeholder`, con lo que los correos no se van.

> `webcmp` es una cuenta de sistema **sin home**, así que pip no encuentra dónde
> escribir su caché y cada `pip install` baja todo desde PyPI. Dedicale uno:
> `sudo install -d -m 0700 -o webcmp -g webcmp /var/cache/webcmp-pip`. El deploy lo
> exporta solo (`PIP_CACHE_DIR` en `/etc/webcmp/deploy.env`).

> `collectstatic` **no es instantáneo**: son ~917 archivos y ~114 MB, y con
> `CompressedManifestStaticFilesStorage` genera además el `.gz` y el `.br` de cada
> uno. En esta máquina son unos 3 minutos. El deploy completo anda por los 5.

### 4. systemd

```bash
sudo cp deploy/systemd/webcmp.service deploy/systemd/webcmp-huey.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now webcmp webcmp-huey
sudo journalctl -u webcmp -f
```

Los logs van a journald, no a archivos.

> **Las rutas de `/srv/webcmp` están escritas en el `.service`.** En un unit,
> `APP_DIR=/srv/webcmp` no es una clave válida de systemd y systemd tampoco expande
> variables en `WorkingDirectory=` o `ReadWritePaths=`. Si el checkout cambia de
> lugar, se editan las rutas y se verifica:
> `systemd-analyze verify /etc/systemd/system/webcmp.service`.

> **`EnvironmentFile=-` lleva el guion a propósito.** Sin la clave de descifrado, el
> servicio arranca y falla con el error accionable de `load_secret_key()`. Sin el
> guion, fallaría systemd con un error que no dice qué hacer.

### 5. Nginx

Hay **dos topologías** y cambia solo qué vhost se copia. `deploy/install.sh`
resuelve la elección con la pregunta `PROXY_MODE`; lo que sigue es el camino
manual, equivalente.

#### Con TLS en esta máquina

`PROXY_MODE=nginx-local`: Nginx hace TLS y hace de proxy.

```bash
sudo cp deploy/nginx/webcmp.conf.example /etc/nginx/sites-available/webcmp.conf
sudo nano /etc/nginx/sites-available/webcmp.conf   # certificados y rutas
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/webcmp.conf /etc/nginx/sites-enabled/

# www-data tiene que poder tocar el socket de Gunicorn, que sale
# 0770 webcmp:webcmp. Sin esto Nginx devuelve 502 en cada respuesta y el log dice
# "connect() failed (13: Permission denied)", que no parece un problema de grupo.
sudo usermod -aG webcmp www-data

sudo nginx -t && sudo systemctl restart nginx
```

#### Detrás de un proxy externo

`PROXY_MODE=external`: el TLS lo termina un Nginx Proxy Manager (NPM) que corre
**en otra máquina**. Acá Nginx escucha HTTP, sirve `/static/` y `/media/`, y hace
proxy a Gunicorn por el socket.

```bash
sudo cp deploy/nginx/webcmp-http.conf.example /etc/nginx/sites-available/webcmp.conf
sudo nano /etc/nginx/sites-available/webcmp.conf   # server_name y rutas
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/webcmp.conf /etc/nginx/sites-enabled/

sudo usermod -aG webcmp www-data
sudo nginx -t && sudo systemctl restart nginx
```

En NPM, el upstream es:

| Campo | Valor |
| --- | --- |
| Scheme | `http` (NPM habla HTTP con el servidor; el TLS lo pone él) |
| Forward Hostname / IP | IP de LAN de este servidor |
| Forward Port | puerto HTTP del vhost (80 por defecto) |
| Websockets Support | on (el gráfico de sounding las usa) |
| SSL | Let's Encrypt en NPM; el dominio tiene que resolver a NPM |

No hacen falta Custom Locations: el vhost local ya sirve `/static/` y `/media/`.
El puerto HTTP tiene que quedar alcanzable **solo** desde la máquina del NPM:

```bash
sudo ufw allow from <IP_DEL_NPM> to any port 80 proto tcp
sudo ufw deny 80/tcp
```

En esta topología el health check del deploy apunta a `http://` en
`127.0.0.1:$HTTP_PORT`, no al 443: ese puerto acá no lo escucha nadie. Sin
`HEALTHCHECK_URL` y `HEALTHCHECK_RESOLVE` en `/etc/webcmp/deploy.env`, el deploy
falla por construcción.

> **Reinicio de Nginx, no `reload`.** Los grupos suplementarios se leen al crear
> los workers. Un `reload` alcanza para leer la config, pero si acabás de cambiar
> la pertenencia a un grupo, reiniciá el servicio.

> **`X-Forwarded-Proto` es el header que rompe el login, en las dos direcciones.**
> Django lo lee con `SECURE_PROXY_SSL_HEADER` para resolver `request.is_secure()`.
> Si falta, las cookies de sesión seguras no se envían y el usuario entra en un
> bucle de logout sin un solo error en el log.
>
> - Con TLS acá (`webcmp.conf.example`) la línea correcta es
>   `proxy_set_header X-Forwarded-Proto $scheme;`. **No la quites.**
> - Detrás de un proxy externo (`webcmp-http.conf.example`) esa misma línea es el
>   bug: `$scheme` vale `http` y **sobrescribe** el `https` que manda NPM. Ese
>   vhost usa un `map` que **propaga** el valor recibido y cae a `$scheme` solo
>   cuando no viene header. No lo reemplaces por el `$scheme` del otro vhost.

> **`Host` está repetido dentro de `location /`, y no es una redundancia.** En
> Nginx, `proxy_set_header` se hereda del `server` solo si el `location` no declara
> ninguno. `location /` declara dos (para el websocket), así que los del `server`
> se descartan ahí y el `Host` vuelve al default, que es `$proxy_host`: el nombre
> del upstream. Gunicorn recibía `Host: webcmp` y contestaba `DisallowedHost` en
> todas las peticiones. Si sacás esas líneas, el sitio entero responde 400. Aplica
> a los dos vhosts.

### 6. Datos iniciales y usuario administrador

Una base migrada está vacía: sin municipios ni estaciones, las páginas de pronóstico
no tienen contra qué renderizar.

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py add_stations_data
```

Toma `--provincia` (default `Camagüey`) y `--tipo` (`municipios`, `estaciones` o
`todo`, que es el default).

El usuario administrador se crea con `createsuperuser`. Hay dos formas, y la
diferencia importa:

```bash
# Interactivo (la normal). -t le da terminal al comando.
sudo systemd-run --pipe --wait --uid=webcmp -t \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py createsuperuser

# No interactivo, para un servidor que se arma por script.
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 \
  -p Environment=DJANGO_SUPERUSER_PASSWORD='CONTRASENA_LARGA_Y_UNICA' \ # pragma: allowlist secret
  --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py createsuperuser \
    --noinput --username admin --email admin@insmet.cu
```

> **La contraseña va adentro del `systemd-run`, no en tu shell.**
> `systemd-run` no hereda el entorno de quien lo invoca, así que
> `DJANGO_SUPERUSER_PASSWORD=x systemd-run ... createsuperuser` **crea el usuario
> con una contraseña inservible y no avisa**. El síntoma es un login que siempre
> rechaza la clave. Por eso va como `-p Environment=DJANGO_SUPERUSER_PASSWORD=...`.
>
> `--uid=webcmp` tampoco es opcional: sin él el comando corre como root, no puede
> leer el `.env` (600 de `webcmp`) y falla con un error de descifrado que parece un
> problema de claves.

Después del primer login, el usuario va a ser redirigido a
`/accounts/profile/update/` en cada navegación hasta que complete email, nombre y
apellido. No es un bug: es `CheckUserProfileMiddleware` (`apps/core/middleware.py`),
que manda a cualquier usuario con el perfil incompleto a esa página. Completalo y
seguí.

Para crear más usuarios sin panel:

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py shell -c \
  "from django.contrib.auth.models import User; \
   User.objects.create_superuser('nombre', 'correo@insmet.cu', 'CONTRASENA')"
```

### 7. Verificar

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py check --deploy

curl -I https://www.meteocamaguey.cu
```

`check --deploy` tiene que salir con 0 issues. Lo único tolerado es `security.W008` (el
TLS lo termina Nginx, no Django) y está silenciado en `config/settings/base.py`.

Con proxy externo, ese `curl` pasa por NPM y prueba la cadena completa. Para
validar solo la capa local del servidor —vhost, `/static/` y `/media/`— sin salir
de él: `curl -sSI http://127.0.0.1/`.

### 8. Deploy automático con GitHub Actions

Hasta acá el servidor sirve, pero cada cambio hay que desplegarlo a mano. A partir
de acá, `main` en verde se despliega solo.

#### 8.1 Instalar el andamiaje en el servidor (una vez)

El deploy por workflow no es autocontenido: la primera vez hay que dejar el
servidor armado —usuario de despliegue, su clave, el script, la config, el
sudoers y el resto del andamiaje—.

```bash
sudo ./deploy/install.sh --dry-run   # muestra qué haría, sin tocar nada
sudo ./deploy/install.sh
```

Qué hace, y por qué en ese orden:

| Paso | Qué instala |
| --- | --- |
| 1 | Prerrequisitos de sistema por `apt` y verificación de la versión de Python |
| 2 | Usuario `webcmp` + `media/`, `logs/`, `staticfiles/`, `.cache/` |
| 3 | Clon del repo en `APP_DIR`, propiedad de `webcmp` |
| 4 | Rol y base de datos en PostgreSQL, con las credenciales que se preguntaron |
| 5 | `venv` + `requirements/prod.txt` |
| 6 | `.env` con `scripts/generate_env.py` y los valores preguntados, sin `CHANGE_ME` |
| 7 | gate de migraciones, `migrate`, `collectstatic`, `check --deploy` |
| 8 | Unidades systemd renderizadas a `APP_DIR` y al usuario reales |
| 9 | Nginx en las dos topologías, y el vhost que corresponde a `PROXY_MODE` |
| 10 | `/etc/webcmp/deploy.env` con el destino del health check que corresponde |
| 11 | Andamiaje de deploy: usuario `deploy`, keypair, sudoers y `webcmp-deploy` |
| 12 | Superusuario, por `systemd-run` con la `ENCRYPTION_KEY` |

Es idempotente: correrlo dos veces no cambia nada y no regenera secretos. Importa
porque el script se puede necesitar correr más de una vez, y porque cada paso que
regenera una clave o pisa una config editada a mano convierte un reintento en un
incidente.

A propósito **no** hace: tocar el firewall (imprime los comandos de `ufw`),
configurar el proxy externo (imprime los datos para NPM), crear los secrets del
environment de GitHub, ni tomar snapshots. Son decisiones que dependen de la red
real del sitio, y automatizarlas a ciegas deja más basura de la que recoge.

> **Por qué el script vive en `/usr/local/sbin` y no en `/srv/webcmp/deploy/`.** El
> deploy corre como root y el checkout es escribible por `webcmp`. Con el sudoers
> de `deploy/sudoers/webcmp-deploy`, tener el script dentro del checkout sería una
> escalada trivial: alcanza con editar ese archivo para ejecutar root. En
> `/usr/local/sbin`, propiedad de root, el usuario de despliegue no puede escribirlo.
>
> Y las reglas de sudoers son **estrechas a propósito**: el script acepta el commit
> de destino por argumento, así que con un `(ALL) NOPASSWD: ALL` cualquier SHA de
> cualquier rama llegaría a producción.

Editá `/etc/webcmp/deploy.env` antes de seguir. Lo que más se olvida:

```bash
sudo nano /etc/webcmp/deploy.env
```

| Variable | Por qué importa |
| --- | --- |
| `PUBLIC_HOSTNAME` | Debe ser el `server_name` de Nginx y el `EXTERNAL_HOSTNAME` del `.env` |
| `HEALTHCHECK_URL` | Qué se consulta. Vacío = `https://<PUBLIC_HOSTNAME>/` |
| `HEALTHCHECK_RESOLVE` | `curl --resolve host:port:ip`. Vacío = `<PUBLIC_HOSTNAME>:443:127.0.0.1` |
| `HEALTHCHECK_INSECURE` | Ver abajo |

`PUBLIC_HOSTNAME` tiene que ser **exactamente** esos otros dos, porque el health
check entra con ese nombre como SNI y como `Host`: si no coinciden, TLS no encaja
y el deploy falla aunque el sitio esté perfecto.

Los defaults del health check (`443` en `127.0.0.1`) son los de Nginx con TLS en
esta máquina. Con proxy externo hay que cambiarlos, porque el 443 de acá no lo
escucha nadie:

```bash
HEALTHCHECK_URL=http://www.meteocamaguey.cu/
HEALTHCHECK_RESOLVE=www.meteocamaguey.cu:80:127.0.0.1
```

Así el health check atraviesa el Nginx local —vhost, `/media/` y socket
incluidos— en vez de ir directo a Gunicorn, que dejaba en verde un vhost roto.
`webcmp-deploy --configure` pregunta lo mismo y propone los defaults según el
modo.

Mientras `HEALTHCHECK_INSECURE=1`, el health check corre `curl --insecure` y deja de
detectar un certificado vencido o con nombre incorrecto. Sacalo cuando tengas un
certificado de una CA real.

#### 8.2 Secrets de GitHub

En **Settings → Environments → `production`** (no secrets del repo: el environment
puede pedir aprobación humana, y las credenciales de producción no quedan
visibles desde cualquier workflow).

| Secret | Qué es |
| --- | --- |
| `DEPLOY_HOST` | IP o hostname del servidor |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | La clave **privada** ed25519 en PEM, con `BEGIN`/`END` |
| `DEPLOY_SSH_KNOWN_HOSTS` | Salida de `ssh-keyscan -p 22 <host>` |

```bash
# El .pub del keypair que creó install.sh
cat /home/deploy/.ssh/id_ed25519.pub

# El known_hosts del servidor
ssh-keyscan -p 22 <host-del-servidor>
```

`DEPLOY_SSH_KNOWN_HOSTS` no es decorativo: el workflow usa
`StrictHostKeyChecking=yes`. Va ahí, y no con `accept-new`, porque `accept-new`
acepta cualquier clave la primera vez — un deploy que no verifica a quién se conecta
le entrega la clave de despliegue a cualquiera que responda ese puerto.

> **Los runners de GitHub no llegan a una IP privada.** Si `DEPLOY_HOST` es algo en
> `192.168.x.x` o `10.x.x.x`, el job se queda colgado en `ConnectTimeout=20` y falla.
> Hace falta una IP pública alcanzable, un runner self-hosted dentro de la red, o
> un bastion.

#### 8.3 Qué dispara el deploy

`.github/workflows/deploy-production.yml` corre en dos casos:

| Disparador | Qué hace |
| --- | --- |
| `workflow_run` sobre CI, con `conclusion == success` y `branches: [main]` | Despliega el commit que quedó en verde |
| `workflow_dispatch` | Manual, desde la pestaña Actions |

`workflow_dispatch` es el que se usa para los dos casos raros: un rollback
apegado a un SHA viejo, y un primer deploy antes de fiarse del automático.

Hoy los dos están **apagados a propósito**: no existe servidor de producción, así
que el job tiene `if: vars.DEPLOY_ENABLED == 'true'` y se salta en vez de fallar
por secrets que todavía no se pueden cargar. Se enciende creando esa variable de
repositorio, después de cargar los cuatro secrets.

El concurrency group es `deploy-production` con `cancel-in-progress: false`: dos
deploys se ejecutan **uno después del otro**, nunca superpuestos. Cancelar el que
está corriendo para arrancar otro deja el servidor en un estado intermedio que
nadie pidió.

#### 8.4 Qué hace el deploy

En orden, y todo en `deploy/deploy.sh`:

```text
flock → aborted si hay cambios sin commitear → checkout del SHA → pip install
      → makemigrations --check → check --deploy → migrate → collectstatic
      → restart → health check
```

El `flock` del principio no es un detalle: dos pushes seguidos no pueden migrar a
la vez. Y el `check --deploy` va **antes** de `migrate` a propósito, para que un
deploy con la configuración rota no llegue a tocar el esquema.

Dos cosas que parecen raras y no lo son:

**`makemigrations --check` y no `makemigrations`.** El proyecto versiona las
migraciones, así que en un clone limpio ya existen. El deploy verifica con
`--check` que el árbol cubre los modelos y aborta antes de tocar la base si falta
una migración; `makemigrations` a secas generaría el archivo en el servidor y lo
aplicaría sin revisión.

**`check --deploy` contra la configuración real del servidor**, no el del CI. El del
CI corre sin `ENCRYPTION_KEY` ni con el par de testing, y pasa sobre cosas que en
producción no. Este es el gate que de verdad importa, y corre con los valores con
los que el sitio va a servir.

#### 8.5 Rollback

Automático: si algo falla **después** de que el checkout se movió, un
`trap rollback EXIT` devuelve el servidor al commit anterior y reinicia los
servicios. Si el fallo es antes de mover nada, no hay nada que deshacer.

```bash
# Rollback manual a un SHA que ya se sabe que es bueno
sudo /usr/local/sbin/webcmp-deploy <sha>
sudo journalctl -u webcmp -n 100      # ver qué pasó
```

**El primer deploy en serio: hacelo a mano.** Un `sudo /usr/local/sbin/webcmp-deploy`
con un SHA conocido te dice si el servidor está listo sin meter un workflow de
GitHub en el medio. Recién después, probá el automático.

Los porqués del diseño y la tabla de errores que solo aparecen en producción están
en [`deploy/README-deploy.md`](deploy/README-deploy.md).

### 9. Operación

```bash
sudo systemctl restart webcmp   # reinicio sin cortar conexiones
sudo systemctl reload webcmp    # recarga de workers (SIGHUP)
sudo journalctl -u webcmp --since "1 hour ago" -p err
```

Rotar claves (invalida sesiones y cookies firmadas):

```bash
sudo -u webcmp .venv/bin/python scripts/generate_env.py --production --rotate-keys
sudo systemctl restart webcmp
```

Limpiar archivos huérfanos de `media/` (tiene `--dry-run`):

```bash
sudo -u webcmp .venv/bin/python manage.py cleanup_orphan_media --dry-run
```

<details>
<summary>Si venís de supervisor + gunicorn.sh</summary>

1. `sudo systemctl stop supervisor` (o sacá los programas de
   `/etc/supervisor/conf.d/webcmp.conf` y `supervisorctl update`).
2. Seguí los pasos 3 a 7 en el servidor nuevo.
3. Nginx debe apuntar al socket nuevo: `unix:/run/webcmp/gunicorn.sock`.
4. `gunicorn.sh` ya no está en el repo; el unit lo reemplaza con `ExecStart` directo.

</details>

---

## Contribuir

El proyecto usa un ciclo de trabajo con registro: los cambios con impacto se
describen en `openspec/changes/<NNN-nombre>/` antes de implementarse, y
`AGENTS.md` describe las convenciones que hay que respetar. Es el mismo flujo que
usa la documentación interna; no es burocracia de comité, es lo que permite
retomar un trabajo a medias.

Un cambio en OpenSpec son cuatro archivos, y el `tasks.md` es el que se va
marcando:

```text
openspec/changes/030-lo-que-sea/
├── proposal.md              # por qué y qué resuelve
├── design.md                # cómo, con las decisiones técnicas
├── tasks.md                 # checkboxes; se implementa una tarea a la vez
└── specs/030-lo-que-sea/spec.md
```

Cuando el cambio llega a producción, `gentle-ai sdd-archive` promueve los specs
y mueve el directorio a `openspec/changes/archive/<fecha>-<nombre>/`.

### Ramas y PRs

Dos ramas de larga vida: `dev` integra, `main` es producción. **Las features
entran siempre por `dev`**; `main` sólo recibe promociones por lote y hotfixes.

1. `git checkout dev && git pull`, y desde ahí `git checkout -b feat/030-lo-que-sea`
   (o `fix/…`, `chore/…`).
2. Commits conventional, en español: `feat(meteo): agregar pronóstico de 5 días`.
   Un commit es una unidad de trabajo revisable, con sus tests y su documentación
   adentro.
3. `python manage.py test` en verde antes de pushear. Los hooks de pre-commit
   (ruff, djlint, detect-secrets) corren solos si hiciste `make setup`.
4. PR **contra `dev`**, que se fusiona en squash: un PR es un commit en el
   historial y la rama corta no necesita merge commit.
5. Al fusionarse, un workflow borra la rama.

`dev` no exige aprobación: el gate son los required status checks. `main` sí
exige una aprobación humana —`@orl92` o `@yoelvismr`, vía `.github/CODEOWNERS`—,
porque el CI que da el verde lo define el propio PR y un bot no puede ser code
owner.

### Promoción a producción

1. Con `dev` verde, PR `dev` → `main`. Es una promoción por lote, no feature por
   feature: `dev` acumula y `main` recibe un bloque cerrado.
2. Una persona aprueba y se fusiona con **merge commit** (las features entran por
   squash; la promoción no).
3. Se etiqueta el merge: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.
4. **Inmediatamente**, PR `main` → `dev`. El merge de release crea en `main` un
   commit que `dev` no tiene, y con `strict_required_status_checks_policy` la
   próxima promoción saldría `BEHIND` aunque el contenido sea idéntico: la
   comprobación es de ascendencia de commits, no de árboles.

Un hotfix sale de `main`, pero vuelve a `dev` igual que un release. Si se omite,
`dev` deja de representar lo que corre en producción.

### Qué corre el CI

El job de tests es selectivo por app, no la suite completa:

- En un PR, `dorny/paths-filter` detecta qué apps cambiaron (`apps/<app>/**`) y
  corre `python manage.py test apps.<app>` sólo para ésas.
- Los cambios globales —`config/**`, `manage.py`, `requirements/**`,
  `pyproject.toml`, `.github/**`, `templates/**`, `static/**`— fuerzan la suite
  completa.
- El push a `main` y cada merge de release siempre corren la suite completa, como
  red de seguridad.

Los labels de app van con la ruta completa (`apps.meteo`, no `meteo`): en Django
5.2 el label corto ya no resuelve el paquete de tests.

Ocho jobs son required status checks en las dos ramas: *Detect affected apps*,
*Tests (Django)*, *Lint (Ruff)*, *Templates (djlint)*, *Pre-commit hooks*,
*Bandit SAST*, *Security (pip-audit + migrations)* y *Deploy check (check
--deploy)*. Si un PR renombra o borra uno de esos jobs, el check requerido nunca
reporta y el merge queda bloqueado: **fail-closed** a propósito.

Autores: el Instituto de Meteorología de Cuba.

## Licencia

MIT. Ver [LICENSE](LICENSE).

Centro Meteorológico Provincial Camagüey — Instituto de Meteorología de Cuba.
