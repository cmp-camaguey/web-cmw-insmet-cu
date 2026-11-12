# 033 — Entorno del servidor

Objetivo: nueva sección de configuración en el dashboard que muestre la configuración interna del `.env` y permita editar solo los parámetros que no comprometen el funcionamiento del sitio.
Rama: `feat/033-entorno-servidor` (desde `dev`). Entrega: commit local, sin push.
Runner: `python manage.py test apps.core`.

## Specs

- **S1** — Nueva entrada `Entorno del servidor` en el desplegable Configuración del dashboard, ruta `/config/entorno/`, name `core:environment_config`. Visible y accesible **solo para superusuarios**; un usuario autenticado no superusuario recibe 403.
- **S2** — La página muestra la configuración **efectiva**, leída de `django.conf.settings` (y `os.environ` solo para claves que no son settings, como `ENCRYPTION_KEY` y las de LDAP), agrupada por área: Aplicación, Dominio y orígenes, Base de datos, Caché, Correo, Avisos operativos, LDAP, Actualizaciones, Observaciones (FTP).
- **S3** — Los secretos `SECRET_KEY`, `ENCRYPTION_KEY`, `DB_PASS`, `EMAIL_HOST_PASSWORD`, `LDAP_BIND_PASSWORD` y `FTP_OBS_PASS` **nunca** se renderizan; solo se informa `Configurada` / `Sin configurar`.
- **S4** — `REDIS_URL` se muestra sin credenciales (`redis://:***@host:6379/1`).
- **S5** — Editables (se escriben en `.env`): `EMERGENCY_BANNER_ENABLED`, `NOTIFICATIONS_ENABLED`, `NOTIFICATIONS_MAX_VISIBLE`, `NOTIFICATIONS_RETENTION_READ`, `NOTIFICATIONS_RETENTION_UNREAD`, `NOTIFICATIONS_WARNING_HORIZON_HOURS`, `INVOICE_PENDING_REMINDER_DAYS`, `SITE_URL`, `FTP_OBS_HOST`, `FTP_OBS_USER`, `FTP_OBS_PORT`.
- **S6** — Solo lectura: `DEBUG`, `PRODUCTION`, `SECRET_KEY`, `ENCRYPTION_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `EXTERNAL_HOSTNAME`, `DB_*`, `USE_REDIS_CACHE`, `REDIS_URL`, `EMAIL_*`, `LDAP_*`, `UPDATE_*`.
- **S7** — La escritura preserva comentarios, orden, líneas en blanco y claves ajenas al formulario; es atómica (temporal en el mismo directorio + `os.replace`), conserva el modo del archivo y se serializa con `flock` sobre `.env.lock`.
- **S8** — Si el proceso web no puede escribir el `.env`, el formulario queda deshabilitado con un aviso explícito y el POST no toca el archivo.
- **S9** — Los cambios no se aplican hasta reiniciar el servicio: la página lo advierte y marca con `Pendiente de reinicio` los parámetros cuyo valor en archivo difiere del valor en ejecución. El panel **no** reinicia nada.
- **S10** — Cada guardado registra una entrada en `ActivityLog` con el usuario, la IP y las claves modificadas (nunca los valores).

## Tasks

- [ ] T1 — `ENV_FILE_PATH` en `config/settings/base.py` y `load_dotenv(ENV_FILE_PATH)`. (S7)
- [ ] T2 — `apps/core/env_file.py`: lectura, escritura quirúrgica, atómica y bloqueada. (S5, S7)
- [ ] T3 — `apps/core/env_config.py`: catálogo, valores efectivos, enmascarado y detección de drift. (S2, S3, S4, S9)
- [ ] T4 — `apps/core/forms/env_config.py` + `apps/core/views/env_config.py` + ruta + export. (S1, S5, S8, S10)
- [ ] T5 — Template `apps/core/templates/pages/core/site/environment.html`. (S2, S3, S9)
- [ ] T6 — Entrada de menú en `templates/includes/dashboard/menu/_configuracion.html`. (S1)
- [ ] T7 — Tests `apps/core/tests/test_env_config.py`. (todas)
- [ ] T8 — Docs OpenSpec `openspec/changes/033-entorno-servidor/`. (todas)
- [ ] T9 — Correo, LDAP y Actualizaciones editables + contraseña de FTP. (S5, S6, S11)
- [ ] T10 — Validación de combinaciones que impiden arrancar. (S12)
- [ ] T11 — Textos de la página sin referencia al archivo de entorno. (S13)

## Log

- **L1 (original)** — "agregar nueva seccion de configuracion en el dashboard para la el apartado de configuracion internas del .env (colocale un nombre adecuado). Antes de tocar el codigo muestrame el plan de implementacion. Recuerda usar el mcp codegraph. Solo realizar commit no suvir nada por ahora."
- **L2 (alcance)** — "2. pero que sean solo las que no comprometen el funcionamiento del sitio, las que no sean editables mostrarlas solo como lectura" → la página es editable con lista blanca; el resto, solo lectura.
- **L3 (decisión de diseño)** — Nombre elegido: **Entorno del servidor**. Se muestran valores efectivos de `settings`, no el `.env` crudo: en producción `ENCRYPTION_KEY` llega por systemd y `LOG_LEVEL`/`LOG_FILE` no los consume ningún settings.
- **L4 (decisión de diseño)** — El panel no reinicia el servicio. Reiniciar desde la web es exactamente lo que puede tumbar el sitio; se avisa que los cambios aplican al reiniciar.
- **L5 (hallazgo, fuera de alcance)** — `scripts/generate_env.py` escribe `LOG_LEVEL` y `LOG_FILE`, pero `config/settings/base.py` no los lee (`LOGGING` está hardcodeado). No se corrige en este cambio.
- **L6 (alcance, 2026-11-10)** — "correo, LDAP y Actualizaciones del panel pasalos a configurables. Observaciones (FTP) agregar campo de contraceña" → las tres secciones pasan a editables (incluidas sus credenciales como campos de contraseña) y FTP gana `FTP_OBS_PASS`. Se mantienen en solo lectura las claves que impiden arrancar: `EMAIL_BACKEND` (assert en `production.py`), `DB_*`, hosts/orígenes, `SECRET_KEY`, `ENCRYPTION_KEY` y las rutas de archivo del despliegue. Se agrega validación de las combinaciones que `config/settings` rechaza al iniciar.
- **L7 (alcance, 2026-11-10)** — "no hagas referencia al archivo .ven en los textos" → los avisos de la página dejan de nombrar el archivo de entorno; solo dicen que los cambios aplican al reiniciar.
- **L8 (alcance, 2026-11-10)** — "LDAP 'Solo lectura / Activo en ejecución: No' editable" → se agrega `LDAP_ENABLED` como interruptor explícito en `config/settings/base.py` (`resolve_ldap_enabled`), con la regla vieja —activo si hay `LDAP_SERVER_URI`— como respaldo cuando la variable no existe. La nota de solo lectura de LDAP desaparece porque ya no hay nada que solo se pueda mirar.
- **L9 (alcance, 2026-11-10)** — "Archivo de correos: ... no mostrar" → se quitan del panel las rutas absolutas del servidor (archivo de correos y archivos de estado del despliegue); publican el layout del sistema de archivos sin aportar a la operación.
- **L10 (alcance, 2026-11-10)** — "coloca placeholder en los campos de los formularios" → `EnvField.placeholder`; los de contraseña usan `Ej.: contraseña nueva`, nunca el valor guardado. Después el pedido fue que **todos** empiecen por `Ej.:` (L10b).
- **L11 (alcance, 2026-11-10)** — "ordena los campos de los formularios estan desordenados y tienen mala las dostribusion" → `EnvField.width` (`'full'`/`'half'`) en el catálogo, traducido a `col-12`/`col-md-6` en la vista; catálogo reordenado por afinidad (interruptores juntos, par de retención junto, palancas de LDAP antes de la conexión) y la ayuda pasa a ir debajo del campo, antes de `En ejecución`.
- **L12 (alcance, 2026-11-10)** — "no nuestres nombres de variables 'SECRET_KEY, ENCRYPTION_KEY, DB_PASS' solo nombres literales" → fuera identificadores del HTML: etiquetas de solo lectura en castellano (`Modo de depuración`, `Hosts permitidos`, `Orígenes de confianza para CSRF`, `Vida máxima de la conexión (s)`), motores por nombre (`SMTP`, `Redis`, `PostgreSQL`) y la tarjeta de secretos lista solo `Clave secreta de Django`, `Clave de descifrado`, `Contraseña de la base de datos`.
