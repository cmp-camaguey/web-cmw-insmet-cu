# 026-sistema-notificaciones — diseno tecnico

> Borrador de diseno para la propuesta `026`. Nada de esto esta implementado.
> Las decisiones marcadas **[abierta]** requieren una respuesta antes de escribir
> codigo.

## 1. Modelo

App: `apps/core` — junto a `TaskExecutionLog` y `ActivityLog`, porque es
transversal y no pertenece a `commercial`, `meteo` ni `dashboard`.

```python
class Notification(models.Model):
    class Severity(models.TextChoices):
        INFO = 'info', 'Informacion'
        WARNING = 'warning', 'Advertencia'
        ERROR = 'error', 'Error'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notifications'
    )
    severity = models.CharField(max_length=10, choices=Severity.choices)
    source = models.CharField(max_length=20)          # 'huey' | 'invoice' | 'deploy' | 'warning'
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True)  # ruta relativa, no absoluta
    dedupe_key = models.CharField(max_length=64)        # sha256 hex
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
```

`default_permissions = ()` + los cuatro permisos custom, como el resto del
proyecto. `uuid` como identificador expuesto, nunca `pk`.

### Indices

```python
constraints = [
    # Un aviso no leido por (destinatario, clave) y nada mas.
    models.UniqueConstraint(
        fields=['recipient', 'dedupe_key'],
        condition=Q(read_at__isnull=True),
        name='uniq_notification_unread_dedupe',
    ),
]
indexes = [
    models.Index(fields=['recipient', 'read_at']),          # contador
    models.Index(fields=['recipient', '-created_at']),      # lista
    models.Index(fields=['expires_at']),                    # poda
]
```

El indice unico **parcial** es lo que hace que el dedup sea una garantia de la
base y no una condicion de carrera en Python. PostgreSQL y SQLite lo soportan.
MySQL **no** soporta indices parciales: si algun dia se despliega sobre MySQL, el
dedup baja a `get_or_create` dentro de `transaction.atomic()` con un `SELECT ...
FOR UPDATE` **[abierta: confirmar motor de produccion]**.

## 2. Dedup

```python
def make_dedupe_key(*parts) -> str:
    raw = '|'.join(str(p) for p in parts)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
```

Cada fuente define sus partes. La regla comun: **lo que identifica el hecho, no
el momento**.

| Fuente | Partes de la clave |
|---|---|
| Tarea Huey | `huey`, `logical_key` (o `task_id` si no hay), `ERROR` |
| PDF de factura | `invoice`, `uuid` de la factura, `pdf` |
| Correo de factura | `invoice`, `uuid` de la factura, `email` |
| Despliegue | `deploy`, `sha` desplegado, estado final |
| Aviso por vencer | `warning`, `uuid` del aviso |

Sin el `created_at` en la clave, tres reintentos de la misma tarea producen una
fila. Si el admin la lee y el fallo se repite, el indice parcial ya no aplica y
aparece una fila nueva: eso es lo deseado, porque un fallo repetido despues de
haberlo visto **es** informacion nueva.

## 3. Creacion: explicita, no por señal

Un `post_save` sobre `Invoice` que mire `pdf_status` y otro sobre
`TaskExecutionLog` se pisarian: el PDF fallido de una factura tambien deja una
tarea en `ERROR`, y el admin veria dos avisos del mismo hecho.

Regla: **la notificacion se crea en el borde que conoce el hecho**, no por señal
generica.

| Hecho | Donde se crea |
|---|---|
| Tarea Huey falla | En el receiver `on_error` de `CoreConfig.register_task_signals()`, con una **allowlist** de `task_name`. Las tareas de factura quedan fuera porque tienen su propio aviso mas especifico. |
| PDF falla | En la tarea que genera el PDF, rama `FAILED`. |
| Correo falla | En la tarea que envia el correo, rama `FAILED`. |
| Despliegue termina | `deploy/autodeploy.sh` invoca `python manage.py notificar_despliegue` al cerrar **[abierta: ver 3.1]**. |
| Aviso por vencer | Tarea Huey periodica diaria. |

### 3.1 El aviso de despliegue

`autodeploy.sh` corre como el usuario `deploy`; el proceso Django corre como
`webcmp`. Escribir en la base desde `deploy` funciona (PostgreSQL, no SQLite),
pero **abre una ruta de escritura que hoy no existe** y hay que decidirla a
consciencia:

- **Opcion A (preferida):** el unit termina con
  `sudo -u webcmp /opt/webcmp/venv/bin/python manage.py notificar_despliegue
  --status-file /var/lib/webcmp/deploy-status.json`. Requiere `deploy` pueda
  hacer `sudo -u webcmp` sin password, o que el comando viva detras de un
  `systemd` `.service` de un solo disparo.
- **Opcion B (menos infraestructura):** el navbar detecta el cambio de `running`
  a `success`/`failed` en `UpdateStatusView` y crea la fila la primera vez que
  alguien mira. **La desventaja es real:** si nadie abre el panel, el aviso no
  existe. Se acepta como degradacion consciente, no como diseno.
- **Opcion C:** no notificar despliegues (ya los cubre el propio widget de 025,
  que cambia de estado solo). La mas honesta si A resulta costosa.

**Decision pendiente.** Sin resolverla, la fuente 4 no se implementa.

## 4. Destinatarios

Fase 1: superusuarios. Es un conjunto que ya existe, ya esta autorizado a ver
todo y no requiere una pantalla de preferencias.

Fase 2 **[abierta]**: suscripcion por tipo. Antes de eso hay que responder si
esto lo va a usar **una** persona (el admin del CMP) o **varias**. Con una sola,
cualquier modelo de preferencias es complejidad sin uso. Con varias, hace falta
una relacion `NotificationPreference(user, source, enabled)`.

## 5. UI

### 5.1 Badge y lista

Icono en el navbar del dashboard, hermano del icono de actualizacion que ya vive
en `templates/includes/dashboard/update_navbar.html`. Mismas reglas de espaciado
(`gap-2` en el `navbar-nav`), mismo lenguaje visual de Tabler.

- Sin avisos: icono normal, sin badge.
- Con avisos: badge con el numero, tope visual `9+`.
- Desplegable: las 10 mas recientes, cada una con titulo, `hace X`, severidad por
  color y enlace al objeto original. Pie del desplegable: "Ver todas" y "Marcar
  todas como leidas".
- Pagina completa en `apps/dashboard/` para historial con paginacion
  (`paginate_by = 10`, convencion del proyecto; **sin** DataTables, que solo se
  usa cuando se cargan todos los registros).

### 5.2 Contador

El navbar se renderiza en servidor con el `COUNT` ya hecho (una consulta
indexada por pagina) y el polling de `dashboard-update.js` lo refresca. No se
crea un endpoint nuevo ni un segundo `setInterval`.

## 6. Poda

Management command `notificar_podar`, idempotente:

```python
read_cutoff = now - timedelta(days=settings.NOTIFICATIONS_RETENTION_READ)    # 30
unread_cutoff = now - timedelta(days=settings.NOTIFICATIONS_RETENTION_UNREAD) # 90
```

- Borra leidas con `read_at < read_cutoff`.
- Borra no leidas con `created_at < unread_cutoff` (un aviso de hace 90 dias que
  nadie leyo ya no es accionable; dejarlo solo infla el badge).
- Borra cualquier fila con `expires_at < now`.
- Devuelve el conteo por categoria para que el log diga que hizo.

Programarla con Huey (`periodic_task`) una vez al dia, **no** con un
`threading.Timer`. Es la misma razon por la que se descarto el monitor de
SquidStats: un proceso web con N workers no es el lugar para un reloj.

## 7. Integracion con lo que ya existe

- `TaskExecutionLog` no se modifica. La notificacion enlaza a su vista.
- `Invoice` gana **cero campos**. `pdf_status`, `email_status` y sus errores ya
  estan.
- `Warning` no se toca. La consulta es `valid_until` en la ventana.
- El widget de 025 (`update_navbar.html`, `dashboard-update.js`) se reutiliza
  para el transporte del contador; no se duplica el polling.
- El correo existente (`mail_send`, `EmailRecipientList`) **no** se toca. Si mas
  adelante se quiere enviar avisos por correo, es una fase aparte con su propio
  control de duplicados: enviar dos veces un correo de "factura fallida" es peor
  que no enviarlo.

## 8. Configuracion

```python
NOTIFICATIONS_ENABLED = env.bool('NOTIFICATIONS_ENABLED', default=True)
NOTIFICATIONS_RETENTION_READ = 30     # dias
NOTIFICATIONS_RETENTION_UNREAD = 90   # dias
NOTIFICATIONS_MAX_VISIBLE = 10        # entradas del desplegable
```

Apagado: no se crean filas, no se renderiza el icono, la poda sigue corriendo
(limpiar datos viejos no es una funcion visible que haya que apagar).

## 9. Pruebas previstas

1. Dedup: dos llamadas con la misma clave dejan una fila.
2. Reapertura: marcar leida y repetir el hecho crea una fila nueva.
3. Contador: coincide con `filter(recipient=user, read_at__isnull=True).count()`.
4. Permisos: un no-superusuario no ve el icono ni accede a la lista (403).
5. Poda: borra leidas viejas, no borra no leidas recientes, es idempotente.
6. Flag apagado: no se crea ninguna fila.
7. `TaskExecutionLog` en `ERROR` de una tarea en la allowlist crea un aviso; de
   una tarea de factura **no** (la cubre su fuente especifica).
8. El aviso no incluye el `traceback` completo; el enlace apunta a la vista del
   log.

## 10. Preguntas abiertas

1. **Motor de produccion final** — PostgreSQL o MySQL. Cambia el mecanismo de
   dedup (indice parcial vs transaccion).
2. **Fuente de despliegue** — opcion A, B o C de la seccion 3.1.
3. **Un usuario o varios** — decide si hace falta `NotificationPreference`.
4. **Avisos meteorologicos** — ¿es un aviso para el admin o algo que deberia ver
   el portal publico? Si es lo segundo, esto no es una notificacion, es un
   componente de la portada.
