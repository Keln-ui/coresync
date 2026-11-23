from django.contrib.auth import get_user_model
from django.test import TestCase

from pdf_converter.models import SubscriptionPlan


class PlanManagementTests(TestCase):
    def test_manage_plans_loads_for_staff(self):
        staff_user = get_user_model().objects.create_user(
            username='plans-admin',
            password='test-password',
            is_staff=True,
        )
        SubscriptionPlan.objects.create(
            name='Converter plan',
            amount='100.00',
        )
        self.client.force_login(staff_user)

        response = self.client.get('/manage-plans/', HTTP_HOST='revise.localhost')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Converter plan')
