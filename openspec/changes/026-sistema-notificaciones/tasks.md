# 026-sistema-notificaciones — tareas

> **Ninguna tarea esta aprobada.** El change existe como idea documentada. Antes
> de marcar la primera casilla hay que cerrar las cuatro preguntas abiertas de
> `design.md` §10. No abrir rama hasta entonces.

## Fase 0 — Decisiones

- [ ] Confirmar motor de base de datos de produccion (define el dedup: indice parcial en PostgreSQL, transaccion en MySQL).
- [ ] Decidir la fuente del aviso de despliegue (opcion A, B o C de `design.md` §3.1).
- [ ] Confirmar si los avisos son para un solo admin o para varios.
- [ ] Decidir si el aviso de `Warning` por vencer es del panel o de la portada publica.

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
- [ ] Aviso de despliegue segun la decision de fase 0.
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
