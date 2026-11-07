---
name: django-expert
description: "Trigger: Django, DRF, ORM, modelos, queries, auth, tests, performance Django. Guía experta de desarrollo Django/DRF con las convenciones de este proyecto."
license: Apache-2.0
metadata:
  author: "yoelvismr"
  version: "1.1"
---

# Skill: django-expert

## Activation Contract

Cargar al escribir o revisar modelos, vistas, serializers, queries, autenticación, tests o problemas de performance Django/DRF en `apps/`. Triggers típicos: "create a Django model", "optimize this queryset", "implement DRF serializer/viewset", "fix N+1 query problem", "write tests for this app", "this view is slow".

## Project Hard Rules

- Apps viven en `apps/`; importar siempre `from apps.<app>.models import ...`.
- Modelos de negocio: `default_permissions = ()` + permisos custom `view_*`, `add_*`, `change_*`, `delete_*` en español.
- `FileHandlerMixin` + `file_fields` obligatorio en todo modelo con FileField/ImageField.
- Soft delete: usar el manager por defecto (filtra `record_active=True`), nunca `all_objects`.
- URLs de modelos con UUIDField usan kwarg `uuid` (nunca `pk`).
- Vistas con DataTables cargan todos los registros (pagina el cliente); el resto usa `paginate_by = 20`.
- DRF: `DjangoModelPermissionsOrAnonReadOnly`; schema via drf-spectacular.
- Tests: label completo `apps.<app>` (Django 5.2 no resuelve labels cortos).
- Migraciones: versionadas; generar y commitear el archivo en el mismo PR.

## Workflow

### 1. Analyze the Request and Gather Context

Identificar el tipo de tarea: diseño de modelo, vista/API (FBV, CBV, DRF viewsets), optimización de queries, auth/permisos, tests, seguridad (CSRF, XSS, SQL injection), performance, templates. Leer el código existente relevante y el AGENTS.md para respetar convenciones. Verificar versión de Django para compatibilidad (proyecto: Django 5.2).

### 2. Apply Django Best Practices

**Patrones Django:**
- `select_related()` para FK/OneToOne, `prefetch_related()` para reverse FK/M2M.
- Views finas: lógica de negocio en services/managers, no en la vista.
- Class-based views y mixins para reuso; formularios/serializers para validación.
- Nunca editar migraciones ya aplicadas.
- Usar features de seguridad integradas (CSRF tokens, decoradores de auth).

**DRF:**
- `ModelSerializer` para CRUD estándar; paginación y filtrado correctos.
- Clases de permiso apropiadas; convenciones RESTful; versionar APIs en breaking changes.

### 3. Validate and Test

Antes de presentar la solución:
- Verificar N+1 (usar `connection.queries` en test si hace falta).
- Manejo de errores y edge cases; seguridad.
- Migraciones limpias y reversibles.
- Test para la funcionalidad nueva si no existe, con label completo `apps.<app>`.
- Verificar: `python manage.py check && python manage.py test apps.<app>`.

## Decision Gates

| Situación | Acción |
|---|---|
| Query N+1 o lento | `select_related`/`prefetch_related`; verificar con `connection.queries` en test |
| Borrado lógico | `record_active=True` en manager por defecto |
| Archivos subidos | `FileHandlerMixin` con `file_fields` |
| Migraciones | Generar y commitear; gate `makemigrations --check --dry-run` |

## Common Pitfalls

- Imports circulares (usar referencias lazy).
- Falta de `related_name` en relaciones.
- Olvidar índices en campos consultados frecuentemente.
- `get()` sin manejo de excepciones.
- N+1 en templates y serializers.
- Asumir permisos por defecto (no existen: usar los 4 custom en español).

## Execution Steps

1. Leer el modelo y sus relaciones antes de tocar queries.
2. Aplicar las convenciones del proyecto (permisos, mixins, UUID).
3. Escribir/ajustar tests con el label completo.
4. Verificar: `python manage.py check && python manage.py test apps.<app>`.

## Output Contract

- Código que respeta las convenciones del proyecto.
- Test del cambio si no existía.
- Comando de verificación ejecutado y su resultado.

## References

- `../../AGENTS.md` — convenciones del proyecto (apps, permisos, URLs, tests).
