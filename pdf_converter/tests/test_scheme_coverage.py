from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from revision.models import Class, SchemeOfWork, SchemeWeekProgress, Subject


class SchemeCoverageTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.teacher = user_model.objects.create_user(
            username='coverage-teacher',
            password='test-password',
        )
        self.other_teacher = user_model.objects.create_user(
            username='other-teacher',
            password='test-password',
        )
        self.staff = user_model.objects.create_user(
            username='coverage-admin',
            password='test-password',
            is_staff=True,
        )
        self.class_ref = Class.objects.create(
            name='Grade 10',
            curriculum='cbe',
            level='secondary',
        )
        self.subject = Subject.objects.create(
            class_ref=self.class_ref,
            name='Agriculture',
        )
        self.scheme = SchemeOfWork.objects.create(
            class_ref=self.class_ref,
            subject=self.subject,
            title='Agriculture Term 1',
            term='term1',
            academic_year='2026',
            weeks_count=2,
            file='schemes_of_work/agriculture.docx',
        )

    def test_tracker_requires_login(self):
        response = self.client.get(reverse('scheme_coverage', args=[self.scheme.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_teacher_can_update_week_and_keep_notes_private(self):
        self.client.force_login(self.teacher)
        url = reverse('scheme_coverage', args=[self.scheme.pk])

        response = self.client.post(url, {
            'week_number': '1',
            'status': 'taught',
            'notes': 'Completed soil preparation.',
        })

        self.assertRedirects(response, url)
        progress = SchemeWeekProgress.objects.get(
            scheme=self.scheme,
            teacher=self.teacher,
            week_number=1,
        )
        self.assertEqual(progress.status, 'taught')
        self.assertEqual(progress.notes, 'Completed soil preparation.')

        self.client.force_login(self.other_teacher)
        response = self.client.get(url)

        self.assertContains(response, 'value="planned" selected')
        self.assertNotContains(response, 'Completed soil preparation.')

    def test_invalid_week_is_not_saved(self):
        self.client.force_login(self.teacher)

        response = self.client.post(reverse('scheme_coverage', args=[self.scheme.pk]), {
            'week_number': '3',
            'status': 'taught',
            'notes': '',
        }, follow=True)

        self.assertEqual(SchemeWeekProgress.objects.count(), 0)
        self.assertContains(response, 'Week must be between 1 and 2.')

    def test_staff_can_view_aggregate_coverage_overview(self):
        SchemeWeekProgress.objects.create(
            scheme=self.scheme,
            teacher=self.teacher,
            week_number=1,
            status='follow_up',
            notes='Needs more field practice.',
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse('scheme_coverage_overview'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Scheme coverage overview')
        self.assertContains(response, 'Agriculture Term 1')
        self.assertContains(response, 'Needs more field practice.')

