from django.contrib import admin

from apps.core.models import (
    ActivityLog,
    CompanySettings,
    EmailRecipientList,
    SiteConfiguration,
    Subscriber,
    TaskExecutionLog,
)


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = ('maintenance_mode',)


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo_reeup', 'nit')


@admin.register(EmailRecipientList)
class EmailRecipientListAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_default')


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'source', 'confirmed_at', 'active')
    list_filter = ('active', 'source')
    search_fields = ('email',)

    def has_delete_permission(self, request, obj=None):
        # La baja pone `active=False`: borrar la fila destruye el consentimiento
        # que la persona dio y el rastro de que lo retiró.
        return False


@admin.register(TaskExecutionLog)
class TaskExecutionLogAdmin(admin.ModelAdmin):
    list_display = ('task_name', 'status', 'attempts', 'enqueued_at', 'finished_at')
    list_filter = ('status',)
    readonly_fields = [f.name for f in TaskExecutionLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Mismo criterio que `ActivityLogAdmin`: un registro que se puede editar
        # por partes no sirve como registro.
        return False


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('action_time', 'user', 'action_flag', 'object_repr')
    list_filter = ('user', 'action_flag', 'action_time')
    search_fields = ('object_repr', 'message', 'ip_address')
    readonly_fields = [f.name for f in ActivityLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
