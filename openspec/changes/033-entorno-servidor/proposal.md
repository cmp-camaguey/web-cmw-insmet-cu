# 033-entorno-servidor — propuesta

> **Estado: aprobada el 2026-11-09.** El usuario pidió una nueva sección de
> configuración en el dashboard para la configuración interna del `.env`, con
> nombre adecuado, y acotó el alcance: solo son editables los parámetros que no
> comprometen el funcionamiento del sitio; el resto se muestra en solo lectura.

## 1. Motivación

La configuración del despliegue vive en el archivo `.env` y en el entorno del
proceso. Hoy, para saber qué está corriendo —backend de correo, host de la base,
si Redis está activo, qué hosts acepta Django— hay que entrar por SSH y leer el
archivo, que además no siempre dice la verdad: en producción la
`ENCRYPTION_KEY` llega por `systemd`, y hay claves que el generador escribe pero
ningún settings lee (`LOG_LEVEL`, `LOG_FILE`).

Ajustar los umbrales operativos (retención de avisos, recordatorios de facturas,
franja de emergencia) obliga a editar el `.env` a mano y reiniciar. Es una tarea
de operación cotidiana que no debería requerir consola.

## 2. Solución

Una nueva sección **Configuración → Entorno del servidor** en el dashboard:

- Muestra la configuración **efectiva**, leída de `django.conf.settings` y del
  entorno del proceso, agrupada por área.
- Los secretos se informan como `Configurada` / `Sin configurar`; nunca se
  envía un valor al navegador.
- Una **lista blanca** de parámetros operativos se puede editar y se escribe en
  el archivo de entorno preservando comentarios, orden y claves ajenas. Incluye
  las secciones de Correo, LDAP y Actualizaciones del panel, con validación de
  las combinaciones que impiden arrancar.
- Las credenciales de correo, LDAP y FTP se editan como campos de contraseña que
  nunca se precargan; dejarlos vacíos no cambia el valor guardado.
- Los parámetros que impiden arrancar (base de datos, hosts permitidos,
  CSRF/CORS, `EMAIL_BACKEND`, claves de sesión y descifrado, perfil) quedan en
  solo lectura.
- El panel **no reinicia** el servicio: avisa que los cambios aplican al
  reiniciar y marca como `Pendiente de reinicio` lo que difiere entre archivo y
  proceso.

## 3. Alcance

Dentro:

- `apps/core/env_config.py` (catálogo, valores efectivos, enmascarado, drift).
- `apps/core/env_file.py` (lectura y escritura quirúrgica, atómica y bloqueada).
- Formulario, vista, URL, plantilla y entrada de menú.
- `ENV_FILE_PATH` en `config/settings/base.py` para que el panel y `load_dotenv`
  apunten al mismo archivo.
- Pruebas de archivo, catálogo, formulario, vista, fuga de secretos y menú.

Fuera:

- Reiniciar el servicio desde el panel. Reiniciar desde la web es la acción que
  puede tumbar el sitio; se deja en manos del operador o del despliegue.
- Editar credenciales (`SECRET_KEY`, `ENCRYPTION_KEY`, `DB_PASS`,
  `EMAIL_HOST_PASSWORD`, `LDAP_BIND_PASSWORD`, `FTP_OBS_PASS`).
- Cablear `LOG_LEVEL` / `LOG_FILE`: el generador los escribe y ningún settings
  los lee. Se anota como hallazgo aparte.
- Recargar la configuración en caliente sin reinicio.

## 4. Criterios de aceptación

- La entrada **Entorno del servidor** aparece solo para superusuarios y responde
  403 a un usuario autenticado que no lo sea.
- El HTML de la página no contiene el valor de ningún secreto.
- Guardar un parámetro editable modifica solo esa clave del `.env` y deja
  intacto el resto del archivo.
- Un valor inválido no toca el archivo y devuelve un error de formulario.
- Si el proceso web no puede escribir el archivo, el formulario queda
  deshabilitado y el POST no modifica nada.
- Cada guardado queda en `ActivityLog` con las claves modificadas, sin valores.

## 5. Plan de reversa

El cambio no toca la base de datos ni migraciones. Revertir el commit elimina la
vista, la ruta y la entrada de menú; el `.env` sigue siendo un archivo de texto
que el operador puede editar a mano. No hay estado persistido que deshacer.
