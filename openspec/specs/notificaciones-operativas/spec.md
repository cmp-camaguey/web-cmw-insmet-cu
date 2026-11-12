# notificaciones-operativas Specification

Capability: `notificaciones-operativas` (avisos operativos para superusuarios)

## Purpose

El sistema avisa por su cuenta, dentro del panel, cuando algo que el
administrador tiene que atender falla o está por vencer: una tarea de Huey en
`ERROR`, un PDF o un correo de factura que no salieron, un aviso meteorológico
próximo a caducar, una factura que lleva demasiado sin cobrarse o una solicitud
de servicio que un cliente acaba de mandar.

El aviso se crea en el borde que conoce el hecho, nunca por una señal genérica
sobre el modelo: el PDF fallido de una factura también deja una tarea en
`ERROR`, y dos señales producirían dos avisos del mismo hecho. Desde el cambio
030 la bandeja también es del cliente: la campana y las vistas de avisos piden
solo sesión iniciada, y el aislamiento lo da la consulta por destinatario. No
hay correo: es un canal aparte, con su propio control de duplicados.

## Requirements

### Requirement: NOTIF-REGISTRO

The system SHALL store one row in `Notification` per hecho que se debe avisar,
con `uuid`, `recipient`, `severity`, `source`, `title`, `body`, `url`,
`dedupe_key`, `created_at`, `read_at` y `expires_at`.

#### Scenario: Un aviso recién creado está sin leer

- GIVEN un hecho que merece aviso y ningún aviso previo con su clave
- WHEN se llama a `notify` con severidad, título y clave de deduplicación
- THEN existe una `Notification` para ese destinatario con `read_at` nulo
- AND `is_read` es `False`
- AND el orden por defecto de la lista es `-created_at`

#### Scenario: Una severidad inventada se rechaza

- GIVEN una severidad que no es `info`, `warning` ni `error`
- WHEN se llama a `notify`
- THEN se lanza `ValueError` nombrando las severidades válidas
- AND no se escribe ninguna fila

#### Scenario: El enlace guardado es una ruta relativa

- GIVEN un aviso cuyo destino está dentro del panel
- WHEN se guarda
- THEN `url` es una ruta relativa (por ejemplo `/comercial/facturas/`)
- AND no es una URL absoluta, porque el dominio cambia entre entornos

#### Scenario: Los permisos son los cuatro del proyecto

- GIVEN el modelo `Notification`
- WHEN se inspecciona su `Meta`
- THEN `default_permissions` es `()`
- AND declara `view_notification`, `add_notification`, `change_notification` y
  `delete_notification` en español

### Requirement: NOTIF-DEDUPE

The system SHALL prevent two unread notifications with the same
`(recipient, dedupe_key)` through a partial unique constraint, and SHALL derive
the key from the hecho and never from the momento.

#### Scenario: Dos workers ven el mismo fallo a la vez

- GIVEN dos procesos que intentan notificar el mismo hecho de forma simultánea
- WHEN ambos escriben
- THEN la base admite una sola fila sin leer
- AND el segundo recibe `IntegrityError` y devuelve la fila del primero, sin
  propagar el error

#### Scenario: Un hecho ya leído puede volver a avisar

- GIVEN un aviso leído con una clave de deduplicación
- WHEN el mismo hecho vuelve a ocurrir
- THEN se crea una fila nueva
- AND la anterior se conserva en el historial

#### Scenario: El reintento de la misma tarea no duplica

- GIVEN una tarea de Huey que falla, se reintenta y vuelve a fallar
- WHEN se notifica cada intento con el trabajo lógico como parte de la clave
- THEN existe una sola notificación sin leer
- AND `created_at` no forma parte de la clave, porque un reintento no es un hecho
  nuevo

#### Scenario: Un recordatorio que sigue pendiente reaparece con límite

- GIVEN un recordatorio ya leído cuyo hecho continúa pendiente
- WHEN vuelve a evaluarse dentro de su `repeat_after`
- THEN no se crea otra fila
- AND pasado el intervalo, el hecho vuelve a avisar como mucho una vez más

### Requirement: NOTIF-FUENTES

The system SHALL notify only the hechos declarados accionables, and SHALL NOT
notify internal task failures outside the allowlist.

#### Scenario: Solo las tareas de la allowlist avisan

- GIVEN `TASK_ERROR_ALLOWLIST` con `send_email_task`
- WHEN una tarea fuera de la lista termina en `ERROR`
- THEN no se crea ninguna notificación
- AND una tarea nueva no avisa hasta que alguien decida que su fallo es
  accionable

#### Scenario: El fallo de PDF y el de correo son dos hechos

- GIVEN una factura cuyo PDF falla y cuyo correo también falla
- WHEN se notifican las dos ramas `FAILED`
- THEN existen dos avisos, uno por cada artefacto
- AND un reintento que vuelve a fallar no añade una fila nueva

#### Scenario: Un aviso por vencer muere con el aviso que recuerda

- GIVEN un `Warning` con `valid_until` dentro del horizonte configurado
- WHEN la tarea diaria lo evalúa
- THEN se avisa con severidad `warning`
- AND `expires_at` es el `valid_until` del aviso
- AND un `Warning` ya vencido no genera recordatorio, porque no queda nada que
  adelantar

#### Scenario: El cliente se entera de su factura y de su servicio

- GIVEN una factura emitida y una suscripción cuyo estado cambió a pagado
- WHEN se notifican ambos hechos con `source='invoice'` y `source='subscription'`
- THEN el titular de cada una recibe su aviso además de los superusuarios
- AND el aviso nace donde ocurre el hecho, tanto en la facturación por lote como
  en la manual
- AND `requested` y `pending` no avisan al cliente, porque el primero lo provoca
  él y el segundo llega con la factura

#### Scenario: La solicitud del cliente avisa, la del staff no

- GIVEN una `ServiceSubscription` creada desde el portal público
- WHEN se notifica
- THEN los superusuarios reciben un aviso `info`
- AND una suscripción creada por el staff no avisa, porque quien la crea ya sabe
  que existe

### Requirement: NOTIF-AUDIENCIA

The system SHALL declare the allowed audience per `source` and SHALL apply it
before writing: an unknown source SHALL be treated as internal.

#### Scenario: Una fuente interna no alcanza a un cliente

- GIVEN una llamada con `source='huey'` que pasa `customers=`
- WHEN se escriben los avisos
- THEN solo los reciben los superusuarios activos
- AND lo desconocido se trata como interno, porque el error caro es mandarle a un
  cliente un aviso de operación

#### Scenario: Un aviso de fallo no hereda la audiencia de su fuente

- GIVEN un fallo de PDF con `source='invoice'`, que sí admite clientes
- WHEN se notifica sin declarar titulares
- THEN el aviso queda solo para los superusuarios

#### Scenario: El titular no recibe dos veces

- GIVEN un superusuario que además es el titular del cliente afectado
- WHEN se notifica el hecho
- THEN recibe una sola fila, no dos con la misma clave

### Requirement: NOTIF-PREFERENCIAS

The system SHALL store one `NotificationPreference` per `(user, source)` with two
independent channels (`in_app`, `email`), and SHALL treat the absence of a row as
enabled.

#### Scenario: El cliente apaga la campana

- GIVEN una preferencia con `in_app=False` y `email=True`
- WHEN nace un aviso de esa fuente
- THEN no aparece en la campana y el correo sigue saliendo

#### Scenario: El titular apaga el correo de una fuente

- GIVEN una preferencia con `email=False` para `warning`
- WHEN se publica un aviso con envío marcado
- THEN no recibe ese correo y sí el resto de la lista

#### Scenario: Un superusuario no puede silenciar lo interno

- GIVEN un superusuario con una fila de preferencia que apaga una fuente
- WHEN nace un aviso interno de esa fuente
- THEN lo sigue recibiendo: el filtro de preferencias no toca a los
  administradores

### Requirement: NOTIF-CONTADOR

The system SHALL expose the unread count as JSON at `notifications:unread` with
`ETag` revalidation, and SHALL compute it from the same visibility rule used by
the navbar and the history page.

#### Scenario: Una sola definición de visibilidad

- GIVEN un aviso vencido que la poda todavía no borró
- WHEN el navbar, el historial y el contador lo evalúan
- THEN ninguno lo muestra
- AND el badge no puede decir 3 mientras la lista muestra 2

#### Scenario: El contador no arrastra el estado de despliegue

- GIVEN un superusuario autenticado
- WHEN consulta `GET /notificaciones/sin-leer/`
- THEN la respuesta es `{"unread": n}` y solo cuesta un `COUNT` indexado
- AND no lee archivos ni consulta el remoto, porque ese JSON ya no transporta
  este número

#### Scenario: El cuerpo sin cambios no se reenvía

- GIVEN un cliente que ya tiene el `ETag` vigente
- WHEN repite la consulta con `If-None-Match`
- THEN la respuesta es `304` sin cuerpo
- AND el `ETag` se deriva del JSON final, para que el `304` y el `200` no puedan
  discrepar

#### Scenario: La respuesta no se guarda en un cache intermedio

- GIVEN la respuesta del contador
- WHEN se inspeccionan sus cabeceras
- THEN `Cache-Control` es `private, no-cache`
- AND un cache compartido no puede servir el conteo de otra persona

#### Scenario: Cada sesión consulta su propio conteo

- GIVEN un cliente autenticado con dos avisos sin leer y un superusuario con uno
- WHEN cada uno consulta el contador
- THEN el cliente recibe `2` y el superusuario `1`
- AND sin sesión iniciada la respuesta es `403`

#### Scenario: Sistema apagado y cero pendientes no son lo mismo

- GIVEN `NOTIFICATIONS_ENABLED=False`
- WHEN se pide el conteo
- THEN la respuesta es `null` y el icono se oculta
- AND con el sistema encendido y nada pendiente la respuesta es `0`, que pinta
  el icono sin badge

#### Scenario: El cache compartido solo existe con Redis

- GIVEN `USE_REDIS_CACHE=False`, con LocMem por proceso
- WHEN se pide el conteo
- THEN se calcula en cada llamada y no se cachea
- AND con Redis se cachea con TTL corto y se invalida al escribir

### Requirement: NOTIF-LECTURA

The system SHALL mark a notification as read without deleting it, individually
or in bulk, and SHALL accept any authenticated user, isolating each one by
`recipient`.

#### Scenario: Marcar una como leída

- GIVEN un aviso sin leer del propio usuario
- WHEN se marca como leído
- THEN `read_at` deja de ser nulo
- AND la fila permanece para el historial

#### Scenario: Marcar todas

- GIVEN varios avisos sin leer
- WHEN se marca todo como leído
- THEN ninguno queda pendiente para ese usuario

#### Scenario: No se marca el aviso de otro

- GIVEN un aviso de otro usuario
- WHEN se intenta marcar como leído
- THEN la petición no lo modifica

### Requirement: NOTIF-PODA

The system SHALL prune notifications daily with three non-overlapping rules and
SHALL be idempotent.

#### Scenario: Se borran vencidos, leídos viejos y no leídos viejos

- GIVEN un aviso con `expires_at` pasado, otro leído hace más de
  `NOTIFICATIONS_RETENTION_READ` días y otro sin leer creado hace más de
  `NOTIFICATIONS_RETENTION_UNREAD` días
- WHEN corre la poda
- THEN los tres se borran
- AND el resultado informa el conteo de cada categoría por separado, sin sumar
  dos veces la misma fila

#### Scenario: La poda corre cada día antes de que nadie mire el panel

- GIVEN la tarea periódica registrada en `AppConfig.ready()`
- WHEN Huey la dispara a las 4 UTC
- THEN es medianoche en `America/Havana` y no compite con el uso del panel

#### Scenario: Correrla dos veces no cambia nada

- GIVEN una poda ya ejecutada
- WHEN se ejecuta de nuevo
- THEN no borra filas adicionales y termina sin error

### Requirement: NOTIF-INTERRUPTOR

The system SHALL honour `NOTIFICATIONS_ENABLED` as a single switch that stops
new rows, hides the icon and never raises.

#### Scenario: Apagado no escribe ni rompe

- GIVEN `NOTIFICATIONS_ENABLED=False`
- WHEN cualquier fuente intenta notificar
- THEN no se escribe ninguna fila
- AND no se lanza ninguna excepción al llamador

#### Scenario: Los ajustes tienen valor por defecto

- GIVEN un entorno sin las variables `NOTIFICATIONS_*`
- WHEN arranca la aplicación
- THEN hay valores por defecto para el flag, las dos ventanas de retención, el
  máximo visible del desplegable, el horizonte del aviso por vencer y los días
  del recordatorio de facturas

#### Scenario: La poda sigue activa con las notificaciones apagadas

- GIVEN `NOTIFICATIONS_ENABLED=False`
- WHEN corre la tarea de poda
- THEN limpia las filas existentes
- AND limpiar datos viejos no es una función visible que haya que apagar
