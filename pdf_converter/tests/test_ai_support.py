import io
from unittest.mock import Mock, patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from pdf_converter.views import ai_assistant
from pdf_converter.ai_support import (
    AIServiceError,
    MAX_SYLLABUS_PAGES,
    extract_document_text,
    generate_ai_response,
    get_kicd_syllabus_context,
    get_kicd_syllabus_topics,
)
from revision.models import Class, Curriculum, Subject


@override_settings(
    GEMINI_API_KEY='test-gemini-key',
    GEMINI_MODEL='gemini-test',
    SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies',
)
class AIAssistantTests(TestCase):
    def setUp(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            'candidates': [{'content': {'parts': [{'text': 'A helpful answer.'}]}}]
        }
        self.gemini_response = response

    def test_ai_hub_lists_the_available_tools(self):
        response = self.client.get(reverse('ai_assistant'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Document Q&amp;A')
        self.assertContains(response, 'Study tools')
        self.assertContains(response, 'Scheme drafts')
        self.assertContains(response, 'Exam builder')
        self.assertContains(response, 'General assistant')

    @patch('pdf_converter.views.render')
    def test_scheme_and_exam_class_and_subject_options_come_from_database(self, render_template):
        class_ref = Class.objects.create(name='Grade 10', curriculum='cbe', level='secondary')
        subject = Subject.objects.create(name='Applied Agriculture')
        request = RequestFactory().get(reverse('ai_assistant'))
        request.session = {}

        ai_assistant(request)

        context = render_template.call_args.args[2]
        self.assertEqual(list(context['classes']), [class_ref])
        self.assertEqual(list(context['subjects']), [subject.name])

    def test_scheme_topics_endpoint_returns_topics_for_the_selected_curriculum(self):
        class_ref = Class.objects.create(name='Grade 10', curriculum='cbe', level='secondary')
        subject = Subject.objects.create(name='Applied Agriculture')
        Curriculum.objects.create(
            class_ref=class_ref,
            subject=subject,
            curriculum_type='cbc',
            academic_year='2026',
            file=SimpleUploadedFile('curriculum.pdf', b'%PDF sample'),
            extracted_topics='Agricultural land\nCrop production',
        )

        response = self.client.get(reverse('ai_assistant_topics'), {
            'class_name': class_ref.name,
            'subject': subject.name,
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['topics'], ['Agricultural land', 'Crop production'])
        Curriculum.objects.get().file.delete(save=False)

    @patch(
        'pdf_converter.ai_support.get_kicd_syllabus_context',
        return_value=('Strand\nCrop production\nLearning outcome', 'Official syllabus'),
    )
    def test_scheme_topics_fall_back_to_the_syllabus_file_when_not_pre_extracted(self, get_context):
        class_ref = Class.objects.create(name='Grade 10', curriculum='cbe', level='secondary')
        subject = Subject.objects.create(name='Applied Agriculture')
        curriculum = Curriculum.objects.create(
            class_ref=class_ref,
            subject=subject,
            curriculum_type='cbc',
            academic_year='2026',
            file=SimpleUploadedFile('curriculum.pdf', b'%PDF sample'),
        )

        topics = get_kicd_syllabus_topics(class_ref.name, subject.name)

        self.assertEqual(topics, ['Strand', 'Crop production', 'Learning outcome'])
        get_context.assert_called_once_with(class_ref.name, subject.name)
        curriculum.file.delete(save=False)

    @patch('PyPDF2.PdfReader')
    def test_syllabus_extraction_allows_more_pages_than_user_documents(self, pdf_reader):
        pdf_reader.return_value.pages = [
            Mock(extract_text=Mock(return_value='Official syllabus text.'))
            for _ in range(51)
        ]
        uploaded_file = SimpleUploadedFile('syllabus.pdf', b'%PDF sample')

        with self.assertRaisesMessage(ValueError, 'up to 50 pages'):
            extract_document_text(uploaded_file)

        syllabus_text = extract_document_text(uploaded_file, max_pages=MAX_SYLLABUS_PAGES)

        self.assertIn('Official syllabus text.', syllabus_text)

    def test_extracts_text_from_powerpoint_and_excel_curriculum_files(self):
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = 'Learning outcomes'
        pptx_data = io.BytesIO()
        presentation.save(pptx_data)

        workbook = Workbook()
        workbook.active['A1'] = 'Assessment criteria'
        xlsx_data = io.BytesIO()
        workbook.save(xlsx_data)

        presentation_text = extract_document_text(
            SimpleUploadedFile('curriculum.pptx', pptx_data.getvalue())
        )
        workbook_text = extract_document_text(
            SimpleUploadedFile('curriculum.xlsx', xlsx_data.getvalue())
        )

        self.assertIn('Learning outcomes', presentation_text)
        self.assertIn('Assessment criteria', workbook_text)

    @patch('pdf_converter.ai_support._get_ocr_engine')
    @patch('pypdfium2.PdfDocument')
    @patch('PyPDF2.PdfReader')
    def test_scanned_pdf_pages_are_read_with_ocr(
        self, pdf_reader, pdf_document, get_ocr_engine
    ):
        pdf_reader.return_value.pages = [
            Mock(extract_text=Mock(return_value='')),
            Mock(extract_text=Mock(return_value='Selectable curriculum text.')),
        ]
        page = Mock()
        image = Mock()
        page.render.return_value.to_pil.return_value = image
        pdf_document.return_value.__getitem__.return_value = page
        get_ocr_engine.return_value.image_to_string.return_value = 'Scanned learning outcomes.'
        uploaded_file = SimpleUploadedFile('scanned-syllabus.pdf', b'%PDF sample')

        text = extract_document_text(uploaded_file)

        self.assertIn('Scanned learning outcomes.', text)
        self.assertIn('Selectable curriculum text.', text)
        pdf_document.return_value.__getitem__.assert_called_once_with(0)
        get_ocr_engine.return_value.image_to_string.assert_called_once_with(image)
        image.close.assert_called_once()
        page.close.assert_called_once()

    @patch('pdf_converter.ai_support._get_ocr_engine', return_value=None)
    @patch('PyPDF2.PdfReader')
    @patch('pdf_converter.ai_support.requests.post')
    def test_scanned_pdf_uses_gemini_when_tesseract_is_unavailable(
        self, post, pdf_reader, get_ocr_engine
    ):
        pdf_reader.return_value.pages = [Mock(extract_text=Mock(return_value=''))]
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={
                'candidates': [{'content': {'parts': [{'text': 'Transcribed syllabus text.'}]}}]
            }),
        )
        pdf_bytes = b'%PDF scanned syllabus'
        uploaded_file = SimpleUploadedFile('scanned-syllabus.pdf', pdf_bytes)

        text = extract_document_text(uploaded_file)

        self.assertEqual(text, 'Transcribed syllabus text.')
        self.assertEqual(
            post.call_args.kwargs['json']['contents'][0]['parts'][1]['inline_data'],
            {
                'mime_type': 'application/pdf',
                'data': 'JVBERiBzY2FubmVkIHN5bGxhYnVz',
            },
        )

    @patch('pdf_converter.ai_support._get_ocr_engine', return_value=None)
    @override_settings(GEMINI_API_KEY='')
    def test_scanned_pdf_reports_when_neither_ocr_option_is_configured(
        self, get_ocr_engine
    ):
        with patch('PyPDF2.PdfReader') as pdf_reader:
            pdf_reader.return_value.pages = [Mock(extract_text=Mock(return_value=''))]
            with self.assertRaisesMessage(ValueError, 'Tesseract OCR or a configured GEMINI_API_KEY'):
                extract_document_text(
                    SimpleUploadedFile('scanned-syllabus.pdf', b'%PDF scanned syllabus')
                )

    @patch('pdf_converter.ai_support._get_ocr_engine', return_value=None)
    @patch('pdf_converter.ai_support.requests.post', side_effect=requests.ConnectionError('Connection refused'))
    def test_scanned_pdf_reports_gemini_network_requirement(self, post, get_ocr_engine):
        with patch('PyPDF2.PdfReader') as pdf_reader:
            pdf_reader.return_value.pages = [Mock(extract_text=Mock(return_value=''))]
            with self.assertRaisesMessage(ValueError, 'outbound HTTPS connections'):
                extract_document_text(
                    SimpleUploadedFile('scanned-syllabus.pdf', b'%PDF scanned syllabus')
                )

    @patch('pdf_converter.ai_support._get_ocr_engine')
    def test_extracts_text_from_curriculum_image(self, get_ocr_engine):
        get_ocr_engine.return_value.image_to_string.return_value = 'Scanned topic'
        image = io.BytesIO()
        from PIL import Image

        Image.new('RGB', (10, 10), color='white').save(image, format='PNG')

        text = extract_document_text(
            SimpleUploadedFile('curriculum.png', image.getvalue())
        )

        self.assertEqual(text, 'Scanned topic')

    @patch('pdf_converter.ai_support.requests.post')
    def test_general_chat_uses_configured_gemini_model(self, post):
        post.return_value = self.gemini_response

        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'chat',
            'message': 'How can I plan a lesson?',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['answer'], 'A helpful answer.')
        args, kwargs = post.call_args
        self.assertIn('/models/gemini-test:generateContent', args[0])
        self.assertEqual(kwargs['headers']['x-goog-api-key'], 'test-gemini-key')
        self.assertIn('How can I plan a lesson?', kwargs['json']['contents'][0]['parts'][0]['text'])

    @patch('pdf_converter.ai_support.requests.post')
    def test_general_chat_knows_all_core_services(self, post):
        post.return_value = self.gemini_response

        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'chat',
            'message': 'What services does CoreSynch offer?',
        })

        self.assertEqual(response.status_code, 200)
        sent_prompt = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertIn('Revision Library', sent_prompt)
        self.assertIn('Scheme of Work Generator', sent_prompt)
        self.assertIn('PDF utilities', sent_prompt)
        self.assertIn('Teaching opportunities', sent_prompt)
        self.assertIn('Subscription plans', sent_prompt)

    @patch('pdf_converter.ai_support.requests.post')
    def test_chat_follow_up_includes_recent_turns_and_renders_transcript(self, post):
        first_response = Mock(status_code=200)
        first_response.json.return_value = {
            'candidates': [{'content': {'parts': [{'text': 'You mentioned algebra.'}]}}]
        }
        second_response = Mock(status_code=200)
        second_response.json.return_value = {
            'candidates': [{'content': {'parts': [{'text': 'You said algebra.'}]}}]
        }
        post.side_effect = [first_response, second_response]

        self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'chat',
            'message': 'I am studying algebra.',
        })
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'chat',
            'message': 'What subject did I mention?',
        })

        self.assertEqual(response.status_code, 200)
        contents = post.call_args.kwargs['json']['contents']
        self.assertEqual([turn['role'] for turn in contents], ['user', 'model', 'user'])
        self.assertEqual(contents[0]['parts'][0]['text'], 'I am studying algebra.')
        self.assertEqual(contents[1]['parts'][0]['text'], 'You mentioned algebra.')
        self.assertContains(self.client.get(reverse('ai_assistant')), 'I am studying algebra.')
        self.assertContains(self.client.get(reverse('ai_assistant')), 'You said algebra.')

    def test_new_conversation_clears_session_history(self):
        session = self.client.session
        session['ai_chat_history'] = [
            {'role': 'user', 'text': 'Old question'},
            {'role': 'model', 'text': 'Old answer'},
        ]
        session.save()

        response = self.client.post(reverse('ai_assistant_clear'))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('ai_chat_history', self.client.session)

    @patch('pdf_converter.ai_support.requests.post')
    def test_document_qa_requires_consent_before_sending_file(self, post):
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'document',
            'message': 'Summarize this.',
            'document': SimpleUploadedFile('notes.txt', b'Private study notes.'),
        })

        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    @patch('pdf_converter.ai_support.requests.post')
    def test_document_qa_sends_extracted_text_after_consent(self, post):
        post.return_value = self.gemini_response

        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'document',
            'message': 'What is the main idea?',
            'document_consent': 'yes',
            'document': SimpleUploadedFile('notes.txt', b'The main idea is safe document processing.'),
        })

        self.assertEqual(response.status_code, 200)
        sent_prompt = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertIn('safe document processing', sent_prompt)

    def test_scheme_generator_validates_term_length(self):
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'scheme',
            'class_name': 'Grade 8',
            'subject': 'Science',
            'term': 'Term 1',
            'weeks': '17',
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn('1 to 16 weeks', response.json()['error'])

    @patch('pdf_converter.ai_support.extract_document_text', return_value='Official syllabus text.')
    @patch('revision.models.Curriculum.objects.filter')
    def test_kicd_syllabus_context_uses_matching_active_official_document(self, filter_curricula, extract_text):
        syllabus = Mock()
        syllabus.file = Mock(name='official.pdf')
        syllabus.subject.name = 'Applied Agriculture'
        syllabus.class_ref.name = 'Grade 10'
        syllabus.academic_year = '2026'
        syllabus.get_curriculum_type_display.return_value = 'CBC'
        filter_curricula.return_value.filter.return_value.select_related.return_value.order_by.return_value.first.return_value = syllabus

        text, label = get_kicd_syllabus_context('Grade 10', 'Applied Agriculture')

        self.assertEqual(text, 'Official syllabus text.')
        self.assertIn('Applied Agriculture', label)
        extract_text.assert_called_once_with(syllabus.file, max_pages=MAX_SYLLABUS_PAGES)
        filter_curricula.assert_called_once_with(
            is_active=True,
            class_ref__name__iexact='Grade 10',
            subject__name__iexact='Applied Agriculture',
        )
        filter_curricula.return_value.filter.assert_called_once_with(
            curriculum_type='cbc',
            class_ref__curriculum='cbe',
        )

    @patch('pdf_converter.views.get_kicd_syllabus_context', side_effect=ValueError('No active KICD syllabus.'))
    @patch('pdf_converter.ai_support.requests.post')
    def test_scheme_generator_requires_matching_official_syllabus(self, post, get_syllabus):
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'scheme',
            'class_name': 'Grade 10',
            'subject': 'Applied Agriculture',
            'term': 'Term 1',
            'weeks': '12',
            'topics': ['Agricultural land'],
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn('No active KICD syllabus', response.json()['error'])
        post.assert_not_called()
        get_syllabus.assert_called_once_with('Grade 10', 'Applied Agriculture')

    @patch('pdf_converter.views.get_kicd_syllabus_context', return_value=('KICD land syllabus.', 'Applied Agriculture — Grade 10, CBC, 2026'))
    @patch('pdf_converter.views.get_kicd_syllabus_topics', return_value=['Agricultural land', 'Crop production'])
    @patch('pdf_converter.ai_support.requests.post')
    def test_scheme_generator_requests_reference_table_format(self, post, get_topics, get_syllabus):
        gemini_response = Mock(status_code=200)
        gemini_response.json.return_value = {
            'candidates': [{'content': {'parts': [{
                'text': (
                    '1|1-2|Crop Production|Agricultural Land|Describe ways of accessing land|'
                    'How is land productivity determined?|Discuss ways to access land|'
                    'Resource person, manilla papers|Written test, oral questions|KICD'
                )
            }]}}]
        }
        post.return_value = gemini_response

        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'scheme',
            'class_name': 'Grade 10',
            'subject': 'Applied Agriculture',
            'term': 'Term 1',
            'weeks': '12',
            'topics': ['Agricultural land'],
            'message': 'Include land productivity and agricultural uses.',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        self.assertIn('.docx', response['Content-Disposition'])
        document = Document(io.BytesIO(response.content))
        scheme_table = document.tables[-1]
        self.assertEqual(len(scheme_table.rows[0].cells), 10)
        self.assertEqual(scheme_table.rows[1].cells[0].text, '1')
        self.assertEqual(scheme_table.rows[1].cells[1].text, '1-2')
        self.assertEqual(scheme_table.rows[1].cells[2].text, 'Crop Production')
        self.assertEqual(scheme_table.rows[1].cells[9].text, 'KICD')
        sent_prompt = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertIn('KICD land syllabus.', sent_prompt)
        self.assertIn('Applied Agriculture — Grade 10, CBC, 2026', sent_prompt)
        self.assertIn('Do not use remembered curriculum information', sent_prompt)
        self.assertIn('NAME OF THE TEACHER', sent_prompt)
        self.assertIn('Specific Learning Outcomes', sent_prompt)
        self.assertIn('Key Inquiry Question(s)', sent_prompt)
        self.assertIn('Learning Experiences', sent_prompt)
        self.assertIn('Assessment Methods', sent_prompt)
        self.assertIn('Selected curriculum topics:\nAgricultural land', sent_prompt)
        self.assertNotIn('Selected curriculum topics:\nAgricultural land\nCrop production', sent_prompt)
        get_topics.assert_called_once_with('Grade 10', 'Applied Agriculture')

    @patch('pdf_converter.views.get_kicd_syllabus_context', return_value=('KICD land syllabus.', 'Applied Agriculture — Grade 10, CBC, 2026'))
    @patch('pdf_converter.views.get_kicd_syllabus_topics', return_value=['Agricultural land'])
    @patch('pdf_converter.ai_support.requests.post')
    def test_scheme_generator_rejects_topics_not_in_the_selected_curriculum(
        self, post, get_topics, get_syllabus
    ):
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'scheme',
            'class_name': 'Grade 10',
            'subject': 'Applied Agriculture',
            'term': 'Term 1',
            'weeks': '12',
            'topics': ['Mathematics'],
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn('Choose topics from the active curriculum', response.json()['error'])
        post.assert_not_called()
        get_topics.assert_called_once_with('Grade 10', 'Applied Agriculture')

    @patch(
        'pdf_converter.views.get_kicd_syllabus_context',
        return_value=('Official agriculture syllabus content.', 'Applied Agriculture — Grade 10, CBC, 2026'),
    )
    @patch('pdf_converter.views.get_kicd_syllabus_topics', return_value=['Soil conservation', 'Crop rotation'])
    @patch('pdf_converter.ai_support.requests.post')
    def test_exam_builder_downloads_word_exam_with_separate_marking_scheme(self, post, get_topics, get_syllabus):
        gemini_response = Mock(status_code=200)
        gemini_response.json.return_value = {
            'candidates': [{'content': {'parts': [{
                'text': (
                    'EXAM PAPER\n'
                    '1. Explain two methods of conserving soil. (4 marks)\n'
                    '2. Describe crop rotation. (6 marks)\n'
                    'ANSWER KEY AND MARKING SCHEME\n'
                    '1. Contour farming (2); terracing (2).\n'
                    '2. Growing different crops in sequence to maintain soil fertility (6).'
                )
            }]}}]
        }
        post.return_value = gemini_response

        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'exam',
            'exam_class_name': 'Grade 10',
            'exam_subject': 'Applied Agriculture',
            'exam_topics': ['Soil conservation', 'Crop rotation'],
            'exam_difficulty': 'mixed',
            'exam_marks': '10',
            'exam_duration': '30',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        self.assertIn('Exam_AppliedAgriculture_Grade10.docx', response['Content-Disposition'])
        document = Document(io.BytesIO(response.content))
        document_text = '\n'.join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn('EXAM PAPER', document_text)
        self.assertIn('ANSWER KEY AND MARKING SCHEME', document_text)
        self.assertIn('1. Explain two methods of conserving soil.', document_text)
        self.assertIn('Contour farming', document_text)
        sent_prompt = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertIn('Official agriculture syllabus content.', sent_prompt)
        self.assertIn('Total marks: 10', sent_prompt)
        self.assertIn('Topics: Soil conservation, Crop rotation', sent_prompt)
        get_topics.assert_called_once_with('Grade 10', 'Applied Agriculture')
        self.assertEqual(get_syllabus.call_args.args, ('Grade 10', 'Applied Agriculture'))

    @patch(
        'pdf_converter.views.get_kicd_syllabus_context',
        return_value=('Official agriculture syllabus content.', 'Applied Agriculture — Grade 10, CBC, 2026'),
    )
    @patch('pdf_converter.views.get_kicd_syllabus_topics', return_value=['Soil conservation'])
    @patch('pdf_converter.ai_support.requests.post')
    def test_exam_builder_accepts_topic_wording_variations_and_reports_unmatched_topics(
        self, post, get_topics, get_syllabus
    ):
        gemini_response = Mock(status_code=200)
        gemini_response.json.return_value = {
            'candidates': [{'content': {'parts': [{
                'text': 'TOPIC NOT FOUND: No matching topic. Closest syllabus topic: soil conservation.'
            }]}}]
        }
        post.return_value = gemini_response

        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'exam',
            'exam_class_name': 'Grade 10',
            'exam_subject': 'Applied Agriculture',
            'exam_topics': ['Soil conservation'],
            'exam_difficulty': 'mixed',
            'exam_marks': '10',
            'exam_duration': '30',
        })

        self.assertEqual(response.status_code, 422)
        self.assertIn('Closest syllabus topic: soil conservation.', response.json()['error'])
        sent_prompt = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertIn('Do not require an exact wording match', sent_prompt)
        self.assertIn('correct obvious spelling or grammar errors', sent_prompt)
        get_syllabus.assert_called_once_with('Grade 10', 'Applied Agriculture')
        get_topics.assert_called_once_with('Grade 10', 'Applied Agriculture')

    @patch('pdf_converter.ai_support.requests.post')
    def test_exam_builder_validates_mark_limit(self, post):
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'exam',
            'exam_class_name': 'Grade 10',
            'exam_subject': 'Applied Agriculture',
            'exam_topics': ['Soil conservation'],
            'exam_marks': '250',
            'exam_duration': '60',
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn('Exam marks must be 10–200', response.json()['error'])
        post.assert_not_called()

    @patch('pdf_converter.views.get_kicd_syllabus_context', return_value=('Syllabus.', 'Official syllabus'))
    @patch('pdf_converter.views.get_kicd_syllabus_topics', return_value=['Soil conservation'])
    @patch('pdf_converter.ai_support.requests.post')
    def test_exam_builder_rejects_topics_not_in_the_selected_curriculum(
        self, post, get_topics, get_syllabus
    ):
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'exam',
            'exam_class_name': 'Grade 10',
            'exam_subject': 'Applied Agriculture',
            'exam_topics': ['Mathematics'],
            'exam_marks': '50',
            'exam_duration': '60',
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn('Choose topics from the active curriculum', response.json()['error'])
        post.assert_not_called()
        get_topics.assert_called_once_with('Grade 10', 'Applied Agriculture')

    @patch('pdf_converter.ai_support.requests.post')
    def test_missing_model_returns_actionable_configuration_error(self, post):
        post.return_value = Mock(status_code=404)

        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'chat',
            'message': 'Hello',
        })

        self.assertEqual(response.status_code, 503)
        self.assertIn('gemini-3.5-flash-lite', response.json()['error'])

    @patch('pdf_converter.ai_support.requests.post')
    def test_gemini_service_unavailable_returns_temporary_outage_message(self, post):
        post.return_value = Mock(status_code=503)

        with self.assertRaises(AIServiceError) as error:
            generate_ai_response('Hello')

        self.assertIn('temporarily unavailable', str(error.exception))
        self.assertIn('try again in a few minutes', str(error.exception))


@override_settings(GEMINI_API_KEY='')
class GeminiConfigurationTests(SimpleTestCase):
    def test_ai_request_reports_missing_gemini_configuration(self):
        response = self.client.post(reverse('ai_assistant_generate'), {
            'mode': 'chat',
            'message': 'Hello',
        })

        self.assertEqual(response.status_code, 503)
        self.assertIn('GEMINI_API_KEY', response.json()['error'])
