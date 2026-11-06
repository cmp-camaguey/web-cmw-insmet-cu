# 027-dev-postgres — tareas

> **Estado: SUSTITUIDO por `028-dev-postgres-obligatorio`.** Las casillas sin
> marcar no son trabajo pendiente: se resolvieron dentro de 028 (rol y base
> `webcmp_dev`, `migrate` y suite completa sobre PostgreSQL) o quedaron sin
> objeto al eliminarse `DEV_USE_DB_ENGINE`. Se dejan como estaban para no
> reescribir el registro.

## 1. Perfil `dev`

- [x] 1.1 `DEV_USE_DB_ENGINE` en `config/settings/dev.py`; sin el flag, SQLite
- [x] 1.2 Tests: opt-in activa PostgreSQL, credenciales incompletas fallan con
      `ImproperlyConfigured`, y `DB_ENGINE` solo sigue dando SQLite

## 2. Dependencias

- [x] 2.1 `psycopg[binary]==3.3.8` en `requirements/base.txt`, fuera de
      `requirements/prod.txt`
- [ ] 2.2 `pip install -r requirements/dev.txt` en el venv local y confirmar que
      `import psycopg` funciona

## 3. Prueba real contra PostgreSQL

- [ ] 3.1 Crear rol `webcmp_dev` con `CREATEDB` y base `webcmp_dev`
- [ ] 3.2 `DEV_USE_DB_ENGINE=True` en `.env` y `python manage.py migrate`
- [ ] 3.3 Suite completa con PostgreSQL: `python manage.py test`
- [ ] 3.4 Revisar diferencias de comportamiento que SQLite tapaba (orden de
      `NULL`, `icontains`, constraints) y abrir cambios si aparecen

## 4. Documentacion

- [x] 4.1 README: seccion "PostgreSQL en desarrollo (opcional)" con los pasos
- [x] 4.2 README: corregir donde decia que `psycopg` venia en `prod.txt`

## 5. Cierre

- [ ] 5.1 PR a `dev` y `gentle-ai sdd-archive` cuando este en `main`
