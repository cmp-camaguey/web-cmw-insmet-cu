# 032-horario-y-region — diseño técnico

## 1. Modelo

Se extiende el singleton existente `apps.core.models.SiteConfiguration`; no se
crea otro modelo. La configuración del sitio ya es el lugar de los ajustes
globales (tema, mantenimiento, marca).

```python
class TimeZoneMode(models.TextChoices):
    AUTO = 'auto', 'Automático'
    MANUAL = 'manual', 'Manual'


class TimeFormat(models.TextChoices):
    H24 = '24', '24 horas'
    H12 = '12', '12 horas (AM/PM)'


UTC_OFFSET_CHOICES = [(hour, f'UTC{hour:+d}') for hour in range(-12, 15)]

timezone_mode = models.CharField(
    max_length=10,
    choices=TimeZoneMode.choices,
    default=TimeZoneMode.AUTO,
    verbose_name='Modo de zona horaria',
)
utc_offset_hours = models.SmallIntegerField(
    choices=UTC_OFFSET_CHOICES,
    default=-5,
    verbose_name='Diferencia con UTC (horas)',
)
time_format = models.CharField(
    max_length=2,
    choices=TimeFormat.choices,
    default=TimeFormat.H24,
    verbose_name='Formato de hora',
)
```

Métodos del modelo:

```python
def get_timezone(self):
    """tzinfo efectiva: la del servidor o un offset fijo respecto a UTC."""
    if self.timezone_mode == self.TimeZoneMode.MANUAL:
        return datetime.timezone(datetime.timedelta(hours=self.utc_offset_hours))
    return settings.TIME_ZONE


def get_time_format(self):
    """Patrón de strftime/Django para la hora según el formato elegido."""
    return 'h:i A' if self.time_format == self.TimeFormat.H12 else 'H:i'
```

`default=-5` es la hora estándar de Cuba; solo se usa cuando el modo es manual.
En modo automático el offset es irrelevante y la interfaz lo deshabilita.

## 2. Middleware

`apps.core.middleware.SiteTimezoneMiddleware`, colocado **después** de
`MaintenanceModeMiddleware` para reutilizar el `request.site_config` que ese
middleware ya consulta (una sola lectura del singleton por request).

```python
class SiteTimezoneMiddleware:
    def __call__(self, request):
        site_config = getattr(request, 'site_config', None)
        if site_config is not None:
            timezone.activate(site_config.get_timezone())
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
```

`timezone.activate` es thread-local en Django, así que cada request de cada
worker ve su propia zona y el `finally` evita que el valor se filtre al
siguiente request del mismo hilo. Con `Automático` se activa
`settings.TIME_ZONE` (string con DST); con `Manual`, un `datetime.timezone` de
offset fijo.

Todo lo que ya usa `timezone.localtime()` o el filtro `date` de Django
(plantillas, `timezone.localdate()`) pasa a reflejar la zona configurada sin
tocarlo.

## 3. Formato de 12/24 horas

El filtro `date` recibe formatos explícitos en las plantillas, así que la zona
se aplica sola pero el formato no. Se agregan tres filtros con contexto en
`apps.core.templatetags.form_filters`:

- `local_time`: hora sola con el formato configurado.
- `local_datetime`: fecha + hora con el formato configurado.
- `local_datetime_seconds`: como el anterior, con segundos (tabla de tareas).

Leen `site_branding` del contexto (lo publica el context processor existente),
localizan el valor con `timezone.localtime` cuando es un datetime aware y
formatean con `django.utils.formats.date_format`. Si no hay configuración en el
contexto caen a `H:i` (24 h), que es el valor por defecto.

Las plantillas que muestran hora pasan de `|date:'d/m/Y H:i'` / `|date:'d/m Y
h:i A'` / `|time:'h:i A'` a los filtros nuevos. Las plantillas que solo muestran
fecha se dejan con `|date:` (la zona horaria ya las afecta).

El formato elegido aplica a la **visualización**. Los formularios y Tempus
Dominus siguen usando 12 h con AM/PM porque su contrato de parseo
(`%I:%M %p`, `canonicalFormat`) está construido así; cambiarlo es un trabajo
aparte.

## 4. Interfaz

- URL `core:timezone_configuration` → `/config/horario-region/`.
- `TimezoneConfigurationForm` (ModelForm) con los tres campos.
- `TimezoneConfigurationUpdateView` (LoginRequired + PermissionRequired,
  `core.change_siteconfiguration`), reutiliza el singleton y registra la acción
  en `ActivityLog`.
- Plantilla `pages/core/site/timezone.html` con el layout `form.html`:
  - Card **Zona horaria**: radios Automático/Manual, selector de offset y texto
    de ayuda. El selector se deshabilita cuando el modo es automático.
  - Card **Formato de hora**: radios 24/12 y una vista previa en vivo.
- Entrada **Horario y región** en el desplegable Configuración del menú.

## 5. Pruebas

- Modelo: `get_timezone()` devuelve `settings.TIME_ZONE` en automático y un
  offset fijo en manual; `get_time_format()` devuelve `H:i` / `h:i A`.
- Formulario: acepta los valores válidos; rechaza modo, offset y formato fuera
  de las opciones.
- Vista: 200 con permiso, 403 sin permiso, POST guarda y redirige, registra la
  actividad.
- Middleware: con manual −3, un datetime aware se renderiza tres horas antes que
  UTC; con automático no cambia respecto de `settings.TIME_ZONE`; al terminar el
  request la zona activa queda desactivada.
- Filtros: 24 h y 12 h con AM/PM, incluido el cambio de día por el offset.
- Menú/plantilla: la entrada y los campos existen para el usuario con permiso.
