# dashboard-update-button Specification

Capability: `dashboard-update-button` (actualización del servidor desde el panel)

## Purpose

Un superusuario puede ver si el servidor está sirviendo la última versión de
`main` y pedir el despliegue desde el dashboard, sin SSH. El proceso web no gana
privilegios: escribe una petición que ejecuta el usuario de despliegue.

## ADDED Requirements

### Requirement: UPDATE-STATE

The system SHALL report the update state as JSON at `dashboard:update_status`, and
SHALL accept the value only from one of `up_to_date`, `available`, `running` or
`unknown`.

#### Scenario: Coinciden desplegado y remoto

- GIVEN `/etc/webcmp/deployed` contiene un SHA válido
- AND `refs/heads/main` del remoto resuelve al mismo SHA
- WHEN un superusuario consulta `dashboard:update_status`
- THEN la respuesta es 200 y `state` es `up_to_date`

#### Scenario: Hay un commit nuevo

- GIVEN el commit desplegado es `A` y `refs/heads/main` resuelve a `B`, con `A != B`
- WHEN un superusuario consulta el estado
- THEN `state` es `available`
- AND `deployed` es `A` y `latest` es `B`

#### Scenario: No consta qué se desplegó

- GIVEN `/etc/webcmp/deployed` no existe, o su contenido no es un SHA de 40 hex
- WHEN se consulta el estado
- THEN `state` es `unknown` y `deployed` es `null`
- AND el sistema NO afirma que hay una actualización disponible

#### Scenario: El remoto no responde

- GIVEN `git ls-remote` falla, expira o `git` no está instalado
- WHEN se consulta el estado
- THEN `state` es `unknown` y `check_error` describe el fallo sin exponer rutas
  del servidor
- AND el resultado se cachea 60 segundos (el éxito se cachea `UPDATE_CHECK_TTL`)

#### Scenario: El estado lo escribe otro usuario

- GIVEN `deploy-status.json` contiene claves ajenas al contrato, o no es JSON válido
- WHEN se consulta el estado
- THEN el sistema expone solo `status`, `message`, `sha` y `finished_at`
- AND un `sha` que no sea 40 hex se devuelve como `null`

#### Scenario: Solo superusuarios

- GIVEN un usuario autenticado que es `is_staff` pero no `is_superuser`
- WHEN consulta `dashboard:update_status`
- THEN la respuesta es 403, no un redirect al login (la vista es JSON)

### Requirement: UPDATE-COMMITS

The system SHALL include the messages of the pending commits in
`dashboard:update_status` when `state` is `available`, and this information SHALL
be informative only: a failure obtaining it SHALL NOT change `state`.

#### Scenario: Hay actualización y la API responde

- GIVEN `state` es `available`, con desplegado `A` y último `B`
- WHEN un superusuario consulta el estado
- THEN `commits.commits` contiene hasta 10 entradas con `sha` corto, `subject` y `author`
- AND `subject` es solo la primera línea del mensaje del commit
- AND `commits.total` es el número de commits del rango y `truncated` indica si se
  mostraron menos de los que hay

#### Scenario: La API falla o limita

- GIVEN la API pública responde 403 (límite anónimo), 404, error de red o JSON inválido
- WHEN se consulta el estado
- THEN `state` sigue siendo `available` y los SHA se reportan igual
- AND `commits` es `null`, no una lista vacía, para distinguir "no se pudo consultar"
  de "no hay mensajes"
- AND el fallo se cachea 60 segundos, mientras el éxito se cachea 300

#### Scenario: No hay rango que comparar

- GIVEN `state` es `up_to_date` o `unknown`
- WHEN se consulta el estado
- THEN el sistema NO consulta la API de comparación

#### Scenario: La petición de despliegue no paga la consulta

- GIVEN `state` es `available`
- WHEN un superusuario hace `POST` a `dashboard:update_request`
- THEN la respuesta se decide solo con los SHA
- AND no espera a la API de comparación

#### Scenario: Un mensaje de commit es contenido no confiable

- GIVEN un mensaje de commit contiene HTML o una etiqueta con manejador de eventos
- WHEN el panel lo muestra
- THEN se inserta como texto y NO se interpreta como HTML

### Requirement: UPDATE-REQUEST

The system SHALL create the deploy request file with `O_CREAT | O_EXCL`, so that
two concurrent requests SHALL NOT enqueue two deployments.

#### Scenario: Petición aceptada

- GIVEN `state` es `available`
- WHEN un superusuario hace `POST` a `dashboard:update_request`
- THEN la respuesta es 202
- AND el archivo de petición existe y contiene `requested_by`, `expected_sha` y
  `requested_at`

#### Scenario: Ya hay una petición en disco

- GIVEN el archivo de petición ya existe
- WHEN llega otra petición
- THEN la respuesta es 409 y el archivo original NO se sobrescribe

#### Scenario: No hay nada que desplegar

- GIVEN `state` es `up_to_date`, `running` o `unknown`
- WHEN llega una petición
- THEN la respuesta es 409 y no se crea ningún archivo

#### Scenario: El servidor no permite escribir la petición

- GIVEN el proceso web no puede crear el archivo
- WHEN llega una petición
- THEN la respuesta es 503 con un motivo legible y el error real va al log

### Requirement: UPDATE-EXECUTOR

The deploy executor SHALL resolve `refs/heads/main` itself and SHALL NOT use the
SHA written by the web process as the deploy target.

#### Scenario: La petición pide otro commit

- GIVEN el archivo de petición contiene un `expected_sha` que no es `main`
- WHEN el ejecutor corre
- THEN despliega el SHA de `refs/heads/main`
- AND el `expected_sha` solo aparece en el log como dato informativo

#### Scenario: Carrera entre dos peticiones

- GIVEN dos peticiones llegan casi a la vez
- WHEN el ejecutor arranca
- THEN el `flock` deja correr una sola instancia
- AND la petición se borra al consumirse, para que el `.path` pueda volver a
  dispararse

#### Scenario: El despliegue falla

- GIVEN `webcmp-deploy` sale con código distinto de cero
- WHEN el ejecutor termina
- THEN escribe `{"status": "failed", ...}` de forma atómica
- AND no reintenta solo (un reintento automático de una migración rota es un bucle)

#### Scenario: El despliegue funciona

- GIVEN `webcmp-deploy` sale con código cero
- WHEN el ejecutor termina
- THEN escribe `{"status": "ok", ...}` con el SHA desplegado

#### Scenario: El ejecutor muere a mitad de un despliegue

- GIVEN el ejecutor escribió `running` con su `started_at`
- AND systemd lo mata antes de escribir el estado final
- WHEN un superusuario consulta el estado una hora después
- THEN el sistema NO sigue reportando el despliegue como en curso
- AND vuelve a comparar el commit desplegado con `main`, para que el botón no
  quede inutilizado por un estado huérfano
- AND el `last_run` original se sigue reportando como información

### Requirement: UPDATE-PRIVILEGES

The web process SHALL NOT gain any capability to execute commands.

#### Scenario: Sin sudo nuevos

- GIVEN el sudoers instalado en el servidor
- WHEN se audita después de instalar este cambio
- THEN sigue teniendo exactamente las dos líneas de `webcmp-deploy`
- AND no existe ninguna regla que permita a `webcmp` ejecutar nada

#### Scenario: El ejecutor vive fuera del checkout

- GIVEN el ejecutor instalado
- WHEN se inspecciona su ruta
- THEN está en `/usr/local/sbin`, propiedad de `root:root`, modo 0755
- AND no en el checkout, que escribe el usuario del servicio

#### Scenario: El panel se apaga sin tocar código

- GIVEN el `.path` deshabilitado con `systemctl disable --now`
- WHEN el panel escribe una petición
- THEN no se ejecuta ningún despliegue
- AND el comando manual `webcmp-deploy <sha>` sigue funcionando igual
