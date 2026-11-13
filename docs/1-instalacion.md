# 1 · Instalación

De un clon vacío a `runserver` en la máquina local. El servidor de producción se
instala aparte: ver [Despliegue](../deploy/README-deploy.md).

## Requisitos

| Componente | Nota |
| --- | --- |
| Python | 3.14. El CI la fija y ruff apunta a `py314` |
| PostgreSQL | Servidor local y cliente `psql` |
| `wkhtmltopdf` | Binario del sistema para los PDFs. `pip` no lo instala |

En Debian/Ubuntu:

```bash
sudo apt install build-essential pkg-config python3-dev \
  libcairo2-dev libpango1.0-dev wkhtmltopdf postgresql
```

| Paquete | Para qué |
| --- | --- |
| `build-essential` | Compila los pins que no tienen rueda para Python 3.14 |
| `libpango1.0-dev` | WeasyPrint lo carga por cffi. El nombre no es `libpango-1.0-dev` |
| `wkhtmltopdf` | Genera los PDFs de facturas, avisos y reportes |

## 1. Entorno y dependencias

```bash
git clone https://github.com/cmp-camaguey/webcmp.git
cd webcmp
make setup
```

`make setup` crea `.venv`, instala `requirements/dev.txt` y `requirements/test.txt`,
genera el `.env` de desarrollo y activa los hooks de git. Si el `.env` ya existe
no lo toca.

El mismo resultado a mano:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
python scripts/generate_env.py --development
git config core.hooksPath .githooks
```

## 2. Base de datos

El rol necesita `CREATEDB`: la suite de tests crea y destruye su propia base.

```bash
sudo -u postgres psql <<'SQL'
CREATE USER webcmp_dev WITH PASSWORD 'CAMBIAR_ESTA_CLAVE' CREATEDB;  -- pragma: allowlist secret
CREATE DATABASE webcmp_dev OWNER webcmp_dev;
SQL
```

El `.env` que generó `make setup` ya apunta a esa base:

```dotenv
DB_ENGINE=postgresql
DB_NAME=webcmp_dev
DB_USER=webcmp_dev
DB_PASS=CAMBIAR_ESTA_CLAVE
DB_HOST=127.0.0.1
DB_PORT=5432
DB_SSL_MODE=prefer
```

Si usás otra contraseña, cambiala en los dos lados: en el rol y en `DB_PASS`. En
uno solo, cada consulta falla con `password authentication failed`.

## 3. Preparar la base

```bash
python manage.py migrate             # las migraciones vienen versionadas
python manage.py add_stations_data   # provincias, municipios y estaciones
python manage.py create_observation_groups
python manage.py createsuperuser
```

`add_stations_data` acepta `--tipo municipios|estaciones|todo` y `--provincia`;
sin argumentos carga todo lo de Camagüey.

`create_observation_groups` crea el grupo `Estacion <número> - <rol>` de cada
estación (jefe, revisor y observador) con los permisos de observaciones que
corresponden. Se puede volver a correr tras agregar estaciones: no duplica
grupos ni quita permisos agregados a mano.

## 4. Arrancar

```bash
python manage.py runserver
```

En `http://127.0.0.1:8000/`. El perfil de desarrollo usa PostgreSQL y manda el
correo a la consola. El worker de Huey, que genera los PDFs y envía los correos,
corre en otra terminal:

```bash
./run_huey.sh
```

Para probar el flujo completo de factura y correo, ver
[2 · Desarrollo](2-desarrollo.md#worker-y-correos).

## Dependencias por entorno

Cada archivo es autocontenido y se instala con un solo `pip install -r`.

| Archivo | Instala |
| --- | --- |
| `requirements/base.txt` | Runtime. Sin gunicorn ni herramientas de desarrollo |
| `requirements/dev.txt` | `base.txt` + ruff, bandit, pip-audit, djlint, pre-commit, django-debug-toolbar, setuptools, wheel |
| `requirements/test.txt` | `base.txt` + django-debug-toolbar |
| `requirements/prod.txt` | `base.txt` + gunicorn, whitenoise, redis |

## Targets de `make`

| Target | Qué hace |
| --- | --- |
| `make setup` | venv + dependencias + `.env` + hooks |
| `make env` | Genera el `.env` solo si falta. Requiere el venv creado |
| `make env-force` | Regenera el `.env` conservando el par de claves |
| `make check` | `manage.py check` |
| `make test` | Suite completa + tests de los scripts de deploy |
| `make test-deploy` | Solo los tests de `deploy/` y `run_huey.sh` |
| `make lint` | `ruff check` + `ruff format --check` |
| `make djlint` | Lint de templates |
| `make audit` | `pip-audit` sobre las dependencias de runtime |
