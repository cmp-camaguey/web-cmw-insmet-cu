# 026-sistema-notificaciones

> **Estado: idea documentada, no aprobada.** No hay implementacion, ni rama, ni
> tareas abiertas. Existe para que la idea no se pierda y para que, cuando se
> retome, no se empiece copiando el diseno de otro proyecto sin entender por que.

## Por que

El sistema ya registra sus propios fallos en tres sitios distintos, y **nadie se
entera hasta que un cliente llama**:

| Donde queda registrado | Que pasa hoy |
|---|---|
| `TaskExecutionLog` (`apps/core/models.py`) | Una tarea Huey falla, se guarda el error y el traceback, y queda ahi. |
| `Invoice.pdf_status` / `pdf_error` | El PDF de una factura no se genero y la factura se queda sin documento. |
| `Invoice.email_status` / `email_error` | El correo al cliente fallo y no salio. |
| `/var/lib/webcmp/deploy-status.json` | Un despliegue fallo y solo se ve si alguien abre el navbar a mirar. |

El problema no es falta de datos: es que **estan escritos para quien va a
buscarlos, y no hay nadie buscando**. Un aviso en el navbar convierte "quedo en
un log" en "hay algo que hacer".

## La idea, en una frase

Una **proyeccion de atencion**: filas que representan "esto necesita que un humano
lo mire", no un tercer log de eventos.

## Que NO es

- **No es un log de eventos.** Ya hay dos (`TaskExecutionLog`, `ActivityLog`) y un
  tercero solo agrega ruido. La notificacion **referencia** el hecho original por
  URL y no lo duplica.
- **No notifica cada CRUD.** Notificar cada `save()` es la forma mas rapida de que
  el badge marque 300 y nadie lo abra nunca.
- **No es tiempo real.** El proyecto es WSGI y no tiene stack ASGI. Ver
  "Transporte" mas abajo.
- **No reemplaza el correo.** `EmailRecipientList` / `mail_send` y el envio de
  PDFs siguen exactamente como estan; esto es la capa de "lo vio alguien en el
  panel".

## Que aprende de SquidStats (y que descarta)

Se evaluo `kaelthasmanu/SquidStats` (Flask, MIT) como referencia. Sirve, pero no
como se lo suele citar.

**Se adopta:**

- **Dedup por hash** antes de crear: `_generate_message_hash(message, source,
  type)` + `_check_duplicate_notification`. Su version resuelve "el mismo aviso no
  se repite", y es lo que evita que el centro de notificaciones se vuelva un
  vertedero.
- **Poda por cantidad** (`_cleanup_old_notifications(keep_count=N)`). Se adapta a
  una poda por antiguedad y estado de lectura (ver `design.md`).
- **Contador de no leidas** (`_get_unread_count`).

**Se descarta, con motivo:**

| Su idea | Por que no aca |
|---|---|
| `systemctl restart squid` desde el request | Funciona porque su unit instalado corre `User=root` (`utils/squidstats.service`, el que escribe su `install.sh`). `webcmp` tiene shell `/bin/false` y no tiene sudo, a proposito. Ver `025-update-web-deploy`. |
| Socket.IO | Corremos WSGI; el transporte ya se decidio en 025 y no cambia por esto. |
| Monitor en `threading.Thread` dentro del proceso web | Con N workers de Gunicorn hay N monitores: avisos duplicados y N `git fetch` compitiendo por `.git/lock`. |
| `git fetch` desde la vista | Toca el checkout y necesita escritura. El chequeo de despliegue ya usa `git ls-remote`, que no toca nada. |

## Fuentes de aviso (concretas, no hipoteticas)

Cada fuente existe hoy y ya produce el dato:

1. **Tarea Huey en `ERROR`** — `TaskExecutionLog.status == 'ERROR'`, con
   `error_message` y `traceback`. Aviso con enlace al log de tareas.
2. **PDF de factura fallido** — `Invoice.pdf_status == 'failed'`, con
   `pdf_error`. Enlace a la factura.
3. **Correo de factura fallido** — `Invoice.email_status == 'failed'`, con
   `email_error`.
4. **Despliegue terminado** — `success` o `failed` leido de
   `/var/lib/webcmp/deploy-status.json` al cerrar `autodeploy.sh`.
5. **Aviso meteorologico por vencer** — `Warning.valid_until` dentro de las
   proximas 24 h y sin reemplazo. Enlace al aviso.

Fuera de alcance por ahora: suscripciones. `ServiceSubscription` **no tiene
expiracion** — su propio `status_display` lo documenta: el estado es solo el del
pago. Inventar un vencimiento seria inventar un dato.

## Transporte

Server-rendered mas el polling que ya existe. El navbar del dashboard ya hace
`fetch` cada 60 s en reposo (`static/dist/js/dashboard-update.js`); el contador de
no leidas viaja en **esa misma respuesta** (`GET` del estado de actualizacion) y
no en un endpoint nuevo. Cero infraestructura nueva, cero proceso nuevo.

## Rollback

Un flag. `NOTIFICATIONS_ENABLED` en settings (o en `SiteConfiguration` si se
quiere apagar sin desplegar): apagado, las vistas dejan de crear filas y el
navbar no renderiza el icono. Las filas existentes se quedan; la poda las limpia
sola.

## Criterios de aceptacion (cuando se implemente)

- Un fallo de tarea Huey produce **una** notificacion por tarea logica, no una
  por reintento.
- El mismo fallo repetido tres veces con la notificacion sin leer deja **una**
  fila; si el admin la marca leida y vuelve a fallar, aparece una nueva.
- El contador del navbar coincide con las filas no leidas del usuario.
- Apagar `NOTIFICATIONS_ENABLED` deja de crear filas y esconde el icono sin
  errores, sin tocar la base.
- La poda borra las leidas de mas de 30 dias y las no leidas de mas de 90, y es
  idempotente.
- Ningun aviso expone `traceback` completo en la lista; el detalle queda en la
  vista original a la que enlaza.
