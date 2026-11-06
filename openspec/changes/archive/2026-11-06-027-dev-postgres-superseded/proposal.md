# 027-dev-postgres

> **Estado: SUSTITUIDO por `028-dev-postgres-obligatorio`.** 027 dejaba
> PostgreSQL detrás de `DEV_USE_DB_ENGINE=True` y SQLite como default. 028 quitó
> el flag y retiró SQLite de los tres perfiles (PR #133, `d5febab`), así que lo
> que 027 proponía ya no existe en el código y sus tareas pendientes quedaron
> cubiertas o sin objeto. Se archiva como historia, no como trabajo por hacer.
>
> El texto de abajo se conserva tal como estaba, incluida la línea siguiente, que
> describe el estado anterior a 028.

> **Estado: implementado en la rama `feat/dev-postgres`.** No cambia el
> comportamiento por defecto de desarrollo: SQLite sigue ganando sin opt-in.

## Por que

Produccion corre PostgreSQL y desarrollo corre SQLite. Hasta ahora **no habia
forma de ejecutar la app contra PostgreSQL en local**, y eso no es un detalle de
comodidad: lo que se valida en SQLite no prueba lo que corre en produccion.

Diferencias que rompen en silencio:

| Comportamiento | SQLite | PostgreSQL |
|---|---|---|
| `LIKE` / `icontains` con mayusculas | comparacion ASCII, a veces coincide | distingue mayusculas segun collation |
| Orden de `NULL` | `NULL` primero en `ASC` | `NULL` al final en `ASC` (por defecto) |
| Constraints | relajadas, validadas al final | estrictas, inmediatas |
| Transacciones y concurrencia | bloqueo de escritor unico | MVCC real |
| Secuencias | `AUTOINCREMENT` | `SERIAL`/`IDENTITY`, no se reutilizan |

Una migracion o una query que solo se probo en SQLite puede fallar el dia del
despliegue, cuando el costo es un outage y no un test rojo.

## Que

Un opt-in explicito: `DEV_USE_DB_ENGINE=True` en el `.env` de desarrollo hace que
el perfil `dev` use el motor de `DB_ENGINE` (con `DB_NAME`, `DB_USER`, `DB_PASS`,
`DB_HOST`). Sin el flag, todo sigue igual: SQLite.

Ademas, `psycopg[binary]` pasa de `requirements/prod.txt` a
`requirements/base.txt`: es el driver del motor, runtime en los tres entornos, no
una herramienta de desarrollo.

## Que NO es

- **No es "dev honra `DB_ENGINE` siempre".** Eso ya se decidio en contra y hay un
  test que lo fija (`test_sqlite_wins_over_db_engine_env`): un `.env` copiado de
  produccion haria que una laptop escriba en la base real. El opt-in conserva esa
  garantia.
- **No cambia `production` ni `testing`.** Los dos ya pasan
  `prefer_sqlite=False`; su comportamiento es identico.
- **No migra el `.env` de nadie.** El flag es por entorno y por desarrollador.

## Criterios de aceptacion

1. Sin `DEV_USE_DB_ENGINE`, `dev` usa SQLite aunque `DB_ENGINE=postgresql`.
2. Con `DEV_USE_DB_ENGINE=True` y credenciales completas, `dev` usa PostgreSQL.
3. Con `DEV_USE_DB_ENGINE=True` y credenciales incompletas, el arranque falla con
   `ImproperlyConfigured`, no adivina.
4. `pip install -r requirements/dev.txt` deja el driver disponible.
5. La suite completa sigue en verde con SQLite.
