from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from pdf_converter import admin_views


@override_settings(
    ACADEMIC_HOST='academic.test',
    CONVERTER_HOST='converter.test',
    ALLOWED_HOSTS=['academic.test', 'converter.test', '127.0.0.1', 'revise.localhost'],
)
class AdminDashboardRoutingTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.staff_user = SimpleNamespace(is_authenticated=True, is_staff=True)

    @patch('pdf_converter.admin_views._academic_admin_dashboard')
    def test_academic_host_uses_academic_dashboard(
        self,
        academic_dashboard,
    ):
        academic_dashboard.return_value = HttpResponse('academic dashboard')
        request = self.factory.get('/admin/dashboard/', HTTP_HOST='academic.test')
        request.user = self.staff_user

        response = admin_views.admin_dashboard(request)

        self.assertEqual(response.content, b'academic dashboard')
        academic_dashboard.assert_called_once_with(request)

    @patch('pdf_converter.admin_views._converter_admin_dashboard')
    def test_converter_host_opens_converter_dashboard(
        self,
        converter_dashboard,
    ):
        converter_dashboard.return_value = HttpResponse('converter dashboard')
        request = self.factory.get(
            '/admin/dashboard/',
            HTTP_HOST='converter.test',
        )
        request.user = self.staff_user

        response = admin_views.admin_dashboard(request)

        self.assertEqual(response.content, b'converter dashboard')
        converter_dashboard.assert_called_once_with(request)

    @patch('pdf_converter.admin_views._academic_admin_dashboard')
    def test_loopback_host_uses_academic_dashboard(
        self,
        academic_dashboard,
    ):
        academic_dashboard.return_value = HttpResponse('academic dashboard')
        request = self.factory.get('/admin/dashboard/', HTTP_HOST='127.0.0.1:8000')
        request.user = self.staff_user

        response = admin_views.admin_dashboard(request)

        self.assertEqual(response.content, b'academic dashboard')
        academic_dashboard.assert_called_once_with(request)


@override_settings(ALLOWED_HOSTS=['revise.localhost'])
class AdminDashboardPageTests(TestCase):
    def test_dashboard_renders_on_revise_localhost(self):
        staff_user = get_user_model().objects.create_user(
            username='dashboard-admin',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(
            '/admin/dashboard/',
            HTTP_HOST='revise.localhost',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Academic operations')
        self.assertContains(response, 'exam and marking-scheme builder')
        self.assertContains(response, 'No active KICD syllabus sources yet')
        self.assertContains(response, 'Add syllabus')
        self.assertContains(response, 'Scheme coverage')
        self.assertContains(response, f'href="{reverse("staff_resource_dashboard")}"')
        self.assertContains(response, 'Upload resources')
