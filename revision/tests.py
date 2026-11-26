import csv
import io
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse

from .forms import BulkRevisionMaterialUploadForm
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
        bulk_upload_url = reverse('staff_bulk_upload_resources')
        self.assertEqual(self.client.get(bulk_upload_url).status_code, 302)

        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_resource_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(upload_url).status_code, 302)
        self.assertEqual(self.client.get(bulk_upload_url).status_code, 302)

    def test_dashboard_links_to_each_upload_category(self):
        response = self.client.get(reverse('staff_resource_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Notes')
        self.assertContains(response, 'Upload Exams')
        self.assertContains(response, 'Upload CBC Books')
        self.assertContains(response, 'Upload Marking Schemes')
        self.assertContains(response, 'Upload Scheme of Work')
        self.assertContains(response, 'Bulk ZIP Upload')

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
        self.assertEqual(scheme_response.status_code, 302)
        self.assertEqual(scheme_response['Location'], reverse('staff_resource_dashboard'))
        scheme = SchemeOfWork.objects.get(title='Grade 6 Science Term 1')
        scheme.file.delete(save=False)

        with patch(
            'revision.views.extract_document_text',
            return_value='Science strand\nLiving things\nLearning outcomes',
        ), patch(
            'revision.views.render', return_value=HttpResponse('review')
        ) as render_template:
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
        self.assertEqual(curriculum_response.status_code, 200)
        self.assertIn('Science strand', render_template.call_args.args[2]['review_text'])
        self.assertEqual(
            render_template.call_args.args[2]['review_form'].initial['extracted_topics'],
            'Science strand\nLiving things\nLearning outcomes',
        )
        curriculum = Curriculum.objects.get(subject=self.subject)
        self.assertFalse(curriculum.is_active)
        self.assertEqual(curriculum.extracted_topics, '')

        with patch(
            'revision.views.render', return_value=HttpResponse('dashboard')
        ):
            review_response = self.client.post(
                reverse('staff_upload_resource', args=['curriculum']),
                {
                    'curriculum_review_action': 'confirm',
                    'extracted_topics': 'Living things\nLearning outcomes',
                    'activate': 'on',
                },
            )
        self.assertEqual(review_response.status_code, 302)
        self.assertEqual(review_response['Location'], reverse('staff_resource_dashboard'))
        curriculum.refresh_from_db()
        self.assertTrue(curriculum.is_active)
        self.assertEqual(curriculum.extracted_topics, 'Living things\nLearning outcomes')
        curriculum.file.delete(save=False)

    @patch(
        'revision.views.extract_document_text',
        side_effect=ValueError('No readable text was found in the document.'),
    )
    def test_staff_cannot_activate_curriculum_without_readable_preview(self, extract_text):
        with patch(
            'revision.views.render', return_value=HttpResponse('review')
        ) as render_template:
            response = self.client.post(
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

        self.assertEqual(response.status_code, 200)
        self.assertIn('No readable text', render_template.call_args.args[2]['review_error'])
        curriculum = Curriculum.objects.get(subject=self.subject)
        self.assertFalse(curriculum.is_active)

        with patch(
            'revision.views.render', return_value=HttpResponse('review')
        ) as render_template:
            review_response = self.client.post(
                reverse('staff_upload_resource', args=['curriculum']),
                {
                    'curriculum_review_action': 'confirm',
                    'extracted_topics': 'Living things',
                    'activate': 'on',
                },
            )

        self.assertEqual(review_response.status_code, 200)
        self.assertIn(
            'Upload a searchable document',
            str(render_template.call_args.args[2]['review_form'].errors),
        )
        curriculum.refresh_from_db()
        self.assertFalse(curriculum.is_active)
        curriculum.file.delete(save=False)

    def make_bulk_archive(self, rows, files):
        manifest = io.StringIO()
        writer = csv.DictWriter(
            manifest,
            fieldnames=(
                'filename',
                'title',
                'subject_id',
                'material_type',
                'term',
                'description',
                'year',
                'is_free',
                'price',
            ),
        )
        writer.writeheader()
        writer.writerows(rows)

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.csv', manifest.getvalue())
            for filename, content in files.items():
                archive.writestr(filename, content)
        return SimpleUploadedFile('resources.zip', archive_buffer.getvalue(), 'application/zip')

    def test_staff_can_bulk_upload_resources_from_manifest(self):
        uploaded_zip = self.make_bulk_archive(
            [
                {
                    'filename': 'files/science-notes.txt',
                    'title': 'Science Notes',
                    'subject_id': self.subject.pk,
                    'material_type': 'notes',
                    'term': 'term1',
                    'description': 'Revision notes',
                    'year': 2026,
                    'is_free': 'true',
                    'price': '0',
                },
                {
                    'filename': 'science-paper.txt',
                    'title': 'Science Paper',
                    'subject_id': self.subject.pk,
                    'material_type': 'past_paper',
                    'term': 'term2',
                    'description': '',
                    'year': 2025,
                    'is_free': 'false',
                    'price': '150',
                },
            ],
            {
                'files/science-notes.txt': b'notes content',
                'science-paper.txt': b'paper content',
            },
        )

        response = self.client.post(
            reverse('staff_bulk_upload_resources'),
            {'zip_file': uploaded_zip},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('staff_resource_dashboard'))
        self.assertEqual(RevisionMaterial.objects.count(), 2)
        notes = RevisionMaterial.objects.get(title='Science Notes')
        paper = RevisionMaterial.objects.get(title='Science Paper')
        self.assertEqual(notes.file.read(), b'notes content')
        self.assertEqual(paper.file.read(), b'paper content')
        self.assertTrue(notes.is_free)
        self.assertFalse(paper.is_free)
        self.assertEqual(paper.price, 150)
        notes.file.delete(save=False)
        paper.file.delete(save=False)

    def test_bulk_upload_rejects_manifest_references_to_missing_files(self):
        uploaded_zip = self.make_bulk_archive(
            [{
                'filename': 'missing.pdf',
                'title': 'Missing resource',
                'subject_id': self.subject.pk,
                'material_type': 'notes',
                'term': 'all',
                'description': '',
                'year': '',
                'is_free': 'true',
                'price': '0',
            }],
            {},
        )

        form = BulkRevisionMaterialUploadForm(files={'zip_file': uploaded_zip})

        self.assertFalse(form.is_valid())
        self.assertIn('missing or invalid filename', str(form.errors))
        self.assertEqual(RevisionMaterial.objects.count(), 0)
