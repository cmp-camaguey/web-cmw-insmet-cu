# dev-postgres-obligatorio — requisitos delta

## Requirements

### Requirement: DB-ENGINE-OBLIGATORIO

Todo perfil de `config.settings` SHALL construir `DATABASES` a partir de
`DB_ENGINE` y SHALL fallar con `ImproperlyConfigured` si esa variable falta o
está vacía.

#### Scenario: Sin DB_ENGINE en desarrollo

- GIVEN un `.env` con `DEBUG=True` y sin `DB_ENGINE`
- WHEN arranca el perfil `dev`
- THEN el arranque falla con `ImproperlyConfigured`
- AND el mensaje nombra `DB_ENGINE` y el comando que regenera el archivo

#### Scenario: Sin DB_ENGINE en CI

- GIVEN un entorno sin `PRODUCTION` y sin `DB_ENGINE`
- WHEN arranca el perfil `testing`
- THEN el arranque falla con `ImproperlyConfigured`
- AND no se crea ningún archivo `db.sqlite3`

### Requirement: SQLITE-RECHAZADO

`DB_ENGINE` con valor `sqlite` o `sqlite3` SHALL ser rechazado en los tres
perfiles mediante `ImproperlyConfigured`.

#### Scenario: `.env` heredado de una instalación anterior

- GIVEN un `.env` con `DB_ENGINE=sqlite3`
- WHEN arranca cualquier perfil
- THEN el arranque falla con `ImproperlyConfigured`
- AND el mensaje menciona SQLite, PostgreSQL y `generate_env.py`
- AND el mensaje advierte que los datos existentes no se convierten solos

#### Scenario: Alias del mismo motor

- GIVEN `DB_ENGINE=sqlite`
- WHEN se construye la configuración
- THEN el resultado es el mismo rechazo que con `sqlite3`

### Requirement: CI-USA-POSTGRESQL

Los jobs de CI que importan settings y ejecutan migraciones SHALL declarar un
service container de PostgreSQL y las cuatro credenciales `DB_*`.

#### Scenario: Suite en un pull request

- GIVEN el job `Tests (Django)` con el service `postgres:18`
- WHEN corre la suite
- THEN Django crea y destruye su base de tests en ese servidor
- AND ningún paso cae a SQLite

#### Scenario: Migraciones en el job de seguridad

- GIVEN el job `Security (pip-audit + migrations)`
- WHEN ejecuta `makemigrations` y `migrate`
- THEN las migraciones se aplican sobre PostgreSQL
- AND el healthcheck evita leer una carrera de arranque como error de settings

### Requirement: ENV-DE-DESARROLLO-POSTGRESQL

`python scripts/generate_env.py --development` SHALL escribir un bloque de base
de datos que apunte a PostgreSQL local y que arranque sin edición manual.

#### Scenario: Clon nuevo

- GIVEN un repositorio recién clonado y un PostgreSQL local con el rol
  `webcmp_dev`
- WHEN se ejecuta `python scripts/generate_env.py --development`
- THEN el `.env` contiene `DB_ENGINE=postgresql`, `DB_NAME=webcmp_dev`,
  `DB_USER=webcmp_dev`, `DB_HOST=127.0.0.1` y `DB_PORT=5432`
- AND `python manage.py migrate` funciona sin editar el archivo
