# 033-entorno-servidor — requisitos

## S1 — Sección en el dashboard

El dashboard DEBE exponer una sección **Configuración → Entorno del servidor**
en la ruta `/config/entorno/` (`core:environment_config`).

- Visible y accesible solo para superusuarios.
- Aceptación: el superusuario recibe 200 y ve la entrada del menú; un usuario
  autenticado no superusuario recibe 403 y no ve la entrada.

## S2 — Configuración efectiva

La sección DEBE mostrar los valores en ejecución leídos de
`django.conf.settings` (y de `os.environ` solo para claves que no son settings),
agrupados en: Aplicación, Dominio y orígenes, Base de datos, Caché, Correo,
LDAP y Actualizaciones del panel.

- Aceptación: con `CACHES` apuntando a Redis, la tarjeta de Caché informa
  `RedisCache`; el perfil informa Producción o Desarrollo según `IS_PRODUCTION`.

## S3 — Secretos no expuestos

La página NO DEBE renderizar el valor de `SECRET_KEY`, `ENCRYPTION_KEY`,
`DB_PASS`, `EMAIL_HOST_PASSWORD`, `LDAP_BIND_PASSWORD` ni `FTP_OBS_PASS`.

- Solo se informa `Configurada` o `Sin configurar`.
- Aceptación: el HTML no contiene el valor de ninguno de esos secretos.

## S4 — Credenciales en URLs

`REDIS_URL` DEBE mostrarse sin usuario ni contraseña.

- Aceptación: `redis://:secreto@host:6379/1` se muestra como
  `redis://***@host:6379/1`; una URL sin credenciales se muestra igual.

## S5 — Parámetros editables

La sección DEBE permitir editar y escribir en el archivo de entorno únicamente:

- **Sitio**: `SITE_URL`.
- **Avisos operativos**: `EMERGENCY_BANNER_ENABLED`, `NOTIFICATIONS_ENABLED`,
  `NOTIFICATIONS_MAX_VISIBLE`, `NOTIFICATIONS_RETENTION_READ`,
  `NOTIFICATIONS_RETENTION_UNREAD`, `NOTIFICATIONS_WARNING_HORIZON_HOURS`,
  `INVOICE_PENDING_REMINDER_DAYS`.
- **Correo**: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`,
  `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`.
- **LDAP**: `LDAP_ENABLED`, `LDAP_SERVER_URI`, `LDAP_START_TLS`, `LDAP_BIND_DN`,
  `LDAP_BIND_PASSWORD`, `LDAP_USER_SEARCH_BASE`, `LDAP_GROUP_SEARCH_BASE`,
  `LDAP_STAFF_GROUP`, `LDAP_SUPERUSER_GROUP`.
- **Actualizaciones del panel**: `UPDATE_REPO_URL`, `UPDATE_BRANCH_REF`,
  `UPDATE_REPO_API`, `UPDATE_CHECK_TTL`, `UPDATE_CHECK_TIMEOUT`.
- **Observaciones (FTP)**: `FTP_OBS_HOST`, `FTP_OBS_USER`, `FTP_OBS_PASS`,
  `FTP_OBS_PORT`.

- Aceptación: guardar `NOTIFICATIONS_MAX_VISIBLE=25` agrega esa línea y deja el
  resto del archivo intacto.

## S6 — Parámetros de solo lectura

DEBEN mostrarse en solo lectura y no DEBEN poder modificarse desde el
formulario: `DEBUG`, `PRODUCTION`, `SECRET_KEY`, `ENCRYPTION_KEY`,
`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`,
`EXTERNAL_HOSTNAME`, `DB_*`, `USE_REDIS_CACHE`, `REDIS_URL`, `EMAIL_BACKEND` y
las rutas de archivo de las actualizaciones (`UPDATE_DEPLOYED_SHA_FILE`,
`UPDATE_STATUS_FILE`, `UPDATE_REQUEST_FILE`).

Las rutas absolutas del servidor (archivo de correos, archivos de estado del
despliegue) NO DEBEN mostrarse en la página: publican el layout del sistema de
archivos sin aportar nada a la operación.

`EMAIL_BACKEND` queda fuera de la lista blanca porque `production.py` levanta
`ImproperlyConfigured` si apunta a un backend que descarta correo o no verifica
el certificado: un error ahí deja el sitio sin arrancar y sin panel con el que
corregirlo.

## S11 — Contraseñas editables

Los campos `EMAIL_HOST_PASSWORD`, `LDAP_BIND_PASSWORD` y `FTP_OBS_PASS` DEBEN
renderizarse como campos de contraseña siempre vacíos, con un indicador
`Configurada` / `Sin configurar`.

- Un campo vacío NO DEBE modificar ni borrar el valor guardado.
- Aceptación: guardar el formulario sin escribir la contraseña conserva el valor
  anterior; escribir una nueva lo reemplaza; el HTML nunca contiene el valor.

## S12 — Combinaciones que impiden arrancar

El formulario NO DEBE aceptar una combinación que `config/settings` rechace al
iniciar:

- `EMAIL_USE_TLS` y `EMAIL_USE_SSL` activos a la vez.
- `EMAIL_PORT=465` sin `EMAIL_USE_SSL`.
- `EMAIL_HOST` con `EMAIL_PORT` vacío.
- `LDAP_ENABLED` activo sin `LDAP_SERVER_URI`.
- `LDAP_ENABLED` activo sin `LDAP_USER_SEARCH_BASE`.

- Aceptación: cada caso devuelve un error de formulario y no modifica el
  archivo.

## S13 — Textos sin referencia al archivo

La página NO DEBE nombrar el archivo de entorno en sus textos visibles. La
advertencia DEBE decir que los cambios se aplican al reiniciar el servicio y que
mientras tanto el sitio sigue usando los valores de **En ejecución**.

## S14 — Placeholders

Cada campo de texto o numérico DEBE mostrar un placeholder que empiece por
`Ej.:` y muestre la forma esperada del valor (por ejemplo
`Ej.: https://cmp.camaguey.cu/`, `Ej.: smtp.cmp.cu`,
`Ej.: ldap://dc.cmp.cu:389`). Los campos de contraseña DEBEN usar
`Ej.: contraseña nueva`, nunca el valor guardado. Los checkboxes no llevan
placeholder.

- Aceptación: el HTML contiene los placeholders de ejemplo y ningún placeholder
  coincide con un valor guardado.

## S7 — Escritura segura del archivo

La escritura DEBE preservar comentarios, líneas en blanco, orden y claves ajenas
al formulario, DEBE ser atómica (temporal en el mismo directorio +
`os.replace`), DEBE conservar el modo del archivo y DEBE serializarse con
`flock`.

- Aceptación: un archivo con comentarios y claves desconocidas conserva ambos
  tras un guardado; un valor con salto de línea se rechaza con `ValueError` y el
  archivo no cambia.

## S8 — Archivo no escribible

Si el proceso web no puede escribir el archivo, el formulario DEBE quedar
deshabilitado con un aviso explícito y el POST NO DEBE modificar el archivo ni
registrar actividad.

## S9 — Aplicación diferida

Los cambios NO DEBEN aplicarse en caliente. La página DEBE advertir que se
aplican al reiniciar el servicio y DEBE marcar como `Pendiente de reinicio` los
parámetros cuyo valor en archivo difiere del valor en ejecución. El panel NO
DEBE reiniciar el servicio.

## S10 — Auditoría

Cada guardado con cambios DEBE registrar una fila en `ActivityLog` con el
usuario, la IP, el User-Agent y las claves modificadas, sin los valores.

- Aceptación: tras guardar `NOTIFICATIONS_MAX_VISIBLE=25` existe una entrada con
  la clave en el mensaje y sin el número `25`.
