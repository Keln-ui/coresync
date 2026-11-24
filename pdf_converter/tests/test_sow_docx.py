import io
import os
import tempfile

from docx import Document
from django.test import SimpleTestCase

from pdf_converter.sow_docx import (
    build_scheme_of_work_docx,
    personalize_uploaded_scheme_docx,
)


class SchemeOfWorkDocumentTests(SimpleTestCase):
    def test_generated_document_has_branded_heading_and_repeating_weekly_table(self):
        output = build_scheme_of_work_docx(
            school_name='Muhu Senior School',
            class_name='Grade 10',
            subject_name='Arabic',
            term='Term 1',
            academic_year='2027',
            weeks_count=2,
            custom_content='Week 1:\nNumbers\nWeek 2:\nAlgebra',
        )

        document = Document(output)

        self.assertIn('MUHU SENIOR SCHOOL', document.tables[0].cell(0, 0).text)
        self.assertIn('SCHEME OF WORK  /  TERM 1 2027', document.tables[0].cell(0, 0).text)
        metadata = document.tables[1]
        self.assertIn('CLASS / GRADE\nGrade 10', metadata.cell(0, 0).text)
        self.assertIn('SUBJECT\nArabic', metadata.cell(0, 1).text)
        self.assertIn('TERM\nTerm 1', metadata.cell(0, 2).text)
        self.assertIn('YEAR\n2027', metadata.cell(0, 3).text)
        body_elements = list(document._body._element)
        schedule_index = body_elements.index(document.tables[-1]._tbl)
        page_break_indexes = [
            index for index, element in enumerate(body_elements)
            if element.xpath('.//w:br[@w:type="page"]')
        ]
        self.assertEqual(len(page_break_indexes), 1)
        self.assertLess(body_elements.index(metadata._tbl), page_break_indexes[0])
        self.assertLess(page_break_indexes[0], schedule_index)
        self.assertEqual(len(document.tables[-1].rows), 3)
        self.assertEqual(
            document.tables[-1].rows[0]._tr.trPr.find(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader'
            ).get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val'),
            'true',
        )

    def test_uploaded_docx_personalization_adds_school_and_selected_metadata(self):
        original = Document()
        original.add_paragraph('Existing scheme content')
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, 'source.docx')
            original.save(source_path)
            output = personalize_uploaded_scheme_docx(
                source_path,
                'Muhu Senior School',
                class_name='Grade 9',
                subject_name='Mathematics',
                term='Term 1',
                academic_year='2026',
                special_weeks={3: ['Reporting week', 'Mid-break week']},
            )

        document = Document(io.BytesIO(output.getvalue()))

        self.assertIn('MUHU SENIOR SCHOOL', document.tables[0].cell(0, 0).text)
        metadata = document.tables[1]
        self.assertIn('Grade 9', metadata.cell(0, 0).text)
        self.assertIn('Mathematics', metadata.cell(0, 1).text)
        self.assertIn('2026', metadata.cell(0, 3).text)
        self.assertIn('Reporting week', document.tables[2].cell(1, 1).text)
        self.assertIn('Mid-break week', document.tables[2].cell(2, 1).text)
        body_elements = list(document._body._element)
        metadata_index = body_elements.index(metadata._tbl)
        page_break_index = next(
            index for index, element in enumerate(body_elements)
            if element.xpath('.//w:br[@w:type="page"]')
        )
        content_index = next(
            index for index, element in enumerate(body_elements)
            if element.xpath('.//w:t[text()="Existing scheme content"]')
        )
        self.assertLess(metadata_index, page_break_index)
        self.assertLess(page_break_index, content_index)

    def test_selected_term_weeks_are_included_and_marked_in_generated_document(self):
        output = build_scheme_of_work_docx(
            school_name='Muhu Senior School',
            class_name='Grade 9',
            subject_name='Mathematics',
            term='Term 1',
            academic_year='2026',
            weeks_count=2,
            custom_content='Week 1:\nNumbers\nWeek 2:\nAlgebra',
            special_weeks={1: ['Reporting week'], 2: ['Exams', 'Closing week']},
        )

        document = Document(output)
        document_text = '\n'.join(
            [paragraph.text for paragraph in document.paragraphs]
            + [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        )

        self.assertIn('REPORTING WEEK', document_text)
        self.assertIn('EXAMS', document_text)
        self.assertIn('CLOSING WEEK', document_text)
        event_row = document.tables[-1].rows[1]
        self.assertEqual(event_row.cells[0].text, '1')
        self.assertEqual(event_row.cells[1].text, 'REPORTING WEEK')
        self.assertEqual(len({id(cell._tc) for cell in event_row.cells[1:]}), 1)

    def test_uploaded_scheme_replaces_selected_week_with_merged_event_row(self):
        original = Document()
        table = original.add_table(rows=1, cols=10)
        headings = ('K', 'LSN', 'STRAND', 'SUB-STRAND', 'OUTCOMES', 'ACTIVITIES', 'QUESTIONS', 'RESOURCES', 'ASSESSMENT', 'REFLECTION')
        for cell, value in zip(table.rows[0].cells, headings):
            cell.text = value
        for week in (1, 2, 3):
            cells = table.add_row().cells
            cells[0].text = str(week)
            cells[1].text = '1'
            cells[2].text = f'Topic {week}'
            cells[3].text = 'Sub-topic'
            cells[4].text = 'Outcome'
            cells[5].text = 'Lesson activity'
            cells[8].text = 'Assessment'

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, 'weekly-scheme.docx')
            original.save(source_path)
            output = personalize_uploaded_scheme_docx(
                source_path,
                'Muhu Senior School',
                class_name='Grade 9',
                subject_name='Science',
                term='Term 1',
                academic_year='2026',
                special_weeks={2: ['Mid-break week']},
            )

        personalized = Document(io.BytesIO(output.getvalue()))
        schedule_table = personalized.tables[2]
        self.assertEqual(len(schedule_table.rows), 4)
        event_row = schedule_table.rows[2]
        self.assertEqual(event_row.cells[0].text, '2')
        self.assertEqual(event_row.cells[1].text, 'MID TERM BREAK')
        self.assertEqual(len({id(cell._tc) for cell in event_row.cells[1:]}), 1)

    def test_reporting_week_removes_earlier_weeks_from_generated_scheme(self):
        output = build_scheme_of_work_docx(
            school_name='Muhu Senior School',
            class_name='Grade 9',
            subject_name='Science',
            term='Term 1',
            academic_year='2026',
            weeks_count=4,
            custom_content='\n'.join(f'Week {week}:\nTopic {week}' for week in range(1, 5)),
            special_weeks={3: ['Reporting week']},
        )

        schedule_table = Document(output).tables[-1]
        self.assertEqual(len(schedule_table.rows), 3)
        self.assertEqual(schedule_table.rows[1].cells[0].text, '3')
        self.assertEqual(schedule_table.rows[1].cells[1].text, 'REPORTING WEEK')
        self.assertEqual(schedule_table.rows[2].cells[0].text, 'Week 4')

    def test_reporting_week_removes_earlier_uploaded_scheme_rows(self):
        original = Document()
        table = original.add_table(rows=1, cols=3)
        for cell, value in zip(table.rows[0].cells, ('WEEK', 'TOPIC', 'ACTIVITIES')):
            cell.text = value
        for week in range(1, 5):
            cells = table.add_row().cells
            cells[0].text = str(week)
            cells[1].text = f'Topic {week}'
            cells[2].text = 'Lesson activity'

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, 'weekly-scheme.docx')
            original.save(source_path)
            output = personalize_uploaded_scheme_docx(
                source_path,
                'Muhu Senior School',
                class_name='Grade 9',
                subject_name='Science',
                term='Term 1',
                academic_year='2026',
                special_weeks={3: ['Reporting week']},
            )

        schedule_table = Document(io.BytesIO(output.getvalue())).tables[2]
        self.assertEqual(len(schedule_table.rows), 3)
        self.assertEqual(schedule_table.rows[1].cells[0].text, '3')
        self.assertEqual(schedule_table.rows[1].cells[1].text, 'REPORTING WEEK')
        self.assertEqual(schedule_table.rows[2].cells[0].text, '4')
