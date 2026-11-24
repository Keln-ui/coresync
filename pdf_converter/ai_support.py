import logging
import os

import requests
from django.conf import settings


logger = logging.getLogger(__name__)

MAX_DOCUMENT_CHARS = 40000
MAX_DOCUMENT_PAGES = 50
MAX_SYLLABUS_PAGES = 300

CORESYNCH_SERVICES_CONTEXT = """
CoreSynch is an education and document-services platform. Use the information below when users
ask what the system offers or how to navigate it. Do not invent features not listed here.

Academic and learning services:
- Revision Library: browse and search Kenyan CBC and 8-4-4 revision materials, subjects, grades,
  class resources, schemes, notes, past papers, marking schemes, and downloadable study materials.
- Scheme of Work Generator: create teacher-facing, week-by-week schemes of work and export editable
  Microsoft Word documents; users can select class or grade, subject, term, and term length.
- AI Assistant: provide general guidance, explain or summarize study topics, generate practice
  quizzes, answer questions about PDF/DOCX/TXT documents, and draft scheme-of-work support.
- Teaching opportunities: browse BOM school teaching vacancies and homeschooling or private tutoring
  jobs, and submit job advertisements through the job posting page.
- School and user registration: register a school or individual teacher/student account, manage
  profile and account details, and access user dashboards.
- Subscription plans: view plans and subscribe for service access through the available payment flow.

Document converter services:
- PDF conversion: convert PDF or image files to editable DOCX, TXT, HTML, JSON, CSV, and PPTX
  formats, with OCR for scanned documents and images.
- Free converters: convert DOCX Word documents to PDF and convert images to PDF.
- PDF utilities: merge PDFs, split PDFs, reorder or organize pages, extract selected pages,
  compress PDFs, rotate pages, resize pages, crop margins, add text watermarks, flatten PDFs,
  password-protect PDFs, unlock password-protected PDFs, and extract embedded images.
- Conversion history: signed-in users can review their previous conversion activity.

Navigation guidance:
- Use the Home link for the academic homepage.
- Use Revision Library for learning materials and Search for revision resources.
- Use Scheme of Work for lesson planning.
- Use AI Assistant for study and document help.
- Use Document Studio or the converter page for conversions and PDF tools.
- Use Pricing for subscription plans and Profile or Dashboard for account information.
""".strip()


class AIServiceError(Exception):
    pass


def extract_document_text(uploaded_file, max_pages=MAX_DOCUMENT_PAGES):
    extension = os.path.splitext(uploaded_file.name)[1].lower()
    if extension not in {'.pdf', '.docx', '.txt'}:
        raise ValueError('Upload a PDF, DOCX, or TXT document.')
    if uploaded_file.size > 10 * 1024 * 1024:
        raise ValueError('Documents must be 10 MB or smaller.')

    try:
        if extension == '.txt':
            text = uploaded_file.read().decode('utf-8-sig')
        elif extension == '.docx':
            from docx import Document

            document = Document(uploaded_file)
            parts = [paragraph.text for paragraph in document.paragraphs]
            parts.extend(
                cell.text
                for table in document.tables
                for row in table.rows
                for cell in row.cells
            )
            text = '\n'.join(parts)
        else:
            from PyPDF2 import PdfReader

            reader = PdfReader(uploaded_file, strict=False)
            if len(reader.pages) > max_pages:
                raise ValueError(f'Documents can contain up to {max_pages} pages.')
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('The document could not be read. Check the file and try again.') from exc

    text = text.strip()
    if not text:
        raise ValueError('No readable text was found in the document.')
    return text[:MAX_DOCUMENT_CHARS]


def get_kicd_syllabus_context(class_name, subject_name):
    from revision.models import Curriculum

    curricula = Curriculum.objects.filter(
        is_active=True,
        class_ref__name__iexact=class_name,
        subject__name__iexact=subject_name,
    )
    normalized_class = class_name.strip().lower()
    if normalized_class.startswith('grade'):
        curricula = curricula.filter(curriculum_type='cbc', class_ref__curriculum='cbe')
    elif normalized_class.startswith('form'):
        curricula = curricula.filter(curriculum_type='844', class_ref__curriculum='844')

    syllabus = curricula.select_related('class_ref', 'subject').order_by(
        '-academic_year', '-uploaded_at'
    ).first()
    if not syllabus:
        raise ValueError(
            'No active KICD syllabus is available for this class and subject. '
            'Ask an administrator to upload and activate the official syllabus in Django Admin.'
        )

    if not syllabus.file:
        raise ValueError('The stored KICD syllabus has no readable file. Ask an administrator to replace it.')
    try:
        syllabus.file.open('rb')
        try:
            syllabus_text = extract_document_text(syllabus.file, max_pages=MAX_SYLLABUS_PAGES)
        finally:
            syllabus.file.close()
    except ValueError as exc:
        raise ValueError(f'The stored KICD syllabus could not be used: {exc}') from exc
    except OSError as exc:
        raise ValueError('The stored KICD syllabus file could not be opened. Ask an administrator to replace it.') from exc

    source_label = (
        f"{syllabus.subject.name} — {syllabus.class_ref.name}, "
        f"{syllabus.get_curriculum_type_display()}, {syllabus.academic_year}"
    )
    return syllabus_text, source_label


def build_ai_exam_docx(
    class_name,
    subject_name,
    topics,
    difficulty,
    total_marks,
    duration_minutes,
    syllabus_label,
    exam_paper,
    marking_scheme,
):
    import io

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)

    title = document.add_heading(f'{subject_name.upper()} EXAMINATION', level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = document.add_paragraph(f'{class_name} | {difficulty.title()} | {total_marks} marks')
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    duration = document.add_paragraph(f'Duration: {duration_minutes} minutes')
    duration.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph(f'Topics: {topics}')
    document.add_paragraph(f'Syllabus source: {syllabus_label}')
    document.add_paragraph('Name: ____________________________________    Date: ____________________')
    document.add_paragraph()
    document.add_heading('EXAM PAPER', level=1)
    for line in exam_paper.splitlines():
        document.add_paragraph(line)

    document.add_page_break()
    document.add_heading('ANSWER KEY AND MARKING SCHEME', level=1)
    for line in marking_scheme.splitlines():
        document.add_paragraph(line)

    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            run.font.name = 'Aptos'
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(40, 52, 72)

    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


def generate_ai_response(prompt, history=None):
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        raise AIServiceError('AI support is not configured yet. Add GEMINI_API_KEY to the server environment.')

    model = getattr(settings, 'GEMINI_MODEL', 'gemini-3.5-flash-lite')
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    contents = [
        {'role': turn['role'], 'parts': [{'text': turn['text']}]}
        for turn in (history or [])
    ]
    contents.append({'role': 'user', 'parts': [{'text': prompt}]})
    try:
        response = requests.post(
            url,
            headers={'x-goog-api-key': api_key},
            json={
                'contents': contents,
                'generationConfig': {'temperature': 0.4, 'maxOutputTokens': 4096},
            },
            timeout=45,
        )
    except requests.RequestException as exc:
        raise AIServiceError('Could not reach Gemini. Please try again shortly.') from exc

    if response.status_code == 429:
        raise AIServiceError('Gemini is temporarily rate-limiting requests. Please try again shortly.')
    if response.status_code in {401, 403}:
        raise AIServiceError('Gemini rejected the API credentials. Check the key and its Gemini API access.')
    if response.status_code == 404:
        raise AIServiceError(
            'Gemini could not find the configured model. Check GEMINI_MODEL; '
            'the default is gemini-3.5-flash-lite.'
        )
    if response.status_code in {500, 502, 503, 504}:
        logger.warning('Gemini is temporarily unavailable (HTTP %s).', response.status_code)
        raise AIServiceError('Gemini is temporarily unavailable. Please try again in a few minutes.')
    if response.status_code >= 400:
        logger.warning('Gemini API returned HTTP %s.', response.status_code)
        raise AIServiceError('Gemini could not complete this request. Check the API configuration and try again.')

    try:
        data = response.json()
    except ValueError as exc:
        raise AIServiceError('Gemini returned an unreadable response. Please try again.') from exc

    candidates = data.get('candidates') or []
    parts = (candidates[0].get('content') or {}).get('parts') or [] if candidates else []
    answer = '\n'.join(part.get('text', '') for part in parts if part.get('text')).strip()
    if not answer:
        raise AIServiceError('Gemini did not return an answer. Please rephrase your request.')
    return answer
