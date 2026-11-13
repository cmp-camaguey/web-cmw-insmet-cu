# Grupos por rol y jerarquía

Objetivo: crear en cada instalación y en cada deploy los grupos de Django con los roles por jerarquía de todos los modelos, para que un usuario no-superusuario pueda operar el dashboard sin permisos cargados a mano.
Rama: `feat/grupos-por-rol` (desde `dev`). Entrega: commits locales en la rama, sin push.
Runner: `python manage.py test apps.user_auth` y `make test-deploy`.

## Specs

- **S1** — Se crean 18 grupos con nombre `<Módulo> - <Nivel>`, sin tildes: módulos `Meteorologia`, `Observaciones`, `Comercial`, `Publicaciones`, `Configuracion`, `Usuarios`; niveles `Ver`, `Editar`, `Gestionar`.
- **S2** — La jerarquía es acumulativa dentro de cada módulo y no entre módulos: `Ver` = permisos `view_*`; `Editar` = `Ver` + `add_*` + `change_*`; `Gestionar` = los cuatro. Un módulo con `Gestionar` no otorga permisos de otro módulo.
- **S3** — Los permisos se derivan de `Model._meta` (permisos custom declarados + `default_permissions`) en cada corrida, no de una lista de codenames escrita a mano: un modelo nuevo queda cubierto sin editar el comando. Sólo cuentan los modelos que existen en el esquema migrado (estado de las migraciones): los modelos que sólo existen durante las pruebas se omiten con aviso. Un permiso que no sigue `view_/add_/change_/delete_` se informa por pantalla para que no quede fuera de la jerarquía en silencio.
- **S4** — El comando es idempotente y nunca quita permisos: usa `add`, no `set`. Correrlo dos veces no duplica grupos y conserva los permisos agregados a mano en la pantalla de Grupos.
- **S5** — Cada grupo queda con su `GroupProfile` (la señal `post_save` lo crea al alta; el comando hace `get_or_create` para reparar grupos anteriores a la señal). Sin `GroupProfile` el grupo no se puede editar en la pantalla de Grupos.
- **S6** — El comando no toca los grupos existentes `Clientes` ni `Estacion <número> - <rol>`.
- **S7** — El comando falla cerrado (`CommandError`, salida distinta de 0) si falta un permiso que el modelo declara: sería un modelo sin migración aplicada, y un grupo a medias es peor que un deploy detenido.
- **S8** — `deploy/install.sh` corre `create_role_groups` en el paso de migraciones, después de `migrate --check` y antes de `collectstatic`, con `|| die` como el resto de los `manage` de esa sección.
- **S9** — `deploy/deploy.sh` corre `create_role_groups` después de `migrate --check` y antes de `collectstatic`, para que un modelo nuevo traiga sus grupos en el mismo deploy.
- **S10** — Documentación: `docs/1-instalacion.md` y `deploy/README-deploy.md` nombran el comando y su idempotencia.
- **S11** — Pruebas: los 18 grupos con sus juegos de permisos, jerarquía acumulativa, cobertura total de los modelos de cada módulo, filtro por esquema migrado, idempotencia, conservación de permisos manuales, no injerencia con `Clientes`/estación y `--dry-run` sin escrituras.

## Tasks

- [x] T1 — `apps/user_auth/roles.py`: módulos, niveles, derivación de permisos desde `_meta` y `group_name`. (S1, S2, S3) — inline
- [x] T2 — `apps/user_auth/management/commands/create_role_groups.py`: idempotente, `--dry-run`, falla cerrado. (S3, S4, S5, S6, S7) — inline
- [x] T3 — Enganche en `deploy/install.sh`. (S8) — inline
- [x] T4 — Enganche en `deploy/deploy.sh`. (S9) — inline
- [x] T5 — Pruebas. (S11) — inline
- [x] T6 — Documentación. (S10) — inline

## Log

- **L1 (original)** — "implementar en install/deply la creacion de grupos con los roles por gerarquia de tolo los modelos, antes de implementar muestrame un resumen de los grupos a crear para aprobarlos"
- **L2 (decisión, 2026-11-13)** — Jerarquía por módulo, no lineal entre módulos: un rol comercial con `Gestionar` no puede borrar pronósticos. 18 grupos = 6 módulos × 3 niveles (`Ver` ⊂ `Editar` ⊂ `Gestionar`).
- **L3 (decisión, 2026-11-13)** — Nombres sin tildes (`Meteorologia`, `Configuracion`), igual que `Estacion`, para que no rompan en shell ni en `manage.py shell -c`.
- **L4 (decisión, 2026-11-13)** — `Monitoreo` queda fuera: ninguna vista consulta esos permisos y el panel es superusuario. `Clientes` y los grupos de estación se respetan sin cambios.
- **L5 (aprobación)** — "aprobado"
- **L6 (evidencia, 2026-11-13)** — `python manage.py test apps.user_auth.tests.test_role_groups`: 10 pruebas, RED previo por `ModuleNotFoundError` de `apps.user_auth.roles`, luego verde. `python manage.py test apps.user_auth`: 77 pruebas en verde. `python manage.py check` y `makemigrations --check --dry-run` en verde.
- **L7 (evidencia, 2026-11-13)** — `make test-deploy`: 164 ok. Se agregaron cuatro aserciones: los dos scripts nombran el comando y lo ubican despues de `migrate --check`.
- **L8 (evidencia, 2026-11-13)** — `create_role_groups --dry-run` contra la base de desarrollo: 18 grupos con los conteos aprobados (12/34/45, 1/3/4, 8/24/32, 2/6/8, 9/27/36, 6/18/24). Primer pase real: 18 nuevos; segundo pase: 0 nuevos (idempotencia observada).
- **L9 (defecto encontrado por la suite completa, 2026-11-13)** — La suite falló con `CommandError: Faltan permisos declarados por los modelos: core.add_simplefilemodel...`. Causa: `apps/core/tests/test_file_handling.py` define `SimpleFileModel` y `SoftDeleteFileModel`, que se registran en el app registry sólo durante las pruebas y no existen en las migraciones ni tienen permisos en la base. Corrección: el comando limita la derivación al **estado de las migraciones** (`migrated_model_keys()`), no al registry; un modelo fuera del esquema se omite con aviso por `stderr`, y un modelo del esquema con un permiso faltante sigue fallando cerrado. El filtro vive en `roles.py` (`schema_permissions`, `outside_schema_models`) y tiene prueba propia con un conjunto de claves sintético.
- **L10 (evidencia, 2026-11-13)** — `apps.user_auth.tests.test_role_groups`: 11 pruebas en verde.
- **Riesgo** — `Risk: item 5` (cambia el camino de instalación y el de despliegue).
