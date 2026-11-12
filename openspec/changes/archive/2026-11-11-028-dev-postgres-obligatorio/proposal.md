# 028-dev-postgres-obligatorio

> **Estado: implementado en la rama `feat/dev-postgres-only`.** Sustituye el
> opt-in de 027 por un único motor: PostgreSQL en los tres perfiles.

## Por qué

027 dejó PostgreSQL disponible en desarrollo detrás de `DEV_USE_DB_ENGINE=True`.
Eso resolvía "puedo probar el motor de producción", pero dejaba dos verdades:
SQLite por defecto y PostgreSQL cuando alguien se acordaba del flag. La suite
verde de SQLite no decía nada sobre el motor que corre en producción, y el `.env`
local seguía apuntando a `db.sqlite3`.

Con dos motores soportados, cada query tiene dos comportamientos posibles y solo
uno se ejecuta en CI. Quitar SQLite elimina esa ambigüedad: lo que pasa en
desarrollo es lo que pasa en el servidor.

## Qué

- `get_database_config()` no recibe parámetros: `DB_ENGINE` es obligatorio en
  `dev`, `testing` y `production`.
- `DB_ENGINE=sqlite` o `sqlite3` falla con `ImproperlyConfigured` y un mensaje
  que dice qué hacer y advierte que los datos no se convierten solos.
- `scripts/generate_env.py --development` emite PostgreSQL local
  (`webcmp_dev` en `127.0.0.1:5432`).
- CI corre `test` y `security` contra un service container `postgres:18`.
- Se corrigen README, AGENTS.md y los comentarios de `deploy/` que describían el
  respaldo a SQLite.

## Qué NO es

- **No migra datos de `db.sqlite3`.** Un volcado de datos es una operación
  aparte, con su propia verificación. El mensaje de error lo dice explícitamente.
- **No toca la cola de Huey.** `SqliteHuey` y `SqliteStorage` son el backend de
  tareas, no el motor de la base: `config/huey.py` sigue igual y
  `apps/commercial/tests/test_send_test_invoice.py` conserva su cola temporal.
- **No cambia producción.** El perfil `production` ya exigía PostgreSQL desde
  antes; pierde un parámetro, no un comportamiento.
- **No agrega un Dockerfile.** Emular producción es 029; esto es solo el motor.

## Criterios de aceptación

1. Sin `DB_ENGINE`, ningún perfil arranca y el mensaje nombra la variable.
2. `DB_ENGINE=sqlite3` se rechaza en los tres perfiles, con mensaje accionable.
3. Con PostgreSQL completo, `dev` usa `django.db.backends.postgresql`.
4. `generate_env.py --development` deja un `.env` que arranca sin editar.
5. CI ejecuta la suite contra PostgreSQL real, no contra SQLite.
6. La suite completa sigue en verde sobre PostgreSQL.
