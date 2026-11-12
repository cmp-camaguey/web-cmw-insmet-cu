# 030-boletin-y-notificaciones-de-clientes — propuesta

> **Estado: propuesta.** Registra la idea y el analisis del terreno; **no hay
> diseno ni implementacion**. `design.md` y `tasks.md` se escriben cuando se
> cierren las decisiones abiertas de §6, igual que se hizo con la fase 0 de 026.
>
> Origen: pedido del 2026-11-07 — notificaciones para clientes, aviso por correo
> a quien habilite "recibir novedades" en su perfil, formulario del landing
> operativo, y listas de correo que no se elijan a mano.

## 1. Motivacion

Cuatro huecos concretos, con la evidencia de cada uno en el codigo de hoy.

### 1.1 El formulario del landing no es un formulario

`apps/home/templates/pages/home/landing.html:342-358` tiene un `<input
type="email">` y un `<button type="submit">` **sueltos**: no hay etiqueta
`<form>`, ni `action`, ni `method`, ni `name` en el input, ni `{% csrf_token %}`.
No existe vista ni URL que reciba esa suscripcion (`apps/home/urls.py` no tiene
ningun endpoint de boletin).

Consecuencia: hoy es una maqueta. Escribir un correo y pulsar "Suscribirse" no
envia nada a ningun lado. Un visitante cree que se suscribio y no se suscribio a
nada.

### 1.2 La preferencia del perfil se guarda, pero nada la usa

`Profile.newsletter` existe (`apps/user_auth/models.py:26`), se puede marcar en
el registro (`apps/user_auth/forms/users.py:142`) y en el perfil
(`apps/user_auth/forms/profile.py:57`), y un signal la sincroniza con una lista
de correo llamada `newsletter` (`apps/user_auth/signals.py:9-23`).

Lo que no existe es **el envio**. Nadie manda ese boletin: no hay comando, ni
vista, ni tarea periodica que lea esa lista. El consentimiento se registra y se
cumple a medias — que es peor que no pedirlo, porque el usuario cree que va a
recibir algo.

### 1.3 Las listas se eligen a mano y el silencio no deja rastro

`Warning` (`apps/meteo/models.py:308`) y `WeatherReport`
(`apps/meteo/forms/weather_report.py:9`) llevan una FK
`email_recipient_list` que el operador selecciona en cada formulario. `mail_send`
(`apps/core/utils.py:116`) lee esa FK y, si esta vacia, **retorna en silencio**:

```python
recipient_list = (
    alert_obj.email_recipient_list.recipients.all() if alert_obj.email_recipient_list else []
)
if not recipient_list:
    return  # apps/core/utils.py:127
```

O sea: el aviso se publica en el portal, el correo no sale, y no queda ni un log
que lo diga. Olvidar la lista es el camino por defecto y es invisible.

### 1.4 Las notificaciones del portal son solo del admin

`notify_superusers` filtra `is_superuser=True, is_active=True`
(`apps/core/notifications.py:111`). Un cliente con cuenta no recibe **nada** de
lo que le pasa a su factura, su suscripcion o su solicitud: se entera si entra a
mirar.

## 2. Son dos problemas distintos

| | Aviso en el panel (in-app) | Correo (boletin / avisos) |
|---|---|---|
| Modelo | `Notification` (026) | `EmailRecipientList` / `EmailRecipient` |
| Destinatario | usuario autenticado | una direccion de correo |
| Requiere cuenta | si | no |
| Se lee | cuando entra al portal | en su bandeja |
| Baja | marcar leido / borrar | enlace de baja |

Van juntos en este documento porque **comparten la decision de consentimiento**
(quien pidio que le escriban y como lo demuestra), no porque compartan modelo.
Se pueden implementar como dos PRs encadenados; §9 propone el orden.

## 3. Lo que ya existe y no hay que reinventar

| Pieza | Donde | Estado |
|---|---|---|
| `Notification` con dedup, severidad, url, expiracion | `apps/core/models.py` (026) | hecho |
| `notify()` / `notify_superusers()` | `apps/core/notifications.py` | hecho, audiencia fija |
| `Profile.newsletter` + signal de sincronizacion | `apps/user_auth/signals.py` | hecho, con defectos (§4) |
| `EmailRecipientList` / `EmailRecipient` | `apps/core/models.py:279-321` | hecho, esquema rigido (§4.1) |
| `mail_send` sobre `send_email_task` con reintentos y aviso al admin | `apps/core/utils.py:116` | hecho |
| Formulario de registro con casilla de novedades | `apps/user_auth/forms/users.py:142` | hecho |
| Icono de avisos en el navbar | 026 fase 3 | **pendiente** |

## 4. Hallazgos que condicionan el diseno

Estos no son opiniones: son consecuencias verificables del codigo actual.

### 4.1 `EmailRecipient.email` es unico **global**

`apps/core/models.py:301` declara `email = models.EmailField(unique=True)`. Una
direccion solo puede pertenecer a **una** lista. Y el signal del boletin usa:

```python
EmailRecipient.objects.get_or_create(
    email=email,
    defaults={'recipient_list': newsletter_list},  # apps/user_auth/signals.py:18
)
```

Consecuencia real: si `juan@ejemplo.cu` ya esta en la lista de "avisos", Juan se
registra, marca "recibir novedades", el `get_or_create` encuentra su fila (la de
avisos), **no lo agrega al boletin**, y `Profile.newsletter` queda en `True`
mintiendo. No recibe el boletin y nada lo indica. El admin tampoco puede
arreglarlo desde la UI: `EmailRecipientForm.clean_email`
(`apps/core/forms/email_recipients.py:43`) rechaza el duplicado global.

Hay que decidir el esquema: `unique_together ('email', 'recipient_list')` con
migracion de datos, o separar `Subscriber` (persona + consentimiento) de la
pertenencia a listas. La segunda es mas limpia y es la que recomiendo, porque el
consentimiento pertenece a la persona, no a la lista.

### 4.2 La lista del boletin se identifica **por nombre**

`EmailRecipientList.objects.get(name='newsletter')` (`signals.py:11`). Renombrar
esa lista desde la pantalla de listas rompe la sincronizacion **en silencio**:
el `except DoesNotExist: return` se traga el error. Hace falta una marca estable
(un booleano `is_newsletter` o una entrada en settings con el pk).

### 4.3 Cambiar el correo duplica la suscripcion

`ProfileForm.save` escribe `user.email` y despues guarda el perfil
(`apps/user_auth/forms/profile.py:143-153`), lo que dispara el signal con la
direccion **nueva**: crea una fila nueva y **no borra la vieja**. Quien cambia de
correo sigue recibiendo el boletin en la direccion anterior, que puede ser un
buzon abandonado o de otra persona. Sin endpoint de baja, no hay forma de
cortarlo.

### 4.4 El rate limiting no cubre una vista Django

`DEFAULT_THROTTLE_RATES` (`config/settings/base.py:412-422`) es de DRF. Un
endpoint publico de suscripcion escrito como vista Django no tiene freno: sirve
para llenar la lista de basura y, si dispara correo, para usar el SMTP del Centro
contra terceros. Necesita limite por IP en cache, y el doble opt-in lo vuelve
mucho menos atractivo.

### 4.5 No hay consentimiento, ni confirmacion, ni baja

`EmailRecipient` no tiene fecha de alta, origen ni token. Ningun correo lleva
enlace de baja (`templates/emails/` no tiene ninguno). Con una suscripcion
publica sin doble opt-in, cualquiera inscribe a cualquiera: el boton del landing
se convierte en una herramienta de spam con el dominio del Centro como remitente.

### 4.6 Suscriptor sin cuenta

Quien se suscribe desde el landing no tiene `User`, asi que no puede tener aviso
en el panel: solo correo. Esto obliga a que las dos mitades (§2) tengan
destinatarios distintos y a no prometer en la UI algo que no se puede cumplir.

## 5. Solucion propuesta

### 5.1 Boletin y suscripcion publica (correo)

1. **Persona separada de la pertenencia.** Modelo de suscriptor con `email`,
   `created_at`, `source` (`landing` / `registro` / `perfil` / `admin`),
   `confirmed_at`, token de confirmacion y token de baja. Pertenencia a listas
   aparte, con unicidad por `(suscriptor, lista)`.
2. **Doble opt-in para el landing.** El POST crea la suscripcion **pendiente** y
   manda un correo de confirmacion. Sin confirmar no recibe nada. El enlace es de
   un solo uso y expira.
3. **Un solo camino de consentimiento.** El signal del perfil deja de escribir
   filas a mano y usa el mismo servicio que el landing: marcar la casilla en el
   perfil es un consentimiento con `source='perfil'`.
4. **Baja en todos los correos masivos.** Enlace firmado, sin login, que
   desactiva la suscripcion y responde una pagina propia (no un 404).
5. **Destinatarios resueltos por politica, no a mano.** `mail_send` deja de
   depender de una FK elegida por el operador: resuelve la audiencia desde una
   regla explicita (por ejemplo "quien acepto avisos + quien acepto el boletin
   segun el tipo de aviso"), y si la resolucion da vacio **lo registra y avisa al
   admin** (la infraestructura de 026 ya existe) en vez de retornar en silencio.
6. **Envio del boletin**: comando de gestion y/o pantalla, con vista previa y
   confirmacion. Un boton que manda a 500 personas sin confirmar es un incidente
   esperando.

### 5.2 Notificaciones en el panel para clientes (in-app)

1. **Politica de audiencia explicita.** `notify_superusers` pasa a ser un caso
   de una capa de politica: cada `source` declara a quien puede llegar.
   **Regla dura:** una fuente interna (fallo de tarea, error de PDF) nunca puede
   alcanzar a un no-superusuario. Se aplica en el codigo, no en la plantilla.
2. **Fuentes para clientes** (a decidir en §6.5): factura disponible, cambio de
   estado de su suscripcion, respuesta a su solicitud.
3. **Preferencias por usuario** para las fuentes de cliente. En 026 se
   descartaron a proposito porque el conjunto era fijo y chico (dos admins); ese
   argumento **no aplica** a clientes.
4. **UI segun rol** sobre el icono de la fase 3 de 026: el mismo componente, con
   la lista filtrada por `recipient`, y sin acceso a las rutas de administracion.
5. **Dependencia:** esta mitad necesita la fase 3 de 026 (icono, desplegable y
   pagina de historial) ya en `dev`. No se puede adelantar.

## 6. Decisiones abiertas (bloquean el diseno)

1. **¿Doble opt-in para el landing?** Recomiendo que si. Sin el, el formulario
   sirve para inscribir direcciones ajenas y el remitente es el dominio del
   Centro. Costo: un modelo de token y un correo mas.
2. **¿Esquema de suscriptores?** ¿`Subscriber` + pertenencia, o
   `unique_together (email, list)` sobre `EmailRecipient`? La primera soporta
   consentimiento y baja; la segunda es menos migracion pero deja el
   consentimiento pegado a la lista.
3. **¿Que pasa con las filas de `EmailRecipient` que ya existen** al migrar? Hay
   que decidir si se asume que estan confirmadas (vienen de carga manual del
   admin) o si quedan pendientes.
4. **¿Quien envia el boletin, y como?** Comando, pantalla con vista previa, o
   ambos. ¿Un envio manual por edicion, o automatico cuando se publica un aviso?
5. **¿Que eventos generan un aviso in-app para un cliente?** Propuesta a
   discutir: factura emitida, cambio de estado de suscripcion. Lo que **no**
   deberia estar: nada interno.
6. **¿El cliente puede apagar los avisos in-app?** Y si los apaga, ¿el correo de
   lo mismo tambien se apaga? Son dos canales y la gente espera que sean dos
   interruptores.
7. **¿Que pasa con un suscriptor sin cuenta que pide la baja?** ¿Se borra la
   fila o se marca `active=False`? Borrar impide saber a quien ya se le escribio;
   marcar conserva el historial y permite no volver a importunarlo.
8. **Limites de envio.** Tamano maximo por lote, ventana horaria y que hacer si
   el SMTP rechaza. El `send_email_task` ya reintenta; falta decidir el ritmo.
9. **¿El boletin es una entidad propia** (asunto, cuerpo, estado borrador /
   enviado) o se reutiliza la plantilla de avisos? Sin entidad no hay historial
   de que se mando y cuando.

## 7. Criterios de aceptacion (borrador)

- Escribir un correo en el landing y pulsar "Suscribirse" crea una suscripcion
  **pendiente** y envia un correo de confirmacion; sin confirmar no recibe
  ningun envio.
- El enlace de confirmacion es de un solo uso y caduca.
- Un correo con el mismo formato falla visiblemente si el campo esta vacio
  (validacion de servidor, no solo del navegador).
- Cada correo masivo lleva un enlace de baja que funciona sin login y corta los
  envios siguientes.
- `Profile.newsletter = False` deja de recibir el boletin en el envio siguiente.
- Cambiar el correo del perfil **mueve** la suscripcion: no queda una fila con
  la direccion anterior.
- Un correo que ya esta en otra lista puede estar tambien en el boletin
  (se acaba el `newsletter=True` que miente).
- `mail_send` sin destinatarios deja rastro (log + aviso al admin), no un
  `return` mudo.
- Un cliente con cuenta ve sus avisos y **ninguna** fuente interna.
- Un no-superusuario no accede a las pantallas de administracion de listas ni al
  historial de otro usuario.
- Pruebas: opt-in, confirmacion, baja, duplicados entre listas, cambio de correo,
  throttling del endpoint publico, aislamiento de audiencias, y que las rutas de
  administracion siguen exigiendo permiso.

## 8. Fuera de alcance

- Enviar por correo las notificaciones internas del admin (026 lo dejo fuera con
  su propio control de duplicados).
- Preferencias granulares por fuente para el admin.
- El "Boletín aerológico" de `templates/includes/home/menu-list.html:118`: es un
  producto de datos del sitio, no un correo. Sigue deshabilitado.
- Analitica de apertura y clics (pixeles, redirecciones firmadas).
- Marketing segmentado. Esto es un boletin institucional.

## 9. Tamano y orden sugerido

Dos PRs encadenados, no uno:

1. **PR A — boletin y suscripcion (correo).** Modelo de suscriptor, doble opt-in,
   baja, resolucion automatica de destinatarios, arreglo de §4.1 a §4.3, landing
   operativo. Es el que resuelve lo que hoy esta roto.
2. **PR B — avisos in-app para clientes.** Politica de audiencia, fuentes de
   cliente, preferencias, UI segun rol. **Depende de la fase 3 de 026.**

Orden: A primero. B no puede empezar hasta que exista el icono de 026, y A no
depende de nada.
