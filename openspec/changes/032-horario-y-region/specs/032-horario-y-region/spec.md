# 032-horario-y-region — requisitos

## S1 — Sección de configuración

El dashboard debe exponer una sección **Configuración → Horario y región**
visible para usuarios con `core.change_siteconfiguration`.

- Aceptación: la entrada existe en el menú, la URL responde 200 con permiso y
  403 sin permiso.
- Verificación: pruebas de vista y de plantilla.

## S2 — Modo de zona horaria

La sección debe permitir elegir entre `Automático` y `Manual`.

- `Automático` usa la zona configurada en el servidor (`settings.TIME_ZONE`).
- `Manual` usa una diferencia fija con UTC elegida por el administrador.
- Aceptación: el valor persiste en el singleton y `get_timezone()` devuelve la
  zona correcta en cada modo.

## S3 — Diferencia manual con UTC

En modo manual se debe poder elegir cuántas horas se suman o restan al UTC,
entre −12 y +14.

- Aceptación: el formulario rechaza valores fuera del rango y guarda el valor
  elegido.
- Verificación: prueba de formulario y de modelo.

## S4 — Formato de hora

La sección debe permitir elegir formato de 24 horas (`14:30`) o de 12 horas con
AM/PM (`02:30 PM`).

- Aceptación: el valor persiste y `get_time_format()` devuelve `H:i` o `h:i A`.
- Verificación: prueba de modelo y de filtros.

## S5 — Aplicación en las vistas

Las horas mostradas en el portal y en el panel deben respetar la zona horaria y
el formato configurados, sin cambios manuales por plantilla.

- Aceptación: con `Manual` y offset −3, un datetime almacenado en UTC se muestra
  tres horas antes; con formato 12 se muestra con AM/PM.
- Verificación: prueba de middleware y de filtros de plantilla.

## S6 — Integridad operativa

La zona activa no debe filtrarse entre requests del mismo worker.

- Aceptación: al terminar el request, `timezone.get_current_timezone()` vuelve a
  `settings.TIME_ZONE`.
- Verificación: prueba de middleware.
