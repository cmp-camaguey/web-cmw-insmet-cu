import os
import re
import uuid
from uuid import uuid4

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from apps.core.validators import (
    validate_account,
    validate_image_upload,
    validate_nit,
    validate_phones,
    validate_reeup,
)


def pdf_upload_path(instance, filename):
    cls_name = instance.__class__.__name__.lower()
    uid = str(instance.uuid)
    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    return f'pdf/{cls_name}/{uid}_{safe_name}'


def image_upload_path(instance, filename):
    cls_name = instance.__class__.__name__.lower()
    uid = str(instance.uuid)
    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    return f'img/{cls_name}/{uid}_{safe_name}'


class FileHandlerMixin(models.Model):
    file_fields = []

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            for field_name in self.file_fields:
                old_instance = self.__class__.objects.filter(pk=self.pk).first()
                if old_instance:
                    old_file = getattr(old_instance, field_name)
                    new_file = getattr(self, field_name)
                    if old_file and old_file != new_file and os.path.isfile(old_file.path):
                        os.remove(old_file.path)
        super().save(*args, **kwargs)

    def _cleanup_files(self):
        for field_name in self.file_fields:
            file_field = getattr(self, field_name)
            if file_field and os.path.isfile(file_field.path):
                os.remove(file_field.path)

    def delete(self, *args, **kwargs):
        self._cleanup_files()
        super().delete(*args, **kwargs)


class SoftDeleteModel(models.Model):
    record_active = models.BooleanField(default=True, verbose_name='Registro activo')
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='Eliminado en')

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        if hasattr(self, '_cleanup_files'):
            self._cleanup_files()
        self.record_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['record_active', 'deleted_at'])

    def hard_delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)


TIEMPO_CHOICES = [
    ('despejado', 'Despejado'),
    ('mayormente_despejado', 'Mayormente despejado'),
    ('parcialmente_nublado', 'Parcialmente nublado'),
    ('mayormente_nublado', 'Mayormente nublado'),
    ('nublado', 'Nublado'),
    ('lluvias', 'Lluvias'),
    ('lluvias_debiles', 'Lluvias débiles'),
    ('chubascos', 'Chubascos'),
    ('chubascos_electricos', 'Chubascos eléctricos'),
    ('chubascos_lluvias', 'Chubascos y lluvias'),
    ('tormenta', 'Tormenta eléctrica'),
]

VIENTO_DIRECCION_CHOICES = [
    ('N', 'Norte'),
    ('NNE', 'Norte-Noreste'),
    ('NE', 'Noreste'),
    ('ENE', 'Este-Noreste'),
    ('E', 'Este'),
    ('ESE', 'Este-Sureste'),
    ('SE', 'Sureste'),
    ('SSE', 'Sur-Sureste'),
    ('S', 'Sur'),
    ('SSO', 'Sur-Suroeste'),
    ('SO', 'Suroeste'),
    ('OSO', 'Oeste-Suroeste'),
    ('O', 'Oeste'),
    ('ONO', 'Oeste-Noroeste'),
    ('NO', 'Noroeste'),
    ('NNO', 'Norte-Noroeste'),
    ('variable', 'Variable'),
    ('calmado', 'Calmado'),
]

LUNA_CHOICES = [
    ('nueva', 'Luna nueva'),
    ('creciente', 'Luna creciente'),
    ('cuarto_creciente', 'Cuarto creciente'),
    ('creciente_gibosa', 'Creciente gibosa'),
    ('llena', 'Luna llena'),
    ('menguante_gibosa', 'Menguante gibosa'),
    ('cuarto_menguante', 'Cuarto menguante'),
    ('menguante', 'Luna menguante'),
]

MAR_CHOICES = [
    ('0', '0 — Calma'),
    ('1', '1 — Rizada'),
    ('2', '2 — Marejadilla'),
    ('3', '3 — Marejada'),
    ('4', '4 — Fuerte marejada'),
    ('5', '5 — Gruesa'),
    ('6', '6 — Muy gruesa'),
    ('7', '7 — Montaña'),
    ('8', '8 — Montañosa'),
    ('9', '9 — Excepcional'),
]


THEME_BASE_CHOICES = [
    ('slate', 'Slate'),
    ('gray', 'Gray'),
    ('zinc', 'Zinc'),
    ('neutral', 'Neutral'),
    ('stone', 'Stone'),
]

THEME_FONT_CHOICES = [
    ('sans-serif', 'Sans-serif'),
    ('serif', 'Serif'),
    ('monospace', 'Monospace'),
    ('comic', 'Comic'),
]

THEME_RADIUS_CHOICES = [
    ('0', '0'),
    ('0.5', '0.5'),
    ('1', '1'),
    ('1.5', '1.5'),
    ('2', '2'),
]


class SiteConfiguration(FileHandlerMixin):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_mode = models.BooleanField(default=True, verbose_name='Modo mantenimiento')
    primary_color = models.CharField(max_length=7, default='#2b4b9b', verbose_name='Color primario')
    theme_base = models.CharField(
        max_length=10,
        choices=THEME_BASE_CHOICES,
        default='neutral',
        verbose_name='Base del tema',
    )
    theme_font = models.CharField(
        max_length=20,
        choices=THEME_FONT_CHOICES,
        default='sans-serif',
        verbose_name='Familia tipográfica',
    )
    theme_radius = models.CharField(
        max_length=4,
        choices=THEME_RADIUS_CHOICES,
        default='1',
        verbose_name='Radio de esquina',
    )
    brand_logo = models.ImageField(
        upload_to='brand/',
        null=True,
        blank=True,
        verbose_name='Logo de marca',
        validators=[validate_image_upload],
    )
    favicon = models.ImageField(
        upload_to='brand/',
        null=True,
        blank=True,
        verbose_name='Favicon',
        validators=[validate_image_upload],
    )

    file_fields = ['brand_logo', 'favicon']

    class Meta:
        verbose_name = 'Configuración del sitio'
        verbose_name_plural = 'Configuración del sitio'
        default_permissions = ()
        permissions = [
            ('view_siteconfiguration', 'Ver'),
            ('add_siteconfiguration', 'Añadir'),
            ('change_siteconfiguration', 'Editar'),
            ('delete_siteconfiguration', 'Eliminar'),
        ]

    def __str__(self):
        return 'Configuración del sitio'

    @classmethod
    def get_instance(cls):
        instance = cls.objects.first()
        if not instance:
            instance = cls.objects.create(maintenance_mode=False)
        return instance

    def save(self, *args, **kwargs):
        if not self.pk and SiteConfiguration.objects.exists():
            return
        super().save(*args, **kwargs)


class CompanySettings(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nombre = models.CharField(max_length=255, verbose_name='Nombre de la empresa')
    direccion = models.TextField(verbose_name='Dirección')
    codigo_reeup = models.CharField(
        max_length=50, verbose_name='Código REEUP', validators=[validate_reeup]
    )
    nit = models.CharField(max_length=50, verbose_name='NIT', validators=[validate_nit])
    cuenta_bancaria = models.CharField(
        max_length=50, verbose_name='Cuenta bancaria', validators=[validate_account]
    )
    agencia_bancaria = models.CharField(max_length=100, verbose_name='Agencia bancaria')
    telefonos = models.CharField(
        max_length=100, verbose_name='Teléfonos', validators=[validate_phones]
    )
    registro_comercial = models.CharField(
        max_length=50,
        verbose_name='Registro Comercial',
        help_text='Ej: A09404',
    )

    class Meta:
        verbose_name = 'Datos de la empresa'
        verbose_name_plural = 'Datos de la empresa'
        default_permissions = ()
        permissions = [
            ('view_companysettings', 'Ver'),
            ('add_companysettings', 'Añadir'),
            ('change_companysettings', 'Editar'),
            ('delete_companysettings', 'Eliminar'),
        ]

    def __str__(self):
        return self.nombre

    @classmethod
    def get_instance(cls):
        instance = cls.objects.first()
        if not instance:
            instance = cls.objects.create(
                nombre='Centro Meteorológico Provincial Camagüey',
                direccion='Avenida Finlay Km 7 1/2. Camagüey',
                codigo_reeup='211.0.6749',
                nit='11009444483',
                cuenta_bancaria='0659641302450111',
                agencia_bancaria='BANDEC- 5961',
                telefonos='32261284',
                registro_comercial='A09404',
            )
        return instance


class EmailRecipientList(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True, verbose_name='Nombre')
    description = models.TextField(blank=True, verbose_name='Descripción')
    # Marca estable de la lista principal. Antes se buscaba la lista por
    # `name='newsletter'` y renombrarla rompía la sincronización en silencio.
    # Es la audiencia automática de avisos, pronósticos y novedades: el operador
    # no elige lista en ningún formulario.
    is_default = models.BooleanField(
        default=False,
        verbose_name='Lista principal',
        help_text=(
            'Recibe todos los envíos automáticos (avisos, pronósticos y novedades). '
            'Como máximo una lista puede ser la principal.'
        ),
    )

    class Meta:
        verbose_name = 'Lista de correo'
        verbose_name_plural = 'Listas de correo'
        default_permissions = ()
        permissions = [
            ('view_emailrecipientlist', 'Ver'),
            ('add_emailrecipientlist', 'Añadir'),
            ('change_emailrecipientlist', 'Editar'),
            ('delete_emailrecipientlist', 'Eliminar'),
        ]
        constraints = [
            # Parcial: solo restringe las filas marcadas. Sin la condición,
            # la base permitiría una sola lista no principal en todo el sitio.
            models.UniqueConstraint(
                fields=['is_default'],
                condition=models.Q(is_default=True),
                name='uniq_default_list',
            ),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def get_default(cls):
        """La lista principal, o `None` si nadie la marcó.

        No lanza excepción a propósito: la ausencia de lista principal es un
        estado válido (una instalación nueva) y quien llama decide qué hacer.
        """
        return cls.objects.filter(is_default=True).first()


class Subscriber(models.Model):
    """Una persona que aceptó recibir correo, con la prueba de su consentimiento.

    Separado de la pertenencia a listas (`Subscription`) porque el
    consentimiento pertenece a la persona: la misma dirección puede estar en
    «avisos» y en «novedades» sin duplicar la fila ni mentir sobre lo que aceptó.

    Un suscriptor confirmado no se borra nunca: la baja pone `active=False` para
    conservar a quién se le escribió.
    """

    class Source(models.TextChoices):
        LANDING = 'landing', 'Landing'
        REGISTRO = 'registro', 'Registro'
        PERFIL = 'perfil', 'Perfil'
        ADMIN = 'admin', 'Administración'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, verbose_name='Correo electrónico')
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.ADMIN, verbose_name='Origen'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Alta')
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='Confirmado')
    confirmation_token = models.UUIDField(
        null=True, blank=True, unique=True, verbose_name='Token de confirmación'
    )
    confirmation_sent_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Confirmación enviada'
    )
    # Marca del único recordatorio que se manda a quien no confirmó. Existe para
    # que la tarea diaria no reenvíe el mismo correo cada noche: sin esta columna
    # el filtro sería «sigue pendiente», y eso es cierto para siempre.
    reminder_sent_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Recordatorio enviado'
    )
    unsubscribe_token = models.UUIDField(
        default=uuid4, unique=True, editable=False, verbose_name='Token de baja'
    )
    active = models.BooleanField(default=True, verbose_name='Activo')

    class Meta:
        verbose_name = 'Suscriptor'
        verbose_name_plural = 'Suscriptores'
        default_permissions = ()
        permissions = [
            ('view_subscriber', 'Ver'),
            ('add_subscriber', 'Añadir'),
            ('change_subscriber', 'Editar'),
            ('delete_subscriber', 'Eliminar'),
        ]
        indexes = [models.Index(fields=['active', 'confirmed_at'])]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # La dirección es la identidad del suscriptor: normalizarla aquí evita
        # que `Juan@X.cu` y `juan@x.cu` creen dos personas distintas.
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_confirmed(self):
        return self.confirmed_at is not None


class Subscription(models.Model):
    """Pertenencia de un suscriptor a una lista de correo."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.CASCADE,
        related_name='subscriptions',
        verbose_name='Suscriptor',
    )
    recipient_list = models.ForeignKey(
        EmailRecipientList,
        on_delete=models.CASCADE,
        related_name='subscriptions',
        verbose_name='Lista de correo',
    )
    source = models.CharField(
        max_length=10,
        choices=Subscriber.Source.choices,
        default=Subscriber.Source.ADMIN,
        verbose_name='Origen',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Alta')

    class Meta:
        verbose_name = 'Suscripción'
        verbose_name_plural = 'Suscripciones'
        default_permissions = ()
        permissions = [
            ('view_subscription', 'Ver'),
            ('add_subscription', 'Añadir'),
            ('change_subscription', 'Editar'),
            ('delete_subscription', 'Eliminar'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['subscriber', 'recipient_list'],
                name='uniq_subscription_membership',
            ),
        ]

    def __str__(self):
        return f'{self.subscriber.email} → {self.recipient_list.name}'


class TaskExecutionLog(models.Model):
    STATUS_ENQUEUED = 'ENQUEUED'
    STATUS_EXECUTING = 'EXECUTING'
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_ERROR = 'ERROR'
    STATUS_RETRYING = 'RETRYING'
    STATUS_REVOKED = 'REVOKED'
    STATUS_CHOICES = [
        (STATUS_ENQUEUED, 'Enqueued'),
        (STATUS_EXECUTING, 'Executing'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_ERROR, 'Error'),
        (STATUS_RETRYING, 'Retrying'),
        (STATUS_REVOKED, 'Revoked'),
    ]

    task_id = models.CharField(max_length=255, db_index=True)
    task_name = models.CharField(max_length=255)
    logical_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    summary = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ENQUEUED)
    enqueued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    traceback = models.TextField(blank=True)
    args_repr = models.CharField(max_length=512, blank=True)
    func_name = models.CharField(max_length=255, blank=True)
    func_args = models.TextField(blank=True)

    class Meta:
        ordering = ['-enqueued_at']
        indexes = [models.Index(fields=['status', 'enqueued_at'])]
        default_permissions = ()
        permissions = [
            ('view_taskexecutionlog', 'Ver'),
            ('add_taskexecutionlog', 'Añadir'),
            ('change_taskexecutionlog', 'Editar'),
            ('delete_taskexecutionlog', 'Eliminar'),
        ]

    def __str__(self):
        return f'{self.task_name} {self.status} ({self.task_id})'


class ActivityLog(models.Model):
    ACTIVITY_ADDITION = 1
    ACTIVITY_CHANGE = 2
    ACTIVITY_DELETION = 3
    ACTIVITY_LOGIN = 4
    ACTIVITY_LOGOUT = 5
    ACTIVITY_FLAG_CHOICES = (
        (ACTIVITY_ADDITION, 'Adición'),
        (ACTIVITY_CHANGE, 'Cambio'),
        (ACTIVITY_DELETION, 'Eliminación'),
        (ACTIVITY_LOGIN, 'Inicio de sesión'),
        (ACTIVITY_LOGOUT, 'Cierre de sesión'),
    )

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='activity_logs',
        verbose_name='Usuario',
    )
    action_flag = models.PositiveSmallIntegerField(
        choices=ACTIVITY_FLAG_CHOICES, verbose_name='Tipo de acción', db_index=True
    )
    content_type = models.ForeignKey(
        ContentType, null=True, on_delete=models.SET_NULL, verbose_name='Tipo de contenido'
    )
    object_id = models.CharField(max_length=255, null=True, verbose_name='ID del objeto')
    object_repr = models.TextField(verbose_name='Representación del objeto')
    message = models.TextField(verbose_name='Mensaje')
    ip_address = models.GenericIPAddressField(null=True, verbose_name='Dirección IP')
    user_agent = models.TextField(null=True, verbose_name='User-Agent')
    action_time = models.DateTimeField(
        auto_now_add=True, verbose_name='Fecha y hora', db_index=True
    )

    class Meta:
        db_table = 'core_activity_log'
        verbose_name = 'Registro de actividad'
        verbose_name_plural = 'Registros de actividad'
        ordering = ['-action_time']
        default_permissions = ()
        permissions = [
            ('view_activitylog', 'Ver'),
            ('add_activitylog', 'Añadir'),
            ('change_activitylog', 'Editar'),
            ('delete_activitylog', 'Eliminar'),
        ]
        indexes = [models.Index(fields=['action_flag', 'action_time'])]

    def __str__(self):
        return f'{self.get_action_flag_display()} - {self.object_repr}'


class Notification(models.Model):
    """Aviso operativo para un superusuario, generado por el propio sistema.

    Vive en `core` porque es transversal: la fuente puede ser una tarea de Huey,
    una factura o un aviso por vencer, y ninguna de esas apps es dueña del
    concepto.

    `read_at` es `NULL` para no leída, y el índice único parcial lo usa como
    condición: un mismo hecho no puede generar dos avisos sin leer, pero sí
    puede volver a generarlo después de que el admin lo leyó. Un fallo repetido
    tras haberlo visto es información nueva, no ruido.
    """

    class Severity(models.TextChoices):
        INFO = 'info', 'Información'
        WARNING = 'warning', 'Advertencia'
        ERROR = 'error', 'Error'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        verbose_name='Destinatario',
    )
    severity = models.CharField(max_length=10, choices=Severity.choices, verbose_name='Severidad')
    source = models.CharField(max_length=20, verbose_name='Origen')
    title = models.CharField(max_length=200, verbose_name='Título')
    body = models.TextField(blank=True, verbose_name='Detalle')
    # Ruta relativa ('/facturas/<uuid>/'), nunca una URL absoluta: el dominio
    # público cambia entre entornos y guardarlo dejaría enlaces muertos.
    url = models.CharField(max_length=500, blank=True, verbose_name='Enlace')
    # sha256 hex de lo que identifica el HECHO, no el momento. Ver
    # `apps.core.notifications.make_dedupe_key`.
    dedupe_key = models.CharField(max_length=64, verbose_name='Clave de deduplicación')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada')
    read_at = models.DateTimeField(null=True, blank=True, verbose_name='Leída')
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name='Expira')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'
        default_permissions = ()
        permissions = [
            ('view_notification', 'Ver'),
            ('add_notification', 'Añadir'),
            ('change_notification', 'Editar'),
            ('delete_notification', 'Eliminar'),
        ]
        constraints = [
            # Parcial: solo aplica a las NO leídas. Es lo que convierte el
            # dedup en una garantía de la base y no en una condición de carrera
            # entre dos workers de Gunicorn que ven el mismo fallo.
            models.UniqueConstraint(
                fields=['recipient', 'dedupe_key'],
                condition=models.Q(read_at__isnull=True),
                name='uniq_notification_unread_dedupe',
            ),
        ]
        indexes = [
            models.Index(fields=['recipient', 'read_at']),
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f'[{self.severity}] {self.title}'[:80]

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def link(self):
        """Ruta navegable del aviso.

        Los avisos creados antes del arreglo guardaron el **nombre** de la URL
        (`home:warnings_tropical_cyclone`), y en un `href` el navegador lo
        interpreta como esquema propio y delega en `xdg-open`. Se resuelve al
        vuelo para no dejar enlaces rotos en filas viejas.
        """
        from django.urls import NoReverseMatch, reverse

        if not self.url:
            return ''
        if self.url.startswith('/'):
            return self.url
        try:
            return reverse(self.url)
        except NoReverseMatch:
            return ''


class NotificationPreference(models.Model):
    """Interruptores del cliente por canal y por fuente (cambio 030, §7.2).

    **Opt-out, no opt-in**: sin fila el aviso está activado. Un cliente nuevo no
    tiene que configurar nada para recibir lo que le corresponde, y la ausencia
    de fila es el estado normal.

    Los dos canales son independientes a propósito: apagar el aviso in-app no
    corta el correo, y apagar el correo no borra la campana. Son dos formas de
    enterarse del mismo hecho y quien decide no ver una no está pidiendo perder
    la otra.

    Solo existen filas para fuentes de cliente. Los avisos internos de los
    admins no pasan por aquí: `notify_superusers` no consulta preferencias.
    """

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
        verbose_name='Usuario',
    )
    source = models.CharField(max_length=20, verbose_name='Origen')
    in_app = models.BooleanField(default=True, verbose_name='Aviso en la campana')
    email = models.BooleanField(default=True, verbose_name='Aviso por correo')

    class Meta:
        verbose_name = 'Preferencia de aviso'
        verbose_name_plural = 'Preferencias de aviso'
        default_permissions = ()
        permissions = [
            ('view_notificationpreference', 'Ver'),
            ('add_notificationpreference', 'Añadir'),
            ('change_notificationpreference', 'Editar'),
            ('delete_notificationpreference', 'Eliminar'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'source'], name='uniq_notification_preference_user_source'
            ),
        ]
        ordering = ['user', 'source']

    def __str__(self):
        return f'{self.user} · {self.source}'
