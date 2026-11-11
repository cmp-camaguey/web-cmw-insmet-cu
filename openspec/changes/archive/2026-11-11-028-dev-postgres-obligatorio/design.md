# 028-dev-postgres-obligatorio — diseño

## Una sola función, sin parámetros

`config/settings/base.py`:

```text
get_database_config()          -> exige DB_ENGINE
  sin DB_ENGINE                -> ImproperlyConfigured
  sqlite | sqlite3             -> ImproperlyConfigured
  falta DB_NAME/USER/PASS/HOST -> ImproperlyConfigured
  postgresql                   -> dict completo, PORT=5432, CONN_MAX_AGE=600
```

`prefer_sqlite` e `is_production` desaparecen: eran los dos ejes que permitían
que el mismo motor se comportara distinto según el perfil. El motor ahora es el
mismo dato del entorno en los tres casos.

`config/settings/__init__.py` conserva su envoltorio, pero ya no lee `DEBUG` ni
`IS_PRODUCTION`: solo delega. Se mantiene como punto de importación estable para
tests y consumidores.

## Por qué el rechazo es explícito y no un `KeyError`

`DB_ENGINE=sqlite3` tiene que fallar en la pregunta correcta. Si se dejara caer
en la validación genérica de credenciales, el mensaje diría "falta DB_NAME" y
escondería la causa real. El mensaje nombra SQLite, nombra PostgreSQL como
reemplazo, dice cómo regenerar el `.env` y advierte que los datos de `db.sqlite3`
no se convierten solos.

Las llamadas a `sqlite` y `sqlite3` comparten rama: son el mismo motor con dos
nombres, no dos caminos.

## CI: el motor tiene que existir de verdad

`test` y `security` pasan a declarar `services.postgres` con `postgres:18` y las
`DB_*` a nivel de job. `security` lo necesita porque corre `makemigrations` y
`migrate`; sin base, el paso fallaría antes de auditar nada.

`deploy-check` ya traía `DB_*` y se queda como está: `check --deploy` no abre
conexión, solo valida settings.

El service cuenta con `pg_isready` como healthcheck. Sin él, el job puede correr
`manage.py check` mientras el servidor todavía está arrancando, y el fallo se
lee como error de configuración cuando es una carrera.

## Qué se rompe a propósito

- Un `.env` con `DB_ENGINE=sqlite3` deja de arrancar hasta que se edite. Es el
  punto del cambio: el archivo viejo era la única cosa que seguía midiendo
  SQLite.
- `manage.py` ahora necesita PostgreSQL corriendo incluso para `test` y `check`.
  Es el precio de que CI y desarrollo compartan el motor.

## Verificación

- `apps/core/tests/test_settings_profiles.py`: `SqliteIsNotSupportedTests`
  repite el rechazo en los tres perfiles, con `sqlite` y `sqlite3`.
- `apps/core/tests/test_production_settings.py`: `DatabaseConfigTests` fija
  fail-closed, el rechazo de SQLite y la exigencia de credenciales.
- `apps/core/tests/test_generate_env_script.py`: el `.env` de desarrollo emite
  `postgresql` y las credenciales locales.
- Suite completa ejecutada contra PostgreSQL local real.
