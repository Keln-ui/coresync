from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, HttpResponse, JsonResponse
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
import os
import logging
from .models import (
    Class,
    Curriculum,
    RevisionMaterial,
    SchemeOfWork,
    SchemeWeekProgress,
    Subject,
)
from .forms import (
    CurriculumUploadForm,
    RevisionMaterialUploadForm,
    SchemeOfWorkGeneratorForm,
    SchemeOfWorkUploadForm,
)
from pdf_converter.views import revision_access_required
from pdf_converter.payment_utils import payment_required_for_revision, get_revision_material_price
from django.urls import reverse
from urllib.parse import urlencode
from pdf_converter.models import SchoolProfile

logger = logging.getLogger(__name__)

STAFF_UPLOAD_FORMS = {
    'resource': (RevisionMaterialUploadForm, 'Learning resource'),
    'scheme': (SchemeOfWorkUploadForm, 'Scheme of Work'),
    'curriculum': (CurriculumUploadForm, 'Curriculum source'),
}


@user_passes_test(lambda user: user.is_authenticated and user.is_staff)
def staff_resource_dashboard(request):
    """Provide staff with a central place to upload and manage learning resources."""
    from django.db.models import Sum

    return render(request, 'revision/staff_resource_dashboard.html', {
        'total_materials': RevisionMaterial.objects.count(),
        'total_downloads': RevisionMaterial.objects.aggregate(
            total=Sum('download_count')
        )['total'] or 0,
        'total_schemes': SchemeOfWork.objects.count(),
        'total_curricula': Curriculum.objects.count(),
        'recent_materials': RevisionMaterial.objects.select_related(
            'subject', 'subject__class_ref'
        )[:6],
        'recent_schemes': SchemeOfWork.objects.select_related(
            'class_ref', 'subject'
        )[:4],
    })


@user_passes_test(lambda user: user.is_authenticated and user.is_staff)
def staff_upload_resource(request, upload_type):
    """Upload a revision resource, scheme of work, or curriculum source."""
    form_config = STAFF_UPLOAD_FORMS.get(upload_type)
    if form_config is None:
        raise Http404('Unknown resource type.')

    form_class, label = form_config
    initial = {}
    if request.method == 'GET' and upload_type == 'resource':
        material_type = request.GET.get('type', '')
        valid_types = {value for value, _ in RevisionMaterial.MATERIAL_TYPES}
        if material_type in valid_types:
            initial['material_type'] = material_type

    form = form_class(
        request.POST if request.method == 'POST' else None,
        request.FILES or None,
        initial=initial,
    )
    if request.method == 'POST' and form.is_valid():
        item = form.save()
        messages.success(request, f'{label} uploaded successfully.')
        if upload_type == 'resource':
            return redirect('material_detail', material_id=item.id)
        return redirect('staff_resource_dashboard')

    return render(request, 'revision/staff_resource_upload.html', {
        'form': form,
        'upload_type': upload_type,
        'label': label,
    })


def revision_home(request):
    """Home page for revision materials"""
    from django.db.models import Sum, Count
    
    # Get all classes grouped by curriculum and level
    cbe_primary = Class.objects.filter(curriculum='cbe', level='primary')
    cbe_secondary = Class.objects.filter(curriculum='cbe', level='secondary')
    cbe_college = Class.objects.filter(curriculum='cbe', level='college')
    
    eight_four_primary = Class.objects.filter(curriculum='844', level='primary')
    eight_four_secondary = Class.objects.filter(curriculum='844', level='secondary')
    eight_four_college = Class.objects.filter(curriculum='844', level='college')
    
    # Get recent materials
    recent_materials = RevisionMaterial.objects.all().order_by('-created_at')[:10]
    
    # Stats
    total_materials = RevisionMaterial.objects.count()
    free_count = RevisionMaterial.objects.filter(is_free=True).count()
    premium_count = total_materials - free_count
    total_downloads = RevisionMaterial.objects.aggregate(total=Sum('download_count'))['total'] or 0
    cbe_total = cbe_primary.count() + cbe_secondary.count() + cbe_college.count()
    eight_four_total = eight_four_primary.count() + eight_four_secondary.count() + eight_four_college.count()
    
    context = {
        'cbe_primary': cbe_primary,
        'cbe_secondary': cbe_secondary,
        'cbe_college': cbe_college,
        'eight_four_primary': eight_four_primary,
        'eight_four_secondary': eight_four_secondary,
        'eight_four_college': eight_four_college,
        'recent_materials': recent_materials,
        'total_materials': total_materials,
        'free_count': free_count,
        'premium_count': premium_count,
        'total_downloads': total_downloads,
        'cbe_total': cbe_total,
        'eight_four_total': eight_four_total,
    }
    
    return render(request, 'revision/home.html', context)


def class_subjects(request, class_id):
    """View all subjects for a specific class"""
    class_obj = get_object_or_404(Class, id=class_id)
    subjects = Subject.objects.filter(class_ref=class_obj).order_by('name')
    
    context = {
        'class_obj': class_obj,
        'subjects': subjects,
    }
    
    return render(request, 'revision/class_subjects.html', context)


@revision_access_required
def subject_materials(request, subject_id):
    """View all materials for a specific subject"""
    subject = get_object_or_404(Subject, id=subject_id)
    materials = RevisionMaterial.objects.filter(subject=subject).order_by('-created_at')
    
    # Group materials by type
    materials_by_type = {}
    for material in materials:
        mat_type = material.get_material_type_display()
        if mat_type not in materials_by_type:
            materials_by_type[mat_type] = []
        materials_by_type[mat_type].append(material)
    
    context = {
        'subject': subject,
        'materials': materials,
        'materials_by_type': materials_by_type,
    }
    
    return render(request, 'revision/subject_materials.html', context)


@revision_access_required
def material_detail(request, material_id):
    """View details of a specific material"""
    material = get_object_or_404(RevisionMaterial, id=material_id)
    
    context = {
        'material': material,
    }
    
    return render(request, 'revision/material_detail.html', context)


@revision_access_required
def download_material(request, material_id):
    """Download a revision material. Marking schemes require login."""
    material = get_object_or_404(RevisionMaterial, id=material_id)
    
    # Marking schemes require authentication
    if material.material_type == 'marking_scheme' and not request.user.is_authenticated:
        return redirect(f'/accounts/login/?next=/revision/material/{material_id}/')
    
    # Check if payment is required globally or for this material
    if payment_required_for_revision(material):
        price = get_revision_material_price(material)
        if price > 0:
            # In a real implementation, redirect to payment page
            # For now, show a message that payment is required
            messages.error(request, f'Payment of KSH {price} is required to download this material. Please contact the administrator.')
            return redirect('material_detail', material_id=material.id)
    
    # All other materials or authenticated users: allow download
    material.increment_downloads()
    
    # Serve the file
    file_path = os.path.join(settings.MEDIA_ROOT, str(material.file))
    if os.path.exists(file_path):
        with open(file_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/octet-stream')
            response['Content-Disposition'] = f'attachment; filename="{os.path.basename(material.file.name)}"'
            return response
    else:
        return HttpResponse('File not found', status=404)


@revision_access_required
def preview_material(request, material_id):
    """Preview a revision material - show first page as text overview"""
    material = get_object_or_404(RevisionMaterial, id=material_id)
    
    preview_text = ""
    preview_pages = 0
    total_pages = 0
    file_type = ""
    preview_available = False
    
    file_path = os.path.join(settings.MEDIA_ROOT, str(material.file))
    
    if os.path.exists(file_path):
        filename_lower = material.file.name.lower()
        file_type = filename_lower.split('.')[-1].upper() if '.' in material.file.name else 'UNKNOWN'
        
        try:
            if filename_lower.endswith('.pdf'):
                import PyPDF2
                with open(file_path, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    total_pages = len(pdf_reader.pages)
                    preview_pages = min(2, total_pages)
                    
                    preview_lines = []
                    for page_num in range(preview_pages):
                        page = pdf_reader.pages[page_num]
                        text = page.extract_text()
                        if text:
                            lines = text.strip().split('\n')
                            preview_lines.extend(lines[:30])
                    
                    if preview_lines:
                        preview_text = '\n'.join(preview_lines)[:2000]
                        preview_available = True
                    else:
                        # PDF exists but has no extractable text (scanned/image-based)
                        preview_text = "This PDF appears to contain scanned images rather than selectable text. A text preview cannot be generated. Download the file to view its contents."
                        
            elif filename_lower.endswith('.txt'):
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    total_pages = 1
                    preview_pages = 1
                    preview_text = content[:2000]
                    preview_available = True
                    
            elif filename_lower.endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                # Image files - can't show text preview but could show thumbnail
                preview_text = f"This is an image file ({file_type}). A text preview is not available. Download the file to view it."
                
            elif filename_lower.endswith(('.docx', '.doc')):
                preview_text = f"This is a Word document ({file_type}). A text preview is not available in the browser. Download the file to view it."
                
            elif filename_lower.endswith('.pptx'):
                preview_text = f"This is a PowerPoint file ({file_type}). A text preview is not available. Download the file to view it."
                
            else:
                preview_text = f"This file type ({file_type}) is not supported for preview. Download the file to view its contents."
                
        except ImportError as e:
            logger.warning(f"Missing library for preview: {str(e)}")
            preview_text = f"A required library is missing to preview this file. Download the file to view its contents."
            
        except Exception as e:
            logger.warning(f"Could not generate preview for {material.file.name}: {str(e)}")
            preview_text = f"An error occurred while trying to generate a preview for this file. Download the file to view its contents."
    
    context = {
        'material': material,
        'preview_text': preview_text,
        'preview_pages': preview_pages,
        'total_pages': total_pages,
        'file_type': file_type,
        'preview_available': preview_available,
        'is_paid': material.is_free,  # Free materials are effectively "paid"
    }
    
    return render(request, 'revision/material_preview.html', context)


@revision_access_required
def browse_materials(request):
    """Browse all materials with filters"""
    curriculum = request.GET.get('curriculum', '')
    level = request.GET.get('level', '')
    class_id = request.GET.get('class', '')
    material_type = request.GET.get('type', '')
    term = request.GET.get('term', '')
    
    materials = RevisionMaterial.objects.all()
    
    if curriculum:
        materials = materials.filter(subject__class_ref__curriculum=curriculum)
    if level:
        materials = materials.filter(subject__class_ref__level=level)
    if class_id:
        materials = materials.filter(subject__class_ref_id=class_id)
    if material_type:
        materials = materials.filter(material_type=material_type)
    if term:
        materials = materials.filter(term=term)
    
    materials = materials.order_by('-created_at')
    
    # Get filter options
    curricula = Class.CURRICULUM_CHOICES
    levels = Class.LEVEL_CHOICES
    types = RevisionMaterial.MATERIAL_TYPES
    terms = RevisionMaterial.TERM_CHOICES
    
    # Get classes for selected curriculum/level
    classes = []
    if curriculum and level:
        classes = Class.objects.filter(curriculum=curriculum, level=level)
    
    context = {
        'materials': materials,
        'curricula': curricula,
        'levels': levels,
        'types': types,
        'terms': terms,
        'classes': classes,
        'selected_curriculum': curriculum,
        'selected_level': level,
        'selected_class': class_id,
        'selected_type': material_type,
        'selected_term': term,
    }
    
    return render(request, 'revision/browse.html', context)


@revision_access_required
def browse_by_subject(request):
    """Browse all subjects with their materials count"""
    # Get all subjects with material count
    from django.db.models import Count
    subjects = Subject.objects.annotate(
        material_count=Count('materials')
    ).filter(material_count__gt=0).order_by('class_ref__curriculum', 'class_ref__name', 'name')
    
    # Group by curriculum
    cbe_subjects = subjects.filter(class_ref__curriculum='cbe')
    eight_four_subjects = subjects.filter(class_ref__curriculum='844')
    
    context = {
        'cbe_subjects': cbe_subjects,
        'eight_four_subjects': eight_four_subjects,
        'total_subjects': subjects.count(),
    }
    
    return render(request, 'revision/browse_by_subject.html', context)


def browse_schemes(request):
    """Browse all admin-uploaded schemes of work"""
    curriculum = request.GET.get('curriculum', '')
    level = request.GET.get('level', '')
    class_id = request.GET.get('class', '')
    subject_id = request.GET.get('subject', '')
    term = request.GET.get('term', '')
    
    schemes = SchemeOfWork.objects.filter(is_active=True)
    
    if curriculum:
        schemes = schemes.filter(class_ref__curriculum=curriculum)
    if level:
        schemes = schemes.filter(class_ref__level=level)
    if class_id:
        schemes = schemes.filter(class_ref_id=class_id)
    if subject_id:
        schemes = schemes.filter(subject_id=subject_id)
    if term:
        schemes = schemes.filter(term=term)
    
    schemes = schemes.order_by('-academic_year', 'subject', 'term')
    
    context = {
        'schemes': schemes,
        'curricula': Class.CURRICULUM_CHOICES,
        'levels': Class.LEVEL_CHOICES,
        'selected_curriculum': curriculum,
        'selected_level': level,
        'selected_class': class_id,
        'selected_subject': subject_id,
        'selected_term': term,
    }
    
    return render(request, 'revision/browse_schemes.html', context)


def download_scheme(request, scheme_id):
    """Download an admin-uploaded Scheme of Work Word document (.docx)."""
    scheme = get_object_or_404(SchemeOfWork, id=scheme_id, is_active=True)
    event_fields = [
        ('reporting_week', 'Reporting week'),
        ('midbreak_week', 'Mid-break week'),
        ('exams_week', 'Exams'),
        ('closing_week', 'Closing week'),
    ]

    school_profile = SchoolProfile.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    profile_school_name = school_profile.school_name if school_profile else ""

    academic_years = list(
        SchemeOfWork.objects.filter(is_active=True)
        .exclude(academic_year='')
        .values_list('academic_year', flat=True)
        .distinct()
        .order_by('-academic_year')
    )
    if scheme.academic_year not in academic_years:
        academic_years.insert(0, scheme.academic_year)

    week_options = list(range(1, max(0, scheme.weeks_count) + 1))
    context = {
        'scheme': scheme,
        'classes': Class.objects.all().order_by('curriculum', 'level', 'name'),
        'academic_years': academic_years,
        'term_choices': SchemeOfWork.TERM_CHOICES,
        'school_name': profile_school_name,
        'week_options': week_options,
    }

    if request.method == 'GET':
        context['event_fields'] = [
            {'name': name, 'label': label, 'value': ''}
            for name, label in event_fields
        ]
        context.update({
            'selected_class_id': str(scheme.class_ref_id),
            'selected_academic_year': scheme.academic_year,
            'selected_term': scheme.term,
        })
        return render(request, 'revision/download_scheme.html', context)

    school_name = request.POST.get('school_name', '').strip()
    class_id = request.POST.get('class_id', '').strip()
    academic_year = request.POST.get('academic_year', '').strip()
    term = request.POST.get('term', '').strip()
    selected_event_weeks = {
        name: request.POST.get(name, '').strip()
        for name, label in event_fields
    }
    context['event_fields'] = [
        {'name': name, 'label': label, 'value': selected_event_weeks[name]}
        for name, label in event_fields
    ]

    valid_term_values = {value for value, label in SchemeOfWork.TERM_CHOICES}
    if not school_name or not class_id or not academic_year or term not in valid_term_values:
        context.update({
            'school_name': school_name,
            'selected_class_id': class_id,
            'selected_academic_year': academic_year,
            'selected_term': term,
            'error': 'Please enter the school name and select class/grade, year, and term.',
        })
        return render(request, 'revision/download_scheme.html', context)

    try:
        selected_class = Class.objects.get(id=class_id)
    except (Class.DoesNotExist, ValueError, TypeError):
        context.update({
            'school_name': school_name,
            'selected_class_id': '',
            'selected_academic_year': academic_year,
            'selected_term': term,
            'error': 'Please select a valid class/grade.',
        })
        return render(request, 'revision/download_scheme.html', context)

    special_weeks = {}
    invalid_event_week = False
    for name, label in event_fields:
        week_value = selected_event_weeks[name]
        if not week_value:
            continue
        try:
            week_number = int(week_value)
        except ValueError:
            invalid_event_week = True
            break
        if week_number not in week_options:
            invalid_event_week = True
            break
        special_weeks.setdefault(week_number, []).append(label)

    if invalid_event_week:
        context.update({
            'school_name': school_name,
            'selected_class_id': class_id,
            'selected_academic_year': academic_year,
            'selected_term': term,
            'error': f'Special event weeks must be between week 1 and week {len(week_options)}.',
        })
        return render(request, 'revision/download_scheme.html', context)

    term_label = dict(SchemeOfWork.TERM_CHOICES).get(term, term)
    scheme.increment_downloads()
    
    file_path = os.path.join(settings.MEDIA_ROOT, str(scheme.file))
    if not os.path.exists(file_path):
        file_path = getattr(scheme.file, 'path', file_path)
    
    if os.path.exists(file_path):
        ext = os.path.splitext(scheme.file.name)[1].lower()
        if ext in ['.docx', '.doc']:
            content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            download_name = os.path.basename(scheme.file.name)
            if ext == '.docx':
                try:
                    from pdf_converter.sow_docx import personalize_uploaded_scheme_docx
                    personalized = personalize_uploaded_scheme_docx(
                        file_path,
                        school_name,
                        class_name=selected_class.name,
                        subject_name=scheme.subject.name,
                        term=term_label,
                        academic_year=academic_year,
                        special_weeks=special_weeks,
                    )
                    response = HttpResponse(personalized.getvalue(), content_type=content_type)
                    response['Content-Disposition'] = f'attachment; filename="{download_name}"'
                    return response
                except Exception:
                    logger.exception("Could not personalize uploaded scheme of work; serving original DOCX")
        elif ext == '.txt':
            try:
                from pdf_converter.sow_docx import build_scheme_of_work_docx
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    txt_content = f.read()
                docx_buf = build_scheme_of_work_docx(
                    school_name=school_name,
                    class_name=selected_class.name,
                    subject_name=scheme.subject.name,
                    term=term_label,
                    academic_year=academic_year,
                    weeks_count=scheme.weeks_count,
                    custom_content=txt_content,
                    special_weeks=special_weeks,
                )
                safe_name = os.path.splitext(os.path.basename(scheme.file.name))[0] + ".docx"
                response = HttpResponse(docx_buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
                response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
                return response
            except Exception:
                content_type = 'application/octet-stream'
                download_name = os.path.basename(scheme.file.name)
        else:
            content_type = 'application/octet-stream'
            download_name = os.path.basename(scheme.file.name)
            
        with open(file_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type=content_type)
            response['Content-Disposition'] = f'attachment; filename="{download_name}"'
            return response
    else:
        return HttpResponse('File not found', status=404)


@login_required(login_url='login')
def scheme_coverage(request, scheme_id):
    """Let an authenticated teacher track their own weekly scheme coverage."""
    scheme = get_object_or_404(SchemeOfWork, id=scheme_id, is_active=True)
    week_count = min(max(scheme.weeks_count, 1), 52)
    if request.method == 'POST':
        try:
            week_number = int(request.POST.get('week_number', ''))
        except ValueError:
            messages.error(request, 'Choose a valid week.')
            return redirect('scheme_coverage', scheme_id=scheme.id)

        status = request.POST.get('status', '')
        notes = request.POST.get('notes', '').strip()
        valid_statuses = {choice[0] for choice in SchemeWeekProgress.STATUS_CHOICES}
        if not 1 <= week_number <= week_count:
            messages.error(request, f'Week must be between 1 and {week_count}.')
        elif status not in valid_statuses:
            messages.error(request, 'Choose a valid coverage status.')
        elif len(notes) > 1000:
            messages.error(request, 'Notes must be 1,000 characters or fewer.')
        else:
            SchemeWeekProgress.objects.update_or_create(
                scheme=scheme,
                teacher=request.user,
                week_number=week_number,
                defaults={'status': status, 'notes': notes},
            )
            messages.success(request, f'Week {week_number} coverage saved.')
        return redirect('scheme_coverage', scheme_id=scheme.id)

    progress_by_week = {
        item.week_number: item
        for item in SchemeWeekProgress.objects.filter(scheme=scheme, teacher=request.user)
    }
    week_rows = [
        {
            'number': week_number,
            'status': progress_by_week[week_number].status if week_number in progress_by_week else 'planned',
            'notes': progress_by_week[week_number].notes if week_number in progress_by_week else '',
            'updated_at': progress_by_week[week_number].updated_at if week_number in progress_by_week else None,
        }
        for week_number in range(1, week_count + 1)
    ]
    return render(request, 'revision/scheme_coverage.html', {
        'scheme': scheme,
        'week_rows': week_rows,
        'status_choices': SchemeWeekProgress.STATUS_CHOICES,
        'taught_count': sum(row['status'] == 'taught' for row in week_rows),
        'follow_up_count': sum(row['status'] == 'follow_up' for row in week_rows),
    })


@user_passes_test(lambda user: user.is_authenticated and user.is_staff)
def scheme_coverage_overview(request):
    """Show aggregate weekly progress across active schemes to staff."""
    from django.db.models import Count, Q

    schemes = SchemeOfWork.objects.filter(is_active=True).select_related(
        'class_ref', 'subject'
    ).annotate(
        taught_weeks=Count(
            'week_progress__week_number',
            filter=Q(week_progress__status='taught'),
            distinct=True,
        ),
        follow_up_weeks=Count(
            'week_progress__week_number',
            filter=Q(week_progress__status='follow_up'),
            distinct=True,
        ),
        teachers_reporting=Count('week_progress__teacher_id', distinct=True),
    )
    recent_updates = SchemeWeekProgress.objects.select_related(
        'scheme', 'scheme__class_ref', 'scheme__subject', 'teacher'
    ).order_by('-updated_at')[:12]
    return render(request, 'revision/coverage_overview.html', {
        'schemes': schemes,
        'recent_updates': recent_updates,
        'total_updates': SchemeWeekProgress.objects.count(),
        'taught_updates': SchemeWeekProgress.objects.filter(status='taught').count(),
        'follow_up_updates': SchemeWeekProgress.objects.filter(status='follow_up').count(),
    })


@user_passes_test(lambda u: u.is_authenticated and u.is_staff)
def link_material(request):
    """Link a past paper to its marking scheme or vice versa (Admin only)"""
    if request.method == 'POST':
        material_id = request.POST.get('material_id')
        related_material_id = request.POST.get('related_material_id')
        
        if material_id and related_material_id:
            try:
                material = get_object_or_404(RevisionMaterial, id=material_id)
                related_material = get_object_or_404(RevisionMaterial, id=related_material_id)
                
                # Link both ways
                material.related_material = related_material
                material.save()
                
                # If the related material doesn't have a link back, create it
                if not related_material.related_material:
                    related_material.related_material = material
                    related_material.save()
                
                messages.success(request, f'Successfully linked "{material.title}" with "{related_material.title}"')
            except Exception as e:
                messages.error(request, f'Error linking materials: {str(e)}')
        else:
            messages.error(request, 'Please select both materials to link')
        
        return redirect('link_material')
    
    # GET request - show the form
    past_papers = RevisionMaterial.objects.filter(material_type='past_paper').order_by('-created_at')
    marking_schemes = RevisionMaterial.objects.filter(material_type='marking_scheme').order_by('-created_at')
    
    context = {
        'past_papers': past_papers,
        'marking_schemes': marking_schemes,
    }
    
    return render(request, 'revision/link_material.html', context)


@user_passes_test(lambda u: u.is_authenticated and u.is_staff)
def generate_scheme_of_work(request):
    """Generate a scheme of work file and save it as a revision material."""
    from pdf_converter.models import SchoolProfile
    school_profile = SchoolProfile.objects.filter(user=request.user).first()
    school_details_complete = bool(
        school_profile
        and school_profile.school_name.strip()
        and school_profile.phone.strip()
        and school_profile.email.strip()
    )
    if not school_details_complete:
        from urllib.parse import urlencode
        from django.urls import reverse
        messages.warning(request, "Please enter your school details before generating a scheme of work.")
        query = urlencode({'institution_only': '1', 'next': request.path})
        return redirect(f"{reverse('edit_profile')}?{query}")

    if request.method == 'POST':
        form = SchemeOfWorkGeneratorForm(request.POST)
        if form.is_valid():
            class_ref = form.cleaned_data['class_ref']
            subject = form.cleaned_data['subject']
            term = form.cleaned_data['term']
            weeks_count = form.cleaned_data['weeks_count']
            topics = form.cleaned_data['topics']
            objectives = form.cleaned_data['objectives']
            
            # Build the scheme of work content
            lines = [
                f"SCHEME OF WORK",
                f"=" * 40,
                f"",
                f"School: {school_profile.school_name}",
                f"Class: {class_ref.name}",
                f"Curriculum: {class_ref.get_curriculum_display()}",
                f"Subject: {subject.name}",
                f"Term: {dict(RevisionMaterial.TERM_CHOICES).get(term, term)}",
                f"Number of Weeks: {weeks_count}",
                f"",
                f"General Objectives:",
                objectives or "Not specified",
                f"",
                f"=" * 40,
                f"WEEKLY BREAKDOWN:",
                f"=" * 40,
                f"",
            ]
            
            # Split topics into weeks
            topic_list = [t.strip() for t in topics.split('\n') if t.strip()]
            topics_per_week = max(1, len(topic_list) // weeks_count) if topic_list else 1
            
            for week_num in range(1, weeks_count + 1):
                lines.append(f"Week {week_num}:")
                start_idx = (week_num - 1) * topics_per_week
                end_idx = start_idx + topics_per_week
                week_topics = topic_list[start_idx:end_idx] if topic_list else [f"Topic for Week {week_num}"]
                for topic in week_topics:
                    lines.append(f"  - {topic}")
                lines.append(f"  Learning Objectives: To be specified")
                lines.append(f"  Teaching Activities: To be specified")
                lines.append(f"  Assessment Methods: To be specified")
                lines.append("")
            
            content = '\n'.join(lines)
            
            # Save file as a Word document (.docx)
            try:
                from pdf_converter.sow_docx import build_scheme_of_work_docx
                term_label = dict(RevisionMaterial.TERM_CHOICES).get(term, term)
                docx_buf = build_scheme_of_work_docx(
                    school_name=school_profile.school_name,
                    school_address=school_profile.school_address,
                    school_phone=school_profile.phone,
                    school_email=school_profile.email,
                    principal_name=school_profile.principal_name,
                    class_name=class_ref.name,
                    subject_name=subject.name,
                    term=term_label,
                    academic_year="2025",
                    weeks_count=weeks_count,
                    custom_content=content,
                    objectives=objectives,
                )
                safe_filename = f"SOW_{subject.name}_{class_ref.name}_{term}.docx"
                safe_filename = ''.join(c for c in safe_filename if c.isalnum() or c in ('_', '-', '.')).strip()
                storage_path = f"revision_materials/{safe_filename}"
                saved_path = default_storage.save(storage_path, ContentFile(docx_buf.getvalue()))
            except Exception:
                safe_filename = f"SOW_{subject.name}_{class_ref.name}_{term}.txt"
                safe_filename = ''.join(c for c in safe_filename if c.isalnum() or c in ('_', '-', '.')).strip()
                storage_path = f"revision_materials/{safe_filename}"
                generated_file = ContentFile(content.encode('utf-8'))
                saved_path = default_storage.save(storage_path, generated_file)
            
            material = RevisionMaterial.objects.create(
                subject=subject,
                title=f"Scheme of Work - {subject.name} - {class_ref.name}",
                material_type='other',
                term=term,
                description=f"Scheme of Work for {subject.name}, {class_ref.name}",
                file=saved_path,
                is_free=True,
            )
            
            messages.success(request, f'Scheme of Work for {subject.name} has been generated successfully.')
            return redirect('material_detail', material_id=material.id)
    else:
        form = SchemeOfWorkGeneratorForm()
    
    return render(request, 'revision/generate_scheme_of_work.html', {'form': form})