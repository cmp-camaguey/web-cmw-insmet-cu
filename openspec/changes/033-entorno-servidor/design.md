# 033-entorno-servidor — diseño

## 1. Valores efectivos, no el archivo

La página lee `django.conf.settings`. El archivo `.env` se carga una sola vez al
arrancar (`load_dotenv(ENV_FILE_PATH)`) y de ahí se derivan `DATABASES`,
`CACHES`, `AUTHENTICATION_BACKENDS` y los valores normalizados.

Mostrar el archivo crudo mentiría por tres motivos concretos:

- En producción `ENCRYPTION_KEY` no está en el `.env`: la carga `systemd` desde
  `/etc/webcmp/encryption.env`.
- `scripts/generate_env.py` escribe `LOG_LEVEL` y `LOG_FILE`, pero
  `config/settings/base.py` no los consume: `LOGGING` está hardcodeado.
- Varios valores no son 1:1 con una variable (`DATABASES['default']['OPTIONS']`,
  el backend efectivo de caché, el perfil de configuración).

Por eso la página informa lo que el proceso está usando, y el archivo se lee
solo para precargar el formulario y detectar diferencias.

## 2. Lista blanca de edición

Editables, agrupados como se muestran:

- **Sitio**: `SITE_URL`.
- **Avisos operativos**: `EMERGENCY_BANNER_ENABLED`, `NOTIFICATIONS_ENABLED`,
  `NOTIFICATIONS_MAX_VISIBLE`, `NOTIFICATIONS_RETENTION_READ`,
  `NOTIFICATIONS_RETENTION_UNREAD`, `NOTIFICATIONS_WARNING_HORIZON_HOURS`,
  `INVOICE_PENDING_REMINDER_DAYS`.
- **Correo**: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`,
  `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`.
- **LDAP**: `LDAP_SERVER_URI`, `LDAP_START_TLS`, `LDAP_BIND_DN`,
  `LDAP_BIND_PASSWORD`, `LDAP_USER_SEARCH_BASE`, `LDAP_GROUP_SEARCH_BASE`,
  `LDAP_STAFF_GROUP`, `LDAP_SUPERUSER_GROUP`.
- **Actualizaciones del panel**: `UPDATE_REPO_URL`, `UPDATE_BRANCH_REF`,
  `UPDATE_REPO_API`, `UPDATE_CHECK_TTL`, `UPDATE_CHECK_TIMEOUT`.
- **Observaciones (FTP)**: `FTP_OBS_HOST`, `FTP_OBS_USER`, `FTP_OBS_PASS`,
  `FTP_OBS_PORT`.

Criterio: queda fuera lo que **impide arrancar**, porque un panel que guarda una
configuración inválida deja el sitio sin levantar y sin la propia página para
corregirla. Quedan fuera, por ese criterio:

- `DB_*`: un error deja el sitio sin base.
- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`: un error
  produce 400 en el login.
- `SECRET_KEY`, `ENCRYPTION_KEY`, `DB_PASS`: material de clave del que dependen
  las sesiones y el descifrado de credenciales.
- `EMAIL_BACKEND`: `production.py` levanta `ImproperlyConfigured` si apunta a un
  backend que descarta correo o no verifica el certificado.
- `UPDATE_DEPLOYED_SHA_FILE`, `UPDATE_STATUS_FILE`, `UPDATE_REQUEST_FILE`: son
  el contrato con el script de despliegue, no un umbral operativo.
- `DEBUG` y `PRODUCTION`: cambian el perfil completo.

Correo, LDAP y Actualizaciones sí son editables, pero el formulario valida las
combinaciones que `config/settings` rechaza al iniciar (`EMAIL_USE_TLS` con
`EMAIL_USE_SSL`, puerto 465 sin TLS implícito, `EMAIL_HOST` sin puerto,
`LDAP_ENABLED` sin servidor o sin base de búsqueda). Sin esa validación,
"editable" sería "capaz de tumbar el sitio".

Los tres secretos editables (`EMAIL_HOST_PASSWORD`, `LDAP_BIND_PASSWORD`,
`FTP_OBS_PASS`) se muestran como campos de contraseña siempre vacíos, con un
indicador `Configurada` / `Sin configurar`. Dejarlos en blanco no cambia ni
borra el valor guardado.

Cada campo de texto o numérico lleva un placeholder que empieza por `Ej.:` y
muestra la forma esperada del valor (`Ej.: https://cmp.camaguey.cu/`,
`Ej.: smtp.cmp.cu`, `Ej.: ldap://dc.cmp.cu:389`). Es una ayuda de formato, no
una pista del valor actual: los campos de contraseña usan
`Ej.: contraseña nueva`.

## 3. Escritura del archivo

`apps/core/env_file.py`:

- **Quirúrgica**: solo se reescriben las líneas de las claves recibidas. Los
  comentarios, las líneas en blanco, el orden y las claves desconocidas se
  conservan. Una clave comentada (`# CLAVE=...`) no se toca: se agrega la línea
  activa al final, porque "descomentar" automáticamente sería adivinar.
- **Atómica**: temporal en el mismo directorio + `os.replace`, con `fsync` y
  conservación del modo. Un corte deja el archivo viejo, no uno truncado.
- **Serializada**: `flock` exclusivo sobre `<archivo>.lock`, para que dos
  guardados simultáneos no se pisen. En Windows el bloqueo es un no-op.
- **Sin sorpresas**: solo se escriben las claves cuyo valor cambió. Si la clave
  no está en el archivo y el valor coincide con el efectivo, tampoco se agrega.

## 4. Aplicar los cambios sin darle root al proceso web

Django lee el entorno al arrancar. Escribir el `.env` no cambia nada en
caliente, así que la página:

- Advierte que los cambios aplican al reiniciar.
- Marca `Pendiente de reinicio` los parámetros cuyo valor en archivo difiere del
  valor en ejecución.
- Ofrece **Aplicar cambios**: guarda y pide el reinicio en una sola acción, para
  que nadie tenga que recordar el orden ni entrar por SSH.

El proceso web **no** ejecuta `systemctl`. Reiniciar desde ahí sería darle root a
la aplicación: `webcmp` corre con el shell en `/bin/false` y sin sudo, a
propósito. El botón escribe `/var/lib/webcmp/apply-request` y se desentiende; una
unidad `webcmp-apply.path` (`PathExists=`) la ve aparecer y arranca
`webcmp-apply.service`, que corre como root y ejecuta
`/usr/local/sbin/webcmp-apply-env`.

Ese ejecutor es el único punto con privilegios, y está acotado a propósito:

- No lee destinos ni comandos del archivo de petición. Del contenido solo toma
  el nombre de quien pidió, saneado, para el registro.
- Las unidades que reinicia están en `Environment="UNITS=..."`, dentro del unit
  que renderiza `install.sh` desde `GUNICORN_UNIT` y `HUEY_UNIT`.
- Vive en `/usr/local/sbin`, `root:root`, fuera del checkout que escribe
  `webcmp`.
- El archivo se crea con `O_EXCL`: dos pestañas no encolan dos reinicios, y el
  segundo intento se avisa en vez de pisar el primero.
- Se consume antes de tomar el `flock`, porque `PathExists=` solo reacciona a la
  transición ausente→presente: un archivo que quede en disco bloquearía todas
  las peticiones siguientes sin un error visible.

El ejecutor deja `/var/lib/webcmp/apply-status.json` con `running`, `ok` o
`failed`, y la página lo lee. Un `running` de más de 180 s se informa como
**sin confirmar** en vez de "en curso" para siempre: si systemd mató la unidad,
el panel no puede seguir esperando. Un estado desconocido o un JSON roto no se
muestran: la página no inventa resultados.

## 5. Seguridad

- Acceso solo para superusuarios (`UserPassesTestMixin`), mismo criterio que
  `TaskMonitoringView` y `UpdateStatusView`.
- Los secretos nunca se renderizan: `secret_status()` devuelve únicamente
  `key`, `label` y `configured`; la plantilla muestra solo `label` y el estado.
- `REDIS_URL` se muestra con las credenciales sustituidas por `***`.
- Los tests verifican que el HTML no contiene ni `SECRET_KEY` ni la contraseña
  de la base usada en el archivo de prueba.
- Si el proceso no puede escribir el archivo, el formulario se deshabilita y el
  POST no lo toca.

## 6. Auditoría

Cada guardado con cambios crea una fila de `ActivityLog` con `action_flag=2`
(cambio), `object_repr='Entorno del servidor'`, IP y User-Agent, y el mensaje con
las claves modificadas. Los valores no se registran.

## 7. Presentación

La página habla el idioma de quien la usa, no el del código: las etiquetas y los
valores de solo lectura son nombres en castellano. Ni `DEBUG`, ni
`ALLOWED_HOSTS`, ni `django.core.mail.backends.smtp.EmailBackend` aparecen en el
HTML; el motor se muestra como `SMTP`, `Redis` o `PostgreSQL`, y un backend
desconocido cae a su último segmento para no quedar en blanco.

La grilla es una sola: `EnvField.width` declara `'full'` o `'half'` en el
catálogo y la vista lo traduce a `col-12` / `col-md-6`. Los valores largos (URLs,
DN, remitente, secretos) ocupan la fila; los números y los interruptores
comparten columna. El orden de los campos dentro de cada tarjeta es el del
catálogo, agrupado por afinidad: los dos interruptores de correo juntos, el par
de retención de avisos junto, y las dos palancas de LDAP antes de sus datos de
conexión.

Cada campo sigue el mismo orden de lectura: etiqueta, control, texto de ayuda y
por último `En ejecución`, que es el dato que el operador no puede cambiar.
