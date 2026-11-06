# 027-dev-postgres — diseno

## La decision

`config/settings/dev.py` calcula:

```python
DEV_USE_DB_ENGINE = os.getenv('DEV_USE_DB_ENGINE', 'False') == 'True'
DATABASES = get_database_config(is_production=IS_PRODUCTION, prefer_sqlite=not DEV_USE_DB_ENGINE)
```

`base.get_database_config` ya tenia la logica que hacía falta:

```python
if prefer_sqlite or db_engine in {'', 'sqlite', 'sqlite3'}:
    return sqlite
```

O sea: con `prefer_sqlite=False` y sin `DB_ENGINE`, sigue devolviendo SQLite. El
cambio real es **una sola linea de comportamiento**, y solo se activa con el
flag.

## Por que un flag y no honrar `DB_ENGINE` siempre

El perfil `dev` decide con `DEBUG=True`. Un `.env` que viaje de produccion a una
laptop trae `DB_ENGINE=postgresql` y `DB_HOST` del servidor. Honrarlo siempre
significa que un `manage.py migrate` distraido escribe en la base de produccion.
Ese riesgo esta documentado y cubierto por
`test_sqlite_wins_over_db_engine_env`, que se conserva intacto.

El flag no es ceremonia: es la unica diferencia entre "pruebo Postgres" y "me
conecte sin querer a produccion".

## Por que `psycopg` en `base.txt`

`requirements/prod.txt` es `base.txt` + gunicorn + whitenoise + redis. El driver
de la base de datos no es una herramienta de desarrollo ni un extra opcional: sin
el, cualquier entorno con `DB_ENGINE=postgresql` muere al cargar modelos con
`Error loading psycopg2 or psycopg module`. Tenerlo en `base.txt` evita ademas
dos pines del mismo paquete que se desincronizan.

## Verificacion

- `apps/core/tests/test_settings_profiles.py`: dos tests nuevos (opt-in y fallo
  por credenciales incompletas) y el existente que fija SQLite por defecto.
- Prueba real contra PostgreSQL local: crear rol y base, `migrate`, y correr la
  suite con `DEV_USE_DB_ENGINE=True`. **Pendiente de ejecutar**: requiere
  credenciales de `sudo` para crear el rol.
