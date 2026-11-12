# 032-horario-y-region — propuesta

> **Estado: aprobada el 2026-11-09.** El usuario pidió una nueva sección de
> configuración en el dashboard para el horario y la región: zona horaria
> automática o manual (horas sumadas/restadas al UTC) y formato de hora de 24 o
> 12 horas con AM/PM.

## 1. Motivación

Hoy la zona horaria vive fija en `config/settings/base.py`
(`TIME_ZONE = 'America/Havana'`) y el formato de hora está repartido a mano en
cada plantilla: unas muestran `H:i` (24 h) y otras `h:i A` (12 h). Cambiar el
horario del portal obliga a tocar código y desplegar, y el formato no es
consistente entre el panel y el sitio público.

El Centro necesita ajustar el horario local desde el propio dashboard, sin
depender de un despliegue, y unificar cómo se muestran las horas.

## 2. Solución

Una nueva sección **Configuración → Horario y región** sobre el singleton
`SiteConfiguration`:

- **Zona horaria**
  - `Automático`: usa la zona configurada en el servidor (`settings.TIME_ZONE`,
    hoy `America/Havana`, con horario de verano).
  - `Manual`: el administrador elige cuántas horas se suman o restan al UTC
    (de −12 a +14).
- **Formato de hora**: 24 horas (`14:30`) o 12 horas con AM/PM (`02:30 PM`).

La configuración se aplica por request con un middleware que activa la zona
horaria de Django y un conjunto de filtros de plantilla que respetan el formato
elegido. Así la hora local y el formato cambian en todo el portal y en el panel.

## 3. Alcance

Dentro:

- Campos nuevos en `SiteConfiguration` y su migración.
- Vista, formulario, URL, plantilla y entrada de menú de la sección.
- Middleware de zona horaria por request.
- Filtros de plantilla para mostrar fecha/hora con el formato configurado.
- Pruebas de modelo, formulario, vista, middleware y filtros.

Fuera:

- Configurar la zona horaria del proceso de Huey y de los PDFs encolados. Esos
  procesos siguen usando `settings.TIME_ZONE`; se puede abordar en un cambio
  aparte si hace falta.
- Selector de idioma o de región geográfica (el nombre "región" de la sección
  cubre la zona horaria, no un catálogo de países).
- Cambiar el formato de los datepickers de Tempus Dominus.

## 4. Criterios de aceptación

1. La sección aparece en el menú **Configuración** para quien tenga
   `core.change_siteconfiguration`.
2. Se puede elegir `Automático` o `Manual`; en manual se elige una diferencia
   con UTC entre −12 y +14.
3. Se puede elegir formato de 24 o 12 horas con AM/PM.
4. Al guardar, las horas del portal y del panel respetan la zona y el formato
   configurados.
5. La configuración sobrevive al reinicio y no requiere desplegar.
