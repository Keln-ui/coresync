from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Class, Curriculum, RevisionMaterial, SchemeOfWork, Subject


class StaffResourceUploadTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='resource-staff',
            password='test-password',
            is_staff=True,
        )
        self.user = get_user_model().objects.create_user(
            username='resource-user',
            password='test-password',
        )
        self.class_ref = Class.objects.create(
            name='Grade 6',
            curriculum='cbe',
            level='primary',
        )
        self.subject = Subject.objects.create(
            class_ref=self.class_ref,
            name='Science',
        )
        self.client.force_login(self.staff)

    def test_upload_dashboard_and_forms_are_staff_only(self):
        upload_url = reverse('staff_upload_resource', args=['resource'])
        self.client.logout()
        response = self.client.get(reverse('staff_resource_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(upload_url).status_code, 302)

        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_resource_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(upload_url).status_code, 302)

    def test_dashboard_links_to_each_upload_category(self):
        response = self.client.get(reverse('staff_resource_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Notes')
        self.assertContains(response, 'Upload Exams')
        self.assertContains(response, 'Upload CBC Books')
        self.assertContains(response, 'Upload Marking Schemes')
        self.assertContains(response, 'Upload Scheme of Work')

    def test_staff_can_upload_revision_material(self):
        response = self.client.post(
            reverse('staff_upload_resource', args=['resource']),
            {
                'title': 'Science Notes',
                'subject': self.subject.pk,
                'material_type': 'notes',
                'term': 'term1',
                'description': 'Grade 6 notes',
                'year': '2026',
                'is_free': 'on',
                'price': '0',
                'file': SimpleUploadedFile('science.txt', b'notes'),
            },
        )

        self.assertEqual(response.status_code, 302)
        material = RevisionMaterial.objects.get(title='Science Notes')
        self.assertEqual(material.material_type, 'notes')
        self.assertEqual(material.subject, self.subject)
        material.file.delete(save=False)

    def test_staff_can_upload_scheme_and_curriculum(self):
        scheme_response = self.client.post(
            reverse('staff_upload_resource', args=['scheme']),
            {
                'class_ref': self.class_ref.pk,
                'subject': self.subject.pk,
                'title': 'Grade 6 Science Term 1',
                'term': 'term1',
                'academic_year': '2026',
                'weeks_count': '12',
                'description': '',
                'is_active': 'on',
                'file': SimpleUploadedFile('science-scheme.docx', b'docx'),
            },
        )
        self.assertRedirects(scheme_response, reverse('staff_resource_dashboard'))
        scheme = SchemeOfWork.objects.get(title='Grade 6 Science Term 1')
        scheme.file.delete(save=False)

        curriculum_response = self.client.post(
            reverse('staff_upload_resource', args=['curriculum']),
            {
                'class_ref': self.class_ref.pk,
                'subject': self.subject.pk,
                'curriculum_type': 'cbc',
                'academic_year': '2026',
                'extracted_topics': '',
                'is_active': 'on',
                'file': SimpleUploadedFile('science-curriculum.pdf', b'pdf'),
            },
        )
        self.assertRedirects(curriculum_response, reverse('staff_resource_dashboard'))
        curriculum = Curriculum.objects.get(subject=self.subject)
        curriculum.file.delete(save=False)
