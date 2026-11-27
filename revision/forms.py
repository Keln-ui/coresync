import csv
import io
import os
import stat
import zipfile
import zlib
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from django import forms
from django.core.exceptions import ValidationError
from .models import (
    Class,
    Curriculum,
    RevisionMaterial,
    SchemeOfWork,
    Subject,
)


class BulkRevisionMaterialUploadForm(forms.Form):
    MAX_ARCHIVE_SIZE = 100 * 1024 * 1024
    MAX_FILE_COUNT = 1000
    MAX_FILE_SIZE = 50 * 1024 * 1024
    MAX_TOTAL_FILE_SIZE = 500 * 1024 * 1024
    MAX_MANIFEST_SIZE = 1024 * 1024
    REQUIRED_COLUMNS = {
        'filename',
        'title',
        'subject_id',
        'material_type',
        'term',
    }
    OPTIONAL_COLUMNS = {'description', 'year', 'is_free', 'price'}

    zip_file = forms.FileField(
        label='ZIP archive',
        help_text=(
            'Upload a ZIP (up to 100 MB) containing manifest.csv and up to '
            '1,000 resource files. The archive may contain up to 500 MB of '
            'uncompressed resources.'
        ),
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.zip'}),
    )

    def clean_zip_file(self):
        uploaded_file = self.cleaned_data['zip_file']
        if uploaded_file.size > self.MAX_ARCHIVE_SIZE:
            raise ValidationError('The ZIP archive must be 100 MB or smaller.')
        if not uploaded_file.name.lower().endswith('.zip'):
            raise ValidationError('Choose a ZIP archive.')

        try:
            with zipfile.ZipFile(uploaded_file) as archive:
                resources = self._read_manifest(archive)
                bad_file = archive.testzip()
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error) as exc:
            raise ValidationError('The ZIP archive is invalid or could not be read.') from exc

        if bad_file:
            raise ValidationError(f'The ZIP archive contains a damaged file: {bad_file}')
        self.cleaned_data['resources'] = resources
        return uploaded_file

    def _read_manifest(self, archive):
        entries = archive.infolist()
        if len(entries) > self.MAX_FILE_COUNT * 2 + 1:
            raise ValidationError('The ZIP archive contains too many entries.')

        by_name = {}
        manifest_info = None
        resource_size = 0
        for info in entries:
            if info.filename in by_name:
                raise ValidationError(f'The ZIP archive has a duplicate entry: {info.filename}')
            by_name[info.filename] = info
            if info.is_dir():
                continue

            path = PurePosixPath(info.filename)
            if (
                '\\' in info.filename
                or path.is_absolute()
                or str(path) != info.filename
                or any(part in ('', '.', '..') for part in path.parts)
            ):
                raise ValidationError(f'The ZIP archive has an unsafe path: {info.filename}')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValidationError(f'Symbolic links are not allowed: {info.filename}')
            if info.flag_bits & 0x1:
                raise ValidationError(f'Encrypted ZIP entries are not supported: {info.filename}')

            if info.filename == 'manifest.csv':
                manifest_info = info
            else:
                if info.file_size > self.MAX_FILE_SIZE:
                    raise ValidationError(f'{info.filename} exceeds the 50 MB per-file limit.')
                resource_size += info.file_size

        if manifest_info is None:
            raise ValidationError('Add a manifest.csv file at the root of the ZIP archive.')
        if manifest_info.file_size > self.MAX_MANIFEST_SIZE:
            raise ValidationError('manifest.csv must be 1 MB or smaller.')
        if resource_size > self.MAX_TOTAL_FILE_SIZE:
            raise ValidationError('The uncompressed resources exceed the 500 MB total limit.')

        try:
            with archive.open(manifest_info) as manifest_file:
                text_file = io.TextIOWrapper(manifest_file, encoding='utf-8-sig', newline='')
                reader = csv.DictReader(text_file)
                headers = reader.fieldnames or []
                if len(headers) != len(set(headers)):
                    raise ValidationError('manifest.csv contains duplicate column names.')
                missing_columns = self.REQUIRED_COLUMNS - set(headers)
                unknown_columns = set(headers) - self.REQUIRED_COLUMNS - self.OPTIONAL_COLUMNS
                if missing_columns:
                    raise ValidationError(
                        'manifest.csv is missing required columns: '
                        + ', '.join(sorted(missing_columns))
                    )
                if unknown_columns:
                    raise ValidationError(
                        'manifest.csv contains unknown columns: '
                        + ', '.join(sorted(unknown_columns))
                    )
                rows = list(reader)
        except (csv.Error, UnicodeDecodeError) as exc:
            raise ValidationError('manifest.csv must be a valid UTF-8 CSV file.') from exc

        resources = []
        seen_filenames = set()
        subject_ids = set()
        for row_number, row in enumerate(rows, start=2):
            if None in row:
                raise ValidationError(f'manifest.csv row {row_number} has too many columns.')
            if not any((value or '').strip() for value in row.values()):
                continue

            filename = (row.get('filename') or '').strip()
            title = (row.get('title') or '').strip()
            if not filename or filename == 'manifest.csv' or filename not in by_name:
                raise ValidationError(
                    f'manifest.csv row {row_number} refers to a missing or invalid filename.'
                )
            if by_name[filename].is_dir():
                raise ValidationError(f'manifest.csv row {row_number} refers to a directory.')
            if filename in seen_filenames:
                raise ValidationError(f'manifest.csv lists {filename} more than once.')
            if not title or len(title) > 200:
                raise ValidationError(
                    f'manifest.csv row {row_number} needs a title of 1 to 200 characters.'
                )

            subject_id = (row.get('subject_id') or '').strip()
            if not subject_id.isascii() or not subject_id.isdigit() or len(subject_id) > 19:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid subject_id.')
            parsed_subject_id = int(subject_id)
            if parsed_subject_id > 9223372036854775807:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid subject_id.')
            subject_ids.add(parsed_subject_id)

            material_type = (row.get('material_type') or '').strip()
            valid_types = {value for value, _ in RevisionMaterial.MATERIAL_TYPES}
            if material_type not in valid_types:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid material_type.')
            term = (row.get('term') or '').strip()
            valid_terms = {value for value, _ in RevisionMaterial.TERM_CHOICES}
            if term not in valid_terms:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid term.')

            raw_year = (row.get('year') or '').strip()
            try:
                year = int(raw_year) if raw_year else None
            except ValueError as exc:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid year.') from exc
            if year is not None and not 1 <= year <= 9999:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid year.')

            raw_is_free = (row.get('is_free') or 'true').strip().lower()
            boolean_values = {
                'true': True,
                'yes': True,
                '1': True,
                'false': False,
                'no': False,
                '0': False,
            }
            if raw_is_free not in boolean_values:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid is_free value.')

            raw_price = (row.get('price') or '0').strip()
            try:
                price = forms.DecimalField(
                    max_digits=10,
                    decimal_places=2,
                    min_value=Decimal('0'),
                ).clean(raw_price)
            except (ValidationError, InvalidOperation) as exc:
                raise ValidationError(f'manifest.csv row {row_number} has an invalid price.') from exc

            seen_filenames.add(filename)
            resources.append({
                'filename': filename,
                'title': title,
                'subject_id': parsed_subject_id,
                'material_type': material_type,
                'term': term,
                'description': (row.get('description') or '').strip(),
                'year': year,
                'is_free': boolean_values[raw_is_free],
                'price': price,
            })

        if not resources:
            raise ValidationError('manifest.csv must list at least one resource.')
        if len(resources) > self.MAX_FILE_COUNT:
            raise ValidationError('The ZIP archive may contain at most 1,000 resources.')

        subjects = Subject.objects.in_bulk(subject_ids)
        missing_subject_ids = subject_ids - subjects.keys()
        if missing_subject_ids:
            raise ValidationError(
                'manifest.csv contains unknown subject_id values: '
                + ', '.join(str(value) for value in sorted(missing_subject_ids))
            )
        for resource in resources:
            resource['subject'] = subjects[resource['subject_id']]

        listed_filenames = seen_filenames | {'manifest.csv'}
        unlisted_files = {
            info.filename for info in entries
            if not info.is_dir() and info.filename not in listed_filenames
        }
        if unlisted_files:
            raise ValidationError(
                'Every file in the ZIP archive must be listed in manifest.csv. '
                f'Unlisted: {", ".join(sorted(unlisted_files)[:5])}'
            )

        return resources


class RevisionMaterialUploadForm(forms.ModelForm):
    class Meta:
        model = RevisionMaterial
        fields = (
            'title',
            'subject',
            'material_type',
            'term',
            'description',
            'file',
            'year',
            'is_free',
            'price',
        )
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'material_type': forms.Select(attrs={'class': 'form-select'}),
            'term': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'year': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_free': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }


class SchemeOfWorkUploadForm(forms.ModelForm):
    weeks_count = forms.IntegerField(
        min_value=1,
        max_value=52,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 52}),
    )

    class Meta:
        model = SchemeOfWork
        fields = (
            'class_ref',
            'subject',
            'title',
            'term',
            'academic_year',
            'weeks_count',
            'file',
            'description',
            'is_active',
        )
        widgets = {
            'class_ref': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'term': forms.Select(attrs={'class': 'form-select'}),
            'academic_year': forms.TextInput(attrs={'class': 'form-control'}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        class_ref = cleaned_data.get('class_ref')
        subject = cleaned_data.get('subject')
        if class_ref and subject and subject.class_ref_id != class_ref.id:
            self.add_error('subject', 'Choose a subject linked to the selected class.')
        return cleaned_data


class CurriculumUploadForm(forms.ModelForm):
    SUPPORTED_CURRICULUM_EXTENSIONS = {
        '.pdf', '.docx', '.pptx', '.xlsx', '.csv', '.txt',
        '.bmp', '.jpeg', '.jpg', '.png', '.tif', '.tiff', '.webp',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        class_id = None
        if self.is_bound:
            class_id = self.data.get(self.add_prefix('class_ref'))
        elif self.initial.get('class_ref'):
            class_id = self.initial['class_ref']

        if class_id:
            self.fields['subject'].queryset = Subject.objects.filter(
                class_ref_id=class_id
            ).order_by('name')

    class Meta:
        model = Curriculum
        fields = (
            'class_ref',
            'subject',
            'curriculum_type',
            'academic_year',
            'file',
        )
        widgets = {
            'class_ref': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'curriculum_type': forms.Select(attrs={'class': 'form-select'}),
            'academic_year': forms.TextInput(attrs={'class': 'form-control'}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }
        help_texts = {
            'file': (
                'Upload the official syllabus as PDF, DOCX, PPTX, XLSX, CSV, TXT, '
                'or a scanned image. Scanned pages need Tesseract or Gemini OCR.'
            ),
        }

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        if uploaded_file.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Documents must be 10 MB or smaller.')
        extension = os.path.splitext(uploaded_file.name)[1].lower()
        if extension not in self.SUPPORTED_CURRICULUM_EXTENSIONS:
            raise forms.ValidationError(
                'Upload a PDF, DOCX, PPTX, XLSX, CSV, TXT, or scanned image file.'
            )
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        class_ref = cleaned_data.get('class_ref')
        subject = cleaned_data.get('subject')
        if class_ref and not subject:
            subject_id = self.data.get(self.add_prefix('subject'))
            if subject_id:
                try:
                    subject = Subject.objects.get(pk=subject_id)
                except (Subject.DoesNotExist, ValueError, TypeError):
                    subject = None
        if class_ref and subject and subject.class_ref_id != class_ref.id:
            self.add_error('subject', 'Choose a subject linked to the selected class.')
        return cleaned_data


class CurriculumReviewForm(forms.Form):
    extracted_topics = forms.CharField(
        required=False,
        max_length=40000,
        label='Curriculum topics (one per line)',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 10}),
        help_text='Review, edit, and keep only the official topics used by scheme and exam tools.',
    )
    activate = forms.BooleanField(
        required=False,
        initial=True,
        label='Activate this syllabus after review',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )


class SchemeOfWorkGeneratorForm(forms.Form):
    class_ref = forms.ModelChoiceField(
        queryset=Class.objects.all().order_by('curriculum', 'level', 'name'),
        label='Class',
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.all().order_by('name'),
        label='Subject',
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    term = forms.ChoiceField(
        choices=RevisionMaterial.TERM_CHOICES,
        label='Term',
        initial='term1',
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    weeks_count = forms.IntegerField(
        required=True,
        label='Number of Weeks',
        min_value=1,
        max_value=52,
        initial=12,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 12'}),
    )
    topics = forms.CharField(
        required=False,
        label='Topics (one per line)',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'Enter topics or learning outcomes, one per line...'}),
        help_text='List the topics or learning outcomes to include in the scheme.',
    )
    objectives = forms.CharField(
        required=False,
        label='General Objectives',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Enter overall learning objectives...'}),
    )
