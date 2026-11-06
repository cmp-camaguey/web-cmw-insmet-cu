# 026-sistema-notificaciones — tareas

> **Aprobada el 2026-11-06.** Las cuatro preguntas de `design.md` §10 estan
> cerradas (fase 0) y el alcance bajo a **cuatro** fuentes: la de despliegue se
> descarto. Todavia sin implementar — la rama se abre cuando se retome.

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

- [ ] Modelo `Notification` en `apps/core` con `uuid`, `default_permissions = ()` y los cuatro permisos custom.
- [ ] Indice unico parcial `(recipient, dedupe_key)` sobre no leidas; indices de contador, lista y poda.
- [ ] Helper `make_dedupe_key(*parts)` y `notify(recipient, source, severity, title, body, url, dedupe_key)` con `get_or_create` protegido.
- [ ] Migracion (no versionada, `.gitignore`).
- [ ] Pruebas 1, 2 y 6 de `design.md` §9.

## Fase 2 — Fuentes

- [ ] Aviso desde `TaskExecutionLog` en `ERROR`, con allowlist de tareas.
- [ ] Aviso de `Invoice.pdf_status == 'failed'`.
- [ ] Aviso de `Invoice.email_status == 'failed'`.
- [x] Aviso de despliegue — **descartado** (opcion C): el widget de 025 ya muestra
      el estado y no se abre una ruta de escritura `deploy` → `webcmp`.
- [ ] Aviso de `Warning.valid_until` proximo, con tarea Huey diaria.
- [ ] Prueba 7 de `design.md` §9: una tarea de factura no duplica su aviso.

## Fase 3 — UI

- [ ] Icono con badge en el navbar, junto al de actualizacion de 025, respetando `gap-2`.
- [ ] Desplegable con las 10 mas recientes, "Ver todas" y "Marcar todas como leidas".
- [ ] Pagina de historial con `paginate_by = 10`, sin DataTables.
- [ ] Contador server-rendered de entrada y refresco en el polling existente de `dashboard-update.js`.
- [ ] Marcado de lectura individual y masivo, con su prueba de permisos (403 a no-superusuario).

## Fase 4 — Operacion

- [ ] `notificar_podar` idempotente con las dos ventanas de retencion.
- [ ] `periodic_task` de Huey diaria para la poda.
- [ ] Ajustes `NOTIFICATIONS_*` en `config/settings/base.py` con sus valores por defecto.
- [ ] `NOTIFICATIONS_ENABLED=False` verificado: cero filas nuevas, icono oculto, sin errores.
- [ ] Documentar el flag y la poda en `deploy/README-deploy.md`.

## Fuera de alcance

- Envio por correo de los avisos. Va aparte, con su propio control de duplicados.
- Preferencias por usuario (solo si fase 0 confirma varios administradores).
- Tiempo real. Descartado por WSGI en 025 y sin motivo nuevo.
- Reutilizar el `threading.Thread` de SquidStats. Un monitor por worker de Gunicorn.
