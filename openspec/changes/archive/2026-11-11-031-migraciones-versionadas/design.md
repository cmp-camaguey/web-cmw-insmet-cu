# 031-migraciones-versionadas — diseño

## 1. Decisión: regenerar desde cero

Hay dos caminos para obtener la copia canónica:

| Opción | Ventaja | Problema |
| --- | --- | --- |
| Commitear las 10 migraciones locales existentes | No hay que regenerar | Nombres autogenerados (`0002_service_service_category_and_more`), dependen del orden en que se escribieron los modelos y arrastran historia que ya no importa |
| Borrarlas y generar `0001_initial` por app | Determinista, mínimo, revisable | Requiere base nueva para probar |

Se elige **regenerar**. El usuario autorizó explícitamente empezar con una base
nueva y eliminar las migraciones. El esquema no tiene migraciones de datos
(`RunPython`/`RunSQL`): se verificó que las 10 existentes son autogeneradas.

## 2. Orden de generación

`makemigrations` resuelve las dependencias entre apps por `AUTH_USER_MODEL`.
Las tres apps que la usan (`commercial`, `core`, `meteo`, `user_auth`) generan
`swappable_dependency(settings.AUTH_USER_MODEL)`, que apunta a
`user_auth.0001_initial`. Django ordena la aplicación sin intervención.

Apps sin modelos (`api`, `dashboard`, `home`) conservan solo `__init__.py`.

## 3. Los tres puntos de ejecución

### 3.1 `deploy/deploy.sh`

Hoy (paso 3):

```bash
log "Generando migraciones desde los modelos"
manage makemigrations
```

Después:

```bash
log "Verificando que las migraciones versionadas cubren los modelos"
manage makemigrations --check --dry-run
```

Se mantiene en el mismo lugar: antes del `check --deploy` y antes de `migrate`,
para que un árbol sin migración aborte **sin tocar la base**. El paso 5
(`manage migrate --noinput` y `manage migrate --check`) no cambia.

### 3.2 `deploy/install.sh`

Mismo cambio en el paso C.7 (línea 2520). El instalador corre sobre un clone
limpio, que ahora trae las migraciones versionadas, así que `--check` sí puede
usarse. Se conserva `migrate` y `migrate --check`.

### 3.3 `.github/workflows/ci.yml`

Los dos jobs que migran (tests y security) pasan a:

```bash
python manage.py makemigrations --check --dry-run
python manage.py migrate
```

`--check` antes de `migrate`: si falta una migración, el job falla con un
mensaje que nombra la app, en vez de generar el archivo y dar verde sobre un
árbol que no lo tiene.

## 4. Primer deploy

El contenedor del proveedor nace con base vacía:

1. `install.sh` clona el repo (con migraciones versionadas).
2. `makemigrations --check --dry-run` → no detecta cambios.
3. `migrate` aplica `0001_initial` de cada app en orden de dependencias.
4. `migrate --check` confirma que no queda nada pendiente.

No hay reconciliación con `--fake` porque no hay base previa. Ese es
precisamente el motivo de hacer el cambio **antes** del primer deploy: si se
hiciera después, el servidor del proveedor tendría migraciones autogeneradas
registradas en `django_migrations` y habría que reconciliarlas sobre datos
reales.

## 5. Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Una migración generada no reproduce el esquema actual | `migrate` desde cero en CI y en el host de prueba; `migrate --check` al final |
| Dependencia circular entre apps | `makemigrations` la detecta al generar; el orden lo resuelve Django |
| El gate `--check` no corre en el deploy por un checkout incompleto | El paso queda antes de `migrate`; si falla, no se tocó la base |
| Rollback de código con esquema ya migrado | Sin cambios en este release no hay migraciones destructivas; el riesgo de rollback no aumenta |
| Documentación desactualizada | Se actualizan `AGENTS.md` y `deploy/README-deploy.md` en el mismo cambio |

## 6. Trazabilidad del botón de actualización

El botón del navbar (`apps/dashboard/views/dashboard/update.py`) **no** lee
migraciones: detecta un commit nuevo con `git ls-remote`, dispara un archivo que
consume una unit `.path` y compara contra `/etc/webcmp/deployed`. Versionar las
migraciones no lo desbloquea ni lo rompe. Este cambio es de revisabilidad del
esquema; el botón ya funciona con `deploy.sh`.
