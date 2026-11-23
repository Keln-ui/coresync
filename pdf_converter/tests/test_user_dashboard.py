from django.contrib.auth import get_user_model
from django.test import TestCase


class UserDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='converter-user',
            password='test-password',
        )
        self.client.force_login(self.user)

    def test_dashboard_loads_on_revise_localhost_without_revision_app(self):
        response = self.client.get('/dashboard/', HTTP_HOST='revise.localhost')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard.html')
        self.assertContains(response, 'Recent Conversions')
        self.assertContains(response, 'Conversion History')
        self.assertNotContains(response, 'Revision Materials')
        self.assertNotContains(response, 'Popular Revision Materials')

    def test_profile_loads_without_revision_app(self):
        response = self.client.get('/profile/', HTTP_HOST='revise.localhost')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'profile.html')
