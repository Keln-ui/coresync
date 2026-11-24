import io
import re
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn


def _set_cell_background(cell, fill_hex):
    """Set background color of a table cell."""
    shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    cell._tc.get_or_add_tcPr().append(shading_elm)


def _set_cell_margins(cell, top=80, bottom=80, left=100, right=100):
    """Set inner cell padding/margins."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        node = OxmlElement(f'w:{m}')
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)


def _repeat_table_header(row):
    row_properties = row._tr.get_or_add_trPr()
    repeat = OxmlElement('w:tblHeader')
    repeat.set(qn('w:val'), 'true')
    row_properties.append(repeat)


def _prevent_row_split(row):
    row_properties = row._tr.get_or_add_trPr()
    row_properties.append(OxmlElement('w:cantSplit'))


def _add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("PAGE ")
    run.font.size = Pt(8)
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    paragraph._p.append(field)


def _insert_special_weeks_table(document, special_weeks):
    events = [
        (week, label)
        for week, labels in (special_weeks or {}).items()
        for label in labels
    ]
    if not events:
        return None

    events.sort(key=lambda item: int(item[0]))
    table = document.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Inches(1.0)
    table.columns[1].width = Inches(9.7)
    header = table.rows[0]
    _repeat_table_header(header)
    for cell, label in zip(header.cells, ("WEEK", "KEY TERM EVENT")):
        _set_cell_background(cell, "24436B")
        _set_cell_margins(cell, top=90, bottom=90, left=120, right=120)
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run(label)
        run.bold = True
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(255, 255, 255)
    for index, (week, label) in enumerate(events):
        row = table.add_row()
        _prevent_row_split(row)
        for cell, value in zip(row.cells, (f"Week {week}", label)):
            _set_cell_background(cell, "F3F7FC" if index % 2 else "FFFFFF")
            _set_cell_margins(cell, top=80, bottom=80, left=120, right=120)
            cell.paragraphs[0].add_run(value)

    return table


def _week_event_text(labels):
    display_names = {
        'reporting week': 'REPORTING WEEK',
        'mid-break week': 'MID TERM BREAK',
        'exams': 'EXAMS',
        'closing week': 'CLOSING WEEK',
    }
    return " / ".join(display_names.get(label.lower(), label.upper()) for label in labels)


def _add_merged_event_row(table, week_number, labels):
    row = table.add_row()
    _prevent_row_split(row)
    cells = row.cells
    cells[0].text = str(week_number)
    merged_cell = cells[1].merge(cells[-1])
    merged_cell.text = _week_event_text(labels)
    for cell in (cells[0], merged_cell):
        _set_cell_background(cell, "EAF0FA")
        _set_cell_margins(cell, top=115, bottom=115, left=110, right=110)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(0)
        for run in paragraph.runs:
            run.bold = True
            run.font.name = 'Aptos'
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(37, 60, 92)
    return row


def _row_contains_week(text, week_number):
    numbers = [int(value) for value in re.findall(r'\d+', text)]
    if not numbers:
        return False
    if len(numbers) == 1:
        return numbers[0] == week_number
    if len(numbers) == 2 and re.fullmatch(r'\s*\d+\s*[-–]\s*\d+\s*', text):
        return numbers[0] <= week_number <= numbers[1]
    return week_number in numbers


def _reporting_start_week(special_weeks):
    for week_number, labels in (special_weeks or {}).items():
        if any(label.strip().lower() == 'reporting week' for label in labels):
            return int(week_number)
    return None


def _apply_special_weeks_to_tables(document, special_weeks):
    if not special_weeks:
        return False

    schedule_table = None
    for candidate in document.tables:
        if not candidate.rows or len(candidate.columns) < 2:
            continue
        headings = [cell.text.strip().upper() for cell in candidate.rows[0].cells]
        first_heading = headings[0] if headings else ""
        if first_heading in ("WEEK", "WK") or (
            first_heading == "K" and "LSN" in headings and "STRAND" in headings
        ):
            schedule_table = candidate
            break
    if schedule_table is None:
        return False

    reporting_start = _reporting_start_week(special_weeks)
    if reporting_start is not None:
        for row in list(schedule_table.rows[1:]):
            week_values = [int(value) for value in re.findall(r'\d+', row.cells[0].text)]
            if week_values and max(week_values) < reporting_start:
                parent = row._tr.getparent()
                if parent is not None:
                    parent.remove(row._tr)

    for week_number, labels in sorted(special_weeks.items()):
        rows = list(schedule_table.rows[1:])
        matching = [
            row for row in rows
            if _row_contains_week(row.cells[0].text.strip(), int(week_number))
        ]
        if matching:
            anchor = matching[0]._tr
        else:
            later_rows = []
            for row in rows:
                row_numbers = [int(value) for value in re.findall(r'\d+', row.cells[0].text)]
                if row_numbers and min(row_numbers) > int(week_number):
                    later_rows.append((min(row_numbers), row._tr))
            anchor = min(later_rows, key=lambda item: item[0])[1] if later_rows else None

        event_row = schedule_table.add_row()
        if anchor is not None:
            anchor.addprevious(event_row._tr)
        _prevent_row_split(event_row)
        cells = event_row.cells
        cells[0].text = str(week_number)
        merged_cell = cells[1].merge(cells[-1])
        merged_cell.text = _week_event_text(labels)
        for cell in (cells[0], merged_cell):
            _set_cell_background(cell, "EAF0FA")
            _set_cell_margins(cell, top=115, bottom=115, left=110, right=110)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in paragraph.runs:
                run.bold = True
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(37, 60, 92)

        for row in matching:
            parent = row._tr.getparent()
            if parent is not None:
                parent.remove(row._tr)
    return True


def personalize_uploaded_scheme_docx(
    file_path,
    school_name,
    school_address="",
    school_phone="",
    school_email="",
    principal_name="",
    class_name="",
    subject_name="",
    term="",
    academic_year="",
    special_weeks=None,
):
    """Add a branded first-page header and selected scheme details to an uploaded DOCX."""
    document = docx.Document(file_path)
    header = document.add_table(rows=1, cols=1)
    header_cell = header.cell(0, 0)
    _set_cell_background(header_cell, "172B4D")
    _set_cell_margins(header_cell, top=190, bottom=180, left=240, right=240)
    paragraph = header_cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title = paragraph.add_run((school_name or "SCHOOL NAME").upper())
    title.bold = True
    title.font.size = Pt(18)
    title.font.color.rgb = RGBColor(255, 255, 255)
    subtitle = header_cell.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(0)
    line = subtitle.add_run(f"SCHEME OF WORK  /  {term.upper()} {academic_year}".strip())
    line.bold = True
    line.font.size = Pt(10)
    line.font.color.rgb = RGBColor(190, 220, 255)

    labels = [
        ("CLASS / GRADE", class_name),
        ("SUBJECT", subject_name),
        ("TERM", term),
        ("YEAR", academic_year),
    ]
    metadata = document.add_table(rows=1, cols=len(labels))
    metadata.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, (label, value) in zip(metadata.rows[0].cells, labels):
        _set_cell_background(cell, "EAF0FA")
        _set_cell_margins(cell, top=100, bottom=100, left=110, right=110)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        label_run = p.add_run(f"{label}\n")
        label_run.bold = True
        label_run.font.size = Pt(7)
        label_run.font.color.rgb = RGBColor(92, 107, 128)
        value_run = p.add_run(value or "—")
        value_run.bold = True
        value_run.font.size = Pt(9)
        value_run.font.color.rgb = RGBColor(31, 52, 83)

    page_break = document.add_page_break()
    if not _apply_special_weeks_to_tables(document, special_weeks):
        events_table = _insert_special_weeks_table(document, special_weeks)
    else:
        events_table = None
    body = document._body._element
    elements = [header._tbl, metadata._tbl, page_break._p]
    if events_table:
        elements.append(events_table._tbl)
    for index, element in enumerate(elements):
        body.remove(element)
        body.insert(index, element)

    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer


def build_scheme_of_work_docx(
    school_name="CORE SYNCH ACADEMY",
    school_address="",
    school_phone="",
    school_email="",
    principal_name="",
    class_name="Form 4 / Grade 9",
    subject_name="Mathematics",
    term="Term 1",
    academic_year="2025",
    weeks_count=12,
    custom_content="",
    topics=None,
    objectives="",
    special_weeks=None,
):
    """Generate a high-quality, printable Microsoft Word (.docx) Scheme of Work."""
    doc = docx.Document()
    
    # Page setup: A4 landscape for printable school planning tables.
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Inches(11.69)
    section.page_height = Inches(8.27)
    section.top_margin = Inches(0.42)
    section.bottom_margin = Inches(0.48)
    section.left_margin = Inches(0.42)
    section.right_margin = Inches(0.42)

    normal_style = doc.styles['Normal']
    normal_style.font.name = 'Aptos'
    normal_style.font.size = Pt(8.5)
    normal_style.font.color.rgb = RGBColor(40, 52, 72)

    header_table = doc.add_table(rows=1, cols=1)
    header_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    header_cell = header_table.cell(0, 0)
    _set_cell_background(header_cell, "172B4D")
    _set_cell_margins(header_cell, top=200, bottom=190, left=250, right=250)
    header_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p_school = header_cell.paragraphs[0]
    p_school.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_school.paragraph_format.space_after = Pt(4)
    run_school = p_school.add_run((school_name or "SCHOOL NAME").upper())
    run_school.bold = True
    run_school.font.name = 'Aptos Display'
    run_school.font.size = Pt(20)
    run_school.font.color.rgb = RGBColor(255, 255, 255)
    p_title = header_cell.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_after = Pt(0)
    run_title = p_title.add_run(f"SCHEME OF WORK  /  {term.upper()} {academic_year or '2025'}")
    run_title.bold = True
    run_title.font.size = Pt(11)
    run_title.font.color.rgb = RGBColor(190, 220, 255)

    school_details = [value for value in (school_address, "  |  ".join(v for v in (school_phone, school_email) if v)) if value]
    if principal_name:
        school_details.append(f"Principal: {principal_name}")
    if school_details:
        p_detail = doc.add_paragraph()
        p_detail.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_detail.paragraph_format.space_before = Pt(3)
        p_detail.paragraph_format.space_after = Pt(5)
        run_detail = p_detail.add_run("  •  ".join(school_details))
        run_detail.font.size = Pt(8)
        run_detail.font.color.rgb = RGBColor(92, 107, 128)
    
    # Metadata summary table
    meta_table = doc.add_table(rows=2, cols=4)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_table.autofit = True
    
    meta_data = [
        [
            ("CLASS / GRADE", class_name or "N/A"),
            ("SUBJECT", subject_name or "N/A"),
            ("TERM", term or "N/A"),
            ("YEAR", academic_year or "2025"),
        ],
        [
            ("WEEKS", str(weeks_count)),
            ("TEACHER", "____________________"),
            ("TSC NO", "________________"),
            ("SIGN", "_________________"),
        ]
    ]
    for r_idx, row_items in enumerate(meta_data):
        for c_idx, (label, value) in enumerate(row_items):
            cell = meta_table.cell(r_idx, c_idx)
            _set_cell_background(cell, "EAF0FA" if r_idx == 0 else "F7F9FC")
            _set_cell_margins(cell, top=95, bottom=95, left=130, right=130)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.space_before = Pt(0)
            label_run = p.add_run(f"{label}\n")
            label_run.font.size = Pt(7)
            label_run.font.bold = True
            label_run.font.color.rgb = RGBColor(92, 107, 128)
            value_run = p.add_run(value)
            value_run.font.size = Pt(9)
            value_run.font.bold = True
            value_run.font.color.rgb = RGBColor(31, 52, 83)
    
    p_space = doc.add_paragraph()
    p_space.paragraph_format.space_before = Pt(2)
    p_space.paragraph_format.space_after = Pt(2)
    doc.add_page_break()

    # Parse custom content into weekly breakdown
    parsed_weeks = []
    if custom_content and "Week " in custom_content:
        current_week = None
        current_data = []
        for line in custom_content.splitlines():
            line_str = line.strip()
            if line_str.startswith("Week ") and ":" in line_str:
                if current_week is not None:
                    parsed_weeks.append((current_week, "\n".join(current_data)))
                current_week = line_str.split(":")[0].strip()
                current_data = [line_str.split(":", 1)[1].strip()] if len(line_str.split(":", 1)) > 1 else []
            elif current_week is not None and line_str:
                current_data.append(line_str)
        if current_week is not None:
            parsed_weeks.append((current_week, "\n".join(current_data)))
    
    if not parsed_weeks:
        for w in range(1, weeks_count + 1):
            topic = topics[w - 1] if topics and len(topics) >= w else f"Curriculum Focus & Practical Applications"
            parsed_weeks.append((f"Week {w}", topic))

    present_weeks = set()
    for label, content in parsed_weeks:
        match = re.search(r'\d+', label)
        if match:
            present_weeks.add(int(match.group()))
    for week_number in (special_weeks or {}):
        if int(week_number) not in present_weeks:
            parsed_weeks.append((f"Week {week_number}", ""))
    reporting_start = _reporting_start_week(special_weeks)
    if reporting_start is not None:
        parsed_weeks = [
            item for item in parsed_weeks
            if not (re.search(r'\d+', item[0]) and int(re.search(r'\d+', item[0]).group()) < reporting_start)
        ]
    parsed_weeks.sort(key=lambda item: int(re.search(r'\d+', item[0]).group()))
    
    # Main Scheme Table
    draft_rows = []
    for line in (custom_content or "").splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) == 10 and not fields[0].lower().startswith("wk"):
            draft_rows.append(fields)

    table = doc.add_table(rows=1, cols=10)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    col_widths = [
        Inches(0.4),
        Inches(0.45),
        Inches(1.05),
        Inches(1.1),
        Inches(1.8),
        Inches(1.25),
        Inches(1.85),
        Inches(1.2),
        Inches(1.3),
        Inches(0.4),
    ]
    headers = [
        "Wk",
        "Lsn",
        "Strand",
        "Sub-strand",
        "Specific Learning Outcomes",
        "Key Inquiry Question(s)",
        "Learning Experiences",
        "Learning Resources",
        "Assessment Methods",
        "Ref",
    ]
    
    hdr_cells = table.rows[0].cells
    _repeat_table_header(table.rows[0])
    _prevent_row_split(table.rows[0])
    for i, title in enumerate(headers):
        hdr_cells[i].text = title
        hdr_cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        _set_cell_background(hdr_cells[i], "24436B")
        _set_cell_margins(hdr_cells[i], top=115, bottom=115, left=90, right=90)
        p = hdr_cells[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        for r in p.runs:
            r.font.bold = True
            r.font.name = 'Aptos'
            r.font.size = Pt(7.5)
            r.font.color.rgb = RGBColor(255, 255, 255)
    
    # Add Rows
    if draft_rows:
        display_rows = [
            (row[0], row)
            for row in draft_rows
        ]
    else:
        display_rows = parsed_weeks

    for idx, (wk_label, content_value) in enumerate(display_rows):
        week_match = re.search(r'\d+', wk_label)
        week_number = int(week_match.group()) if week_match else None
        week_events = (special_weeks or {}).get(week_number, []) if week_number is not None else []
        if week_events:
            _add_merged_event_row(table, week_number, week_events)
            continue

        row_cells = table.add_row().cells
        _prevent_row_split(table.rows[-1])
        bg_color = "F3F7FC" if idx % 2 == 1 else "FFFFFF"

        if draft_rows:
            row_data = content_value
        else:
            # Clean lines from content
            clean_lines = [l.strip().lstrip("-•* ") for l in content_value.splitlines() if l.strip()]
            topic_summary = clean_lines[0] if clean_lines else f"{subject_name} Unit {idx+1}"

            if len(clean_lines) > 1:
                outcomes_list = "\n• " + "\n• ".join(clean_lines[1:4])
            else:
                outcomes_list = (
                    f"By the end of the lesson, the learner should be able to:\n"
                    f"• Explain fundamental concepts of {topic_summary}\n"
                    f"• Solve standard practical exercises\n"
                    f"• Demonstrate mastery through class evaluation"
                )

            activities = (
                "• Teacher-led demonstration & discussion\n"
                "• Guided group activities & exercises\n"
                "• Individual task presentation & reflection"
            )
            resources = "• KICD Approved Course Book\n• Teachers Guide\n• Realia & Charts"
            remarks = "Achieved [  ]\nIn progress [  ]"
            row_data = [
                wk_label,
                "",
                topic_summary,
                "Key Skills & Competencies",
                outcomes_list,
                "",
                activities,
                resources,
                remarks,
                "",
            ]
        
        for c_idx, text in enumerate(row_data):
            cell = row_cells[c_idx]
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_background(cell, bg_color)
            _set_cell_margins(cell, top=105, bottom=105, left=90, right=90)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.text = text
            for run in p.runs:
                run.font.name = 'Aptos'
                run.font.size = Pt(7.5)
                if c_idx == 0:
                    run.font.bold = True
                    run.font.color.rgb = RGBColor(37, 84, 150)
            if c_idx == 0:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Set cell widths
    for row in table.rows:
        for idx, width in enumerate(col_widths):
            row.cells[idx].width = width
    
    # Apply modern subtle table borders
    tblPr = table._tbl.tblPr
    tblBorders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>\n'
        f'  <w:top w:val="single" w:sz="5" w:space="0" w:color="C9D5E4"/>\n'
        f'  <w:left w:val="single" w:sz="5" w:space="0" w:color="C9D5E4"/>\n'
        f'  <w:bottom w:val="single" w:sz="5" w:space="0" w:color="C9D5E4"/>\n'
        f'  <w:right w:val="single" w:sz="5" w:space="0" w:color="C9D5E4"/>\n'
        f'  <w:insideH w:val="single" w:sz="4" w:space="0" w:color="DCE4EF"/>\n'
        f'  <w:insideV w:val="single" w:sz="4" w:space="0" w:color="DCE4EF"/>\n'
        f'</w:tblBorders>'
    )
    tblPr.append(tblBorders)

    footer = section.footer.paragraphs[0]
    footer.text = "TEACHING & LEARNING  •  SCHEME OF WORK"
    footer.runs[0].font.size = Pt(7)
    footer.runs[0].font.bold = True
    footer.runs[0].font.color.rgb = RGBColor(110, 125, 145)
    page_number = section.footer.add_paragraph()
    _add_page_number(page_number)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
