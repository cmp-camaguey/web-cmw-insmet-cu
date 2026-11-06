# 028-dev-postgres-obligatorio — tareas

## 1. Settings

- [x] 1.1 `get_database_config()` sin parámetros; `DB_ENGINE` obligatorio
- [x] 1.2 Rechazo explícito de `sqlite`/`sqlite3` con mensaje accionable
- [x] 1.3 `dev.py`, `testing.py` y `production.py` delegan sin argumentos
- [x] 1.4 Se elimina `DEV_USE_DB_ENGINE` (opt-in de 027)

## 2. Tests (RED observado antes de implementar)

- [x] 2.1 `SqliteIsNotSupportedTests`: rechazo en los tres perfiles
- [x] 2.2 `DevProfileTests`/`TestingProfileTests`: PostgreSQL desde el entorno y
      fail-closed sin `DB_ENGINE`
- [x] 2.3 `DatabaseConfigTests`: fail-closed, rechazo de SQLite, credenciales
- [x] 2.4 `test_generate_env_script`: `.env` de desarrollo con PostgreSQL

## 3. Generación de entorno

- [x] 3.1 `scripts/generate_env.py --development` emite `DB_ENGINE=postgresql`
      con `DB_NAME/DB_USER/DB_HOST/DB_PORT/DB_SSL_MODE`
- [x] 3.2 Comentario de producción corregido: `psycopg[binary]` está en
      `requirements/base.txt`

## 4. CI

- [x] 4.1 Service container `postgres:18` con healthcheck en `test`
- [x] 4.2 Service container `postgres:18` con healthcheck en `security`
- [x] 4.3 `DB_*` a nivel de job en ambos

## 5. Documentación

- [x] 5.1 README: "PostgreSQL en desarrollo (obligatorio)" con rol, `CREATEDB` y
      credenciales
- [x] 5.2 README: tabla de `requirements/` sin el driver en `prod.txt`
- [x] 5.3 AGENTS.md: stack y comandos con PostgreSQL obligatorio
- [x] 5.4 Comentarios de `deploy/install.sh`, `deploy/deploy.sh` y
      `deploy/README-deploy.md` sin la afirmación "migra contra sqlite3"

## 6. Verificación

- [x] 6.1 Tests focalizados en verde sobre PostgreSQL
- [x] 6.2 Suite completa sobre PostgreSQL (1472 tests OK)
- [ ] 6.3 PR a `dev` (draft hasta que entre `v0.1.2`)
- [x] 6.4 `.env` local apuntado a PostgreSQL, `add_stations_data` ejecutado (6
      estaciones) y superusuario creado por el desarrollador
- [ ] 6.5 `gentle-ai sdd-archive` cuando esté en `main`
