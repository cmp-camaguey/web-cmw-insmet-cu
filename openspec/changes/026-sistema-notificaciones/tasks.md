# 026-sistema-notificaciones — tareas

> **Aprobada el 2026-11-06.** Las cuatro preguntas de `design.md` §10 estan
> cerradas (fase 0) y el alcance bajo a **cuatro** fuentes: la de despliegue se
> descarto. Las cuatro fases estan implementadas: fases 1 y 2 en `dev` desde el
> PR #147, fases 3 y 4 en el PR siguiente.

## Fase 0 — Decisiones

- [x] Motor de produccion: **PostgreSQL** en los tres perfiles (028). El dedup es
      el indice unico parcial; la rama MySQL del diseño no aplica.
- [x] Fuente del aviso de despliegue: **opcion C**, no se notifica. El widget de
      025 ya muestra el estado y la opcion A abre una escritura `deploy` → `webcmp`.
- [x] Destinatarios: **todos los superusuarios** (el repo tiene dos admins). Sin
      `NotificationPreference` mientras sea un conjunto fijo y chico.
- [x] Aviso de `Warning` por vencer: **solo el panel**. Es un recordatorio
      operativo para quien administra el portal.

## Fase 1 — Nucleo

- [x] Modelo `Notification` en `apps/core` con `uuid`, `default_permissions = ()` y los cuatro permisos custom.
- [x] Indice unico parcial `(recipient, dedupe_key)` sobre no leidas; indices de contador, lista y poda.
- [x] Helper `make_dedupe_key(*parts)` y `notify(recipient, source, severity, title, body, url, dedupe_key)` con `get_or_create` protegido.
- [x] Migracion (no versionada, `.gitignore`).
- [x] Pruebas 1, 2 y 6 de `design.md` §9.

## Fase 2 — Fuentes

- [x] Aviso desde `TaskExecutionLog` en `ERROR`, con allowlist de tareas.
- [x] Aviso de `Invoice.pdf_status == 'failed'`.
- [x] Aviso de `Invoice.email_status == 'failed'`.
- [x] Aviso de despliegue — **descartado** (opcion C): el widget de 025 ya muestra
      el estado y no se abre una ruta de escritura `deploy` → `webcmp`.
- [x] Aviso de `Warning.valid_until` proximo, con tarea Huey diaria.
- [x] Prueba 7 de `design.md` §9: una tarea de factura no duplica su aviso.

## Fase 3 — UI

- [x] Icono con badge en el navbar, junto al de actualizacion de 025, respetando `gap-2`.
- [x] Desplegable con las 10 mas recientes, "Ver todas" y "Marcar todas como leidas".
- [x] Pagina de historial con `paginate_by = 10`, sin DataTables.
- [x] Contador server-rendered de entrada y refresco por endpoint propio
      (`notifications:unread`) con `ETag`/`304` y `notifications-badge.js`. **No**
      viaja en el polling de `dashboard-update.js`: ese JSON lee archivos y
      consulta el remoto, y el contador es un `COUNT` indexado que el portal
      público consulta con mucha más frecuencia (PR #152).
- [x] Marcado de lectura individual y masivo, con su prueba de permisos. Desde el
      cambio 030 la bandeja pide solo sesión y el aislamiento lo da
      `recipient=request.user`: un cliente marca lo suyo y recibe 404 en el aviso
      ajeno.

## Fase 4 — Operacion

- [x] `notificar_podar` idempotente con las dos ventanas de retencion.
- [x] `periodic_task` de Huey diaria para la poda.
- [x] Ajustes `NOTIFICATIONS_*` en `config/settings/base.py` con sus valores por defecto.
- [x] `NOTIFICATIONS_ENABLED=False` verificado: cero filas nuevas, icono oculto, sin errores.
- [x] Documentar el flag y la poda en `deploy/README-deploy.md`.

## Fuera de alcance

- Envio por correo de los avisos. Va aparte, con su propio control de duplicados.
- Preferencias por usuario (solo si fase 0 confirma varios administradores).
- Tiempo real. Descartado por WSGI en 025 y sin motivo nuevo.
- Reutilizar el `threading.Thread` de SquidStats. Un monitor por worker de Gunicorn.
