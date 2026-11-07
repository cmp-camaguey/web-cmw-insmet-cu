from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteConfiguration
from apps.meteo.models import Warning


class WarningListViewQueryCountTests(TestCase):
    """Regression guard for WarningListView.

    The meteo WarningListView renders a DataTables grid
    (apps/meteo/templates/pages/meteo/warning/early_warning/list.html extends
    layouts/list.html) which loads ALL records and paginates client-side per the
    project convention. The template reads `object.user.get_full_name`, so the
    queryset must join `user` to avoid an N+1. (The `user__profile` join from the
    original design applies to the card-layout templates/layouts/avisos.html used
    by the public home warning pages, which are out of scope here.)
    """

    @classmethod
    def setUpTestData(cls):
        SiteConfiguration.objects.update_or_create(defaults={'maintenance_mode': False})
        cls.admin = User.objects.create_superuser(
            'perfadmin',
            'perfadmin@example.com',
            'pass',
            first_name='Perf',
            last_name='Admin',
        )
        # Create enough records so the DataTables grid really loads every record
        # (client-side pagination, no paginate_by on the view).
        for i in range(25):
            Warning.objects.create(
                warning_type='early',
                user=cls.admin,
                summary=f'Aviso {i}',
                valid_until=timezone.now() + timezone.timedelta(days=1),
            )
        cls.url = reverse('meteo:alerta_temprana_list')

    def test_query_count_is_constant_and_joined(self):
        # Calibrated constant (this environment): session/auth + middleware
        # (CheckUserProfile, MaintenanceMode) + SiteConfiguration + context-processor
        # counts + 1 joined page fetch. No pagination COUNT: DataTables views
        # (layouts/list.html) do not set paginate_by.
        # If select_related('user') is dropped, each of the 25 rendered rows
        # would add a user query -> count would scale with row count.
        self.client.force_login(self.admin)
        # 11 (was 13): the context processor used to run three COUNT(*) over
        # Warning (early / tropical / storm) to feed the navbar badges. It now
        # reads the active warnings once and derives the three counts AND the
        # emergency banner from that same SELECT: two constant queries fewer on
        # every page.
        # History: 13 -> 11 (subscription badges became one conditional
        # aggregate) -> 13 (notification bell: unread COUNT + top-10 dropdown,
        # change 026) -> 11 (single active-warning SELECT).
        # Constant and indexed: neither the notification queries nor the warning
        # one scale with the number of rows on this page, which is what this
        # guard exists to catch.
        with self.assertNumQueries(11):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_loads_all_records_datatables(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('objects', response.context)
        # DataTables view loads every record (client-side pagination).
        self.assertEqual(len(response.context['objects']), 25)
        # Autor column renders the joined user without per-row queries.
        self.assertContains(response, 'Perf Admin')
