# 033-entorno-servidor — tareas

## Fase 1 — Archivo de entorno

- [x] `ENV_FILE_PATH` en `config/settings/base.py` y `load_dotenv(ENV_FILE_PATH)`.
- [x] `apps/core/env_file.py`: lectura, escritura quirúrgica, atómica y bloqueada.
- [x] Pruebas de archivo (comentarios, orden, claves ajenas, comillas, CRLF, modo, no escribible).

## Fase 2 — Catálogo y valores efectivos

- [x] `apps/core/env_config.py`: `EnvField`, `EDITABLE_FIELDS`, grupos de solo lectura.
- [x] Enmascarado de secretos y de credenciales en `REDIS_URL`.
- [x] Detección de diferencias archivo/proceso (`pending`).
- [x] Pruebas de catálogo, enmascarado y comparación de valores.

## Fase 3 — Panel

- [x] `EnvironmentConfigForm` construido desde `EDITABLE_FIELDS`.
- [x] `EnvironmentConfigView` (superusuario), URL `core:environment_config`.
- [x] Plantilla `pages/core/site/environment.html`.
- [x] Entrada **Entorno del servidor** en el menú Configuración.
- [x] Registro en `ActivityLog` sin valores.
- [x] Pruebas de vista, permisos, fuga de secretos, POST y menú.

## Fase 3b — Correo, LDAP, Actualizaciones y contraseñas

- [x] Correo, LDAP y Actualizaciones del panel pasan a secciones editables.
- [x] `FTP_OBS_PASS` y las credenciales de correo y LDAP como campos de contraseña.
- [x] Campo vacío no borra la contraseña guardada; el valor nunca se renderiza.
- [x] Validación de TLS+SSL, puerto 465 sin TLS implícito, host sin puerto y LDAP sin base de búsqueda.
- [x] Textos de la página sin nombrar el archivo de entorno.

## Fase 3c — Presentación del formulario

- [x] Placeholders con prefijo `Ej.:` en todos los campos de texto y numéricos.
- [x] `LDAP_ENABLED` editable, con la regla anterior como respaldo.
- [x] Sin rutas absolutas del servidor en la página.
- [x] Catálogo reordenado por afinidad y ancho por campo (`col-12` / `col-md-6`).
- [x] Ayuda del campo antes del valor en ejecución.
- [x] Etiquetas y motores en castellano: sin identificadores de variables ni rutas de módulo.

## Fase 3d — Aplicar cambios

- [x] Botón **Aplicar cambios** junto a **Guardar**: guarda y pide el reinicio.
- [x] `apps/core/env_apply.py`: petición con `O_EXCL`, estado del último reinicio y ventana de `running` viejo.
- [x] `EnvironmentConfigApplyView` reutiliza guardado, validación y auditoría de la vista base.
- [x] La página muestra el estado del último reinicio y deshabilita el botón sin directorio escribible.
- [x] `deploy/apply-env.sh` con `systemctl` fijo, saneo del autor y estado atómico.
- [x] `deploy/systemd/webcmp-apply.path` y `.service`, con `Environment="UNITS=..."` renderizado.
- [x] `setup_apply()` en `deploy/install.sh`.
- [x] `deploy/tests/test_apply_env_sh.sh` (23 casos) y `README-deploy.md`.
- [x] **Aplicar cambios** exige el modo mantenimiento: botón deshabilitado con
      aviso y POST rechazado sin escribir el `.env` ni dejar petición.

## Fase 4 — Verificación

- [x] `python manage.py check`.
- [x] `python manage.py test apps.core`.
- [x] `python manage.py test` (suite completa, 1689 tests).
- [x] `ruff check apps config` y `ruff format --check`.
- [x] `djlint --check` y `djlint --lint` sobre las plantillas tocadas.
- [x] Commits en `feat/033-entorno-servidor` (sin push).
