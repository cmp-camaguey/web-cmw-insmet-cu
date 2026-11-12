# 031-migraciones-versionadas — propuesta

> **Estado: propuesta, diseño y tareas cerrados.** Cambio autorizado el
> 2026-11-07: «primero haz el cambio de migraciones versionadas para que ya
> salga con el release» y «podemos empezar con una BD nueva y eliminar las
> migraciones».

## 1. Motivación

Hoy el esquema de la base **no está en el repositorio**. El `.gitignore`
(líneas 9-10) excluye todo lo que hay bajo `migrations/` salvo `__init__.py`:

```gitignore
**/migrations/*
!**/migrations/__init__.py
```

Consecuencia: un clone limpio llega sin migraciones y cada punto de ejecución
las **genera** desde los modelos antes de aplicarlas.

| Punto | Línea | Qué hace hoy |
| --- | --- | --- |
| `deploy/deploy.sh` | 845 | `manage makemigrations` y después `migrate` |
| `deploy/install.sh` | 2520 | `manage makemigrations` y después `migrate` |
| `.github/workflows/ci.yml` | 117, 241 | `python manage.py makemigrations` y después `migrate` |

El propio código documenta el problema (`deploy/deploy.sh:839-843`):

> «una migracion se genera y se aplica en el mismo deploy, sin revision. Si un
> cambio de modelo llega con un ALTER destructivo, se aplica contra produccion
> sin que nadie lo mire. Versionar las migraciones es la unica forma de que eso
> sea revisable; mientras no se haga, el deploy es el unico lugar donde el
> schema se decide.»

### 1.1 Qué se pierde exactamente

1. **Revisión.** Un `ALTER TABLE ... DROP COLUMN` generado por un cambio de
   modelo entra a producción sin pasar por un diff.
2. **Reproducibilidad.** Dos servidores que generan sus propias migraciones
   pueden tener archivos con nombres y contenido distintos para el mismo
   esquema. El estado de `django_migrations` deja de ser comparable.
3. **Rollback.** `deploy.sh` revierte el código, pero no el esquema: Django no
   genera migraciones inversas. El comentario de `deploy.sh:727-728` ya lo
   advierte.
4. **Detección temprana.** El gate natural —`makemigrations --check --dry-run`—
   hoy no se puede usar: en un clone sin migraciones falla siempre, porque
   interpreta «no hay migraciones para el modelo» como «falta la migración».

### 1.2 Antecedente

`deploy/README-deploy.md` documenta un `NodeNotFoundError` real causado por una
migración versionada (`commercial/0006`) que dependía de un padre nunca
commiteado. Versionar sin un gate que verifique la cadena reproduce ese fallo.
Por eso este cambio **no** consiste solo en sacar el `.gitignore`: el gate
`--check` es parte del mismo cambio.

## 2. Solución

1. Quitar `**/migrations/*` del `.gitignore` y versionar las migraciones.
2. Regenerarlas **desde cero** para que sean canónicas y deterministas
   (`0001_initial` por app). El usuario autorizó empezar con una base nueva, así
   que no hay estado previo que preservar.
3. Cambiar los tres puntos de ejecución de `makemigrations` a
   `makemigrations --check --dry-run`, seguido de `migrate` como hasta ahora.
4. Actualizar la documentación que afirma lo contrario (`AGENTS.md`,
   `deploy/README-deploy.md`).
5. Publicar el cambio en el primer GitHub Release (`v0.3.0`).

## 3. Alcance

- **Dentro:** `.gitignore`, migraciones de `commercial`, `core`, `meteo`,
  `publications` y `user_auth`; `deploy/deploy.sh`, `deploy/install.sh`,
  `.github/workflows/ci.yml`; `AGENTS.md` y `deploy/README-deploy.md`.
- **Fuera:** cambios de modelo, cambios de esquema, migraciones de datos,
  `DEPLOY_ENABLED`, el botón de actualización del navbar (no depende de esto).

## 4. Criterios de aceptación

1. `git ls-files '*/migrations/*.py'` lista las migraciones de las cinco apps.
2. `python manage.py makemigrations --check --dry-run` sale `0` sobre el árbol
   versionado.
3. `python manage.py migrate` sobre una base vacía crea el esquema completo.
4. Si se modifica un modelo sin migración, `--check` sale distinto de `0` en
   CI y en el deploy.
5. Ningún documento del repo afirma que las migraciones no se versionan.

## 5. Plan de rollback

El cambio es de proceso, no de datos:

1. Revertir el commit.
2. Restaurar el `.gitignore`.
3. Volver los tres puntos de ejecución a `makemigrations`.

Las migraciones versionadas pueden quedarse en el árbol sin efecto mientras el
`.gitignore` las ignore: no rompen nada por sí solas. El único punto de no
retorno sería un deploy que aplique migraciones destructivas, y este cambio no
introduce ninguna.
