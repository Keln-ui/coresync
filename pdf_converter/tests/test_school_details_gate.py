from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from pdf_converter.models import SchoolProfile
from revision.models import Class, Subject, SchemeOfWork


class SchoolDetailsSchemeGenerationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='teacher',
            email='teacher@example.com',
            password='test-pass-123',
            is_staff=True,
        )

    def test_preview_requires_school_details(self):
        self.client.login(username='teacher', password='test-pass-123')
        response = self.client.post(reverse('sow_preview'), {'mode': 'manual'})

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('edit_profile'), response.url)
        self.assertIn('institution_only=1', response.url)

    def test_institution_details_page_excludes_account_settings(self):
        self.client.login(username='teacher', password='test-pass-123')
        response = self.client.get(reverse('edit_profile'), {
            'institution_only': '1',
            'next': reverse('sow_generator'),
        })

        self.assertContains(response, 'Enter Institution Details')
        self.assertNotContains(response, 'Update Password')
        self.assertContains(response, 'Institution Details')

    def test_preview_uses_saved_school_name(self):
        SchoolProfile.objects.create(
            user=self.user,
            school_name='Muhu Senior School',
            phone='0712345678',
            email='school@example.com',
        )
        self.client.login(username='teacher', password='test-pass-123')

        response = self.client.post(reverse('sow_preview'), {
            'mode': 'manual',
            'school_name': '',
            'class_id': '',
            'subject_id': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Muhu Senior School')

    def test_profile_can_create_school_details(self):
        self.client.login(username='teacher', password='test-pass-123')
        response = self.client.post(reverse('edit_profile'), {
            'action': 'update_school_details',
            'school_name': 'Muhu Senior School',
            'school_address': 'Nairobi',
            'phone': '0712345678',
            'email': 'school@example.com',
            'principal_name': 'Principal',
            'next': reverse('sow_generator'),
        })

        self.assertRedirects(response, reverse('sow_generator'))
        profile = SchoolProfile.objects.get(user=self.user)
        self.assertEqual(profile.school_name, 'Muhu Senior School')

    def test_direct_scheme_download_opens_details_form_without_profile(self):
        class_ref = Class.objects.create(name='Grade 9', curriculum='cbe', level='secondary')
        subject = Subject.objects.create(name='Mathematics')
        scheme = SchemeOfWork.objects.create(
            class_ref=class_ref,
            subject=subject,
            title='Mathematics Scheme',
            term='term1',
            academic_year='2026',
        )

        response = self.client.get(reverse('download_scheme', args=[scheme.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'NAME OF SCHOOL')
        self.assertContains(response, 'Class/Grade')
        self.assertContains(response, 'Year')
        self.assertContains(response, 'Term')
        self.assertContains(response, 'REPORTING WEEK')
        self.assertContains(response, 'MID-BREAK WEEK')
        self.assertContains(response, 'EXAMS')
        self.assertContains(response, 'CLOSING WEEK')
        self.assertNotContains(response, 'Phone Number')
        self.assertNotContains(response, 'School Email')

    def test_download_rejects_special_week_outside_scheme_duration(self):
        class_ref = Class.objects.create(name='Grade 9', curriculum='cbe', level='secondary')
        subject = Subject.objects.create(name='Mathematics')
        scheme = SchemeOfWork.objects.create(
            class_ref=class_ref,
            subject=subject,
            title='Mathematics Scheme',
            term='term1',
            academic_year='2026',
            weeks_count=4,
        )

        response = self.client.post(reverse('download_scheme', args=[scheme.id]), {
            'school_name': 'Muhu Senior School',
            'class_id': str(class_ref.id),
            'academic_year': '2026',
            'term': 'term1',
            'reporting_week': '5',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Special event weeks must be between week 1 and week 4.')
        self.assertEqual(SchemeOfWork.objects.get(id=scheme.id).download_count, 0)
