# dev-postgres — requisitos delta

## ADDED Requirements

### Requirement: DEV-DB-OPT-IN

El perfil `dev` SHALL usar SQLite salvo que `DEV_USE_DB_ENGINE` sea exactamente
`True`, y en ese caso SHALL usar el motor configurado en `DB_ENGINE`.

#### Scenario: Sin opt-in

- GIVEN `DEBUG=True` y `DB_ENGINE=postgresql` en el `.env`
- WHEN arranca el perfil `dev`
- THEN `DATABASES['default']['ENGINE']` es `django.db.backends.sqlite3`
- AND el nombre de la base es `db.sqlite3`

#### Scenario: Opt-in explicito

- GIVEN `DEV_USE_DB_ENGINE=True`, `DB_ENGINE=postgresql` y `DB_NAME`, `DB_USER`,
  `DB_PASS`, `DB_HOST` definidos
- WHEN arranca el perfil `dev`
- THEN `DATABASES['default']['ENGINE']` es `django.db.backends.postgresql`
- AND el puerto por defecto es `5432`

#### Scenario: Opt-in sin credenciales

- GIVEN `DEV_USE_DB_ENGINE=True` y `DB_ENGINE=postgresql`
- AND falta al menos una de `DB_NAME`, `DB_USER`, `DB_HOST`, `DB_PASS`
- WHEN arranca el perfil `dev`
- THEN el arranque falla con `ImproperlyConfigured`
- AND el mensaje nombra las cuatro variables

### Requirement: DB-DRIVER-EN-TODOS-LOS-ENTORNOS

`psycopg[binary]` SHALL estar en `requirements/base.txt`, de modo que
`dev.txt`, `test.txt` y `prod.txt` lo instalen.

#### Scenario: Entorno de desarrollo con PostgreSQL

- GIVEN un venv con `requirements/dev.txt` instalado
- WHEN `DB_ENGINE=postgresql` y `DEV_USE_DB_ENGINE=True`
- THEN Django carga el backend de PostgreSQL sin un `ImportError`
