from django import forms
from .models import (
    Class,
    Curriculum,
    RevisionMaterial,
    SchemeOfWork,
    Subject,
)


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
    class Meta:
        model = Curriculum
        fields = (
            'class_ref',
            'subject',
            'curriculum_type',
            'academic_year',
            'file',
            'extracted_topics',
            'is_active',
        )
        widgets = {
            'class_ref': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'curriculum_type': forms.Select(attrs={'class': 'form-select'}),
            'academic_year': forms.TextInput(attrs={'class': 'form-control'}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'extracted_topics': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        class_ref = cleaned_data.get('class_ref')
        subject = cleaned_data.get('subject')
        if class_ref and subject and subject.class_ref_id != class_ref.id:
            self.add_error('subject', 'Choose a subject linked to the selected class.')
        return cleaned_data


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
