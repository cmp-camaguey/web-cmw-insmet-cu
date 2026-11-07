# 031-migraciones-versionadas — tareas

- [x] **T1. `.gitignore`**: quitar `**/migrations/*` y la excepcion
      `!**/migrations/__init__.py`. Los `__init__.py` siguen trackeados.
      *Evidencia:* `git diff .gitignore` borra solo esas dos lineas.
- [x] **T2. Regenerar migraciones**: los 10 archivos autogenerados se respaldaron
      en `/tmp/opencode/migraciones-viejas/` y se borraron; `makemigrations`
      genero un `0001_initial` por app.
      *Evidencia:* `commercial` 204 lineas, `core` 164, `meteo` 180,
      `publications` 59, `user_auth` 65. Las dependencias por `AUTH_USER_MODEL`
      resuelven (`user_auth.0001_initial` y `auth.0012`).
      *Hallazgo:* las migraciones viejas estaban **desactualizadas** — faltaba
      `Notification` en `core` y habia cinco campos corridos en `commercial`.
      Regenerar no era opcional.
- [x] **T3. `deploy/deploy.sh`**: paso 3 ahora es
      `manage makemigrations --check --dry-run`, con el comentario reescrito.
      *Evidencia:* `deploy/deploy.sh:841`.
- [x] **T4. `deploy/install.sh`**: mismo gate en el paso C.7 y comentarios
      actualizados (encabezado, contrato del lock, mensaje de `die`).
      *Evidencia:* `deploy/install.sh:2520`.
- [x] **T5. `.github/workflows/ci.yml`**: los dos jobs que migran (tests y
      security) verifican antes de aplicar; el step se renombro a
      `Verify and apply migrations`.
      *Evidencia:* lineas 117 y 241.
- [x] **T6. Documentacion**: `AGENTS.md` (convencion y checklist),
      `deploy/README-deploy.md` (tabla del procedimiento, seccion reescrita y
      nota historica de `commercial/0006`), `README.md`, y
      `.opencode/skills/django-expert/SKILL.md`.
      *Evidencia:* `git grep 'no versiona'` no devuelve nada fuera de los
      documentos historicos de `odd/tasks/` y `openspec/changes/archive/`.
- [x] **T7. Verificacion**:
      - `makemigrations --check --dry-run` → 0, «No changes detected»
      - `migrate` sobre base vacia → aplica las 5 apps + las de Django
      - `migrate --check` → 0
      - `python manage.py test` → **1562 tests, OK**
      - `ruff check .` → limpio; `ruff format --check .` → 381 archivos
      - `djlint . --lint` → 0 errores; `--reformat --check` → solo falla en el
        `landing.html` en curso del usuario y en un artefacto local de
        `staticfiles/`
      - `bash -n` de los 4 scripts, YAML parseable, `git diff --check` limpio
      - `deploy/tests/run.sh` → 65 ok, 0 fallas
- [ ] **T8. Release**: tag anotado nuevo sobre el commit de promocion y
      `gh release create` con notas. No mover `v0.2.0`. **Pendiente de la
      decision del usuario sobre el numero de version.**
