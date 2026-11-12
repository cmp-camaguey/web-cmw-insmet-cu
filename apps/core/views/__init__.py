from apps.core.views.activity_log import ActivityLogListView
from apps.core.views.company_settings import (
    CompanySettingsAjaxUpdateView,
    CompanySettingsUpdateView,
)
from apps.core.views.email_recipients import (
    EmailRecipientListCreateView,
    EmailRecipientListDeleteView,
    EmailRecipientListListView,
    EmailRecipientListUpdateView,
)
from apps.core.views.env_config import EnvironmentConfigView
from apps.core.views.serve_file import PublicServeFileView, ServeModelFileView
from apps.core.views.site_configuration import (
    SiteConfigurationUpdateView,
)

__all__ = [
    'ActivityLogListView',
    'CompanySettingsAjaxUpdateView',
    'CompanySettingsUpdateView',
    'EmailRecipientListCreateView',
    'EmailRecipientListDeleteView',
    'EmailRecipientListListView',
    'EmailRecipientListUpdateView',
    'EnvironmentConfigView',
    'PublicServeFileView',
    'ServeModelFileView',
    'SiteConfigurationUpdateView',
]
