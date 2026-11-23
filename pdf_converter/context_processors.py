from urllib.parse import urlsplit
from django.conf import settings


def _hostname(value):
    return urlsplit(f'//{value}').hostname


def platform_links(request):
    host = request.get_host().split(':')[0].lower()
    path = request.path.lower()
    platform_param = request.GET.get('platform')
    session_platform = request.session.get('active_platform')

    # Determine active platform
    if path.startswith('/converter') or platform_param == 'converter' or host.startswith('converter'):
        is_converter = True
    elif path.startswith('/academic') or path.startswith('/search') or path.startswith('/jobs') or path.startswith('/sow') or platform_param == 'academic' or host.startswith('academic'):
        is_converter = False
    elif session_platform == 'converter':
        is_converter = True
    elif session_platform == 'academic':
        is_converter = False
    else:
        # Default on root is Academic
        is_converter = False

    return {
        'is_converter_site': is_converter,
        'is_academic_site': not is_converter,
        'converter_home_url': '/converter/',
        'academic_home_url': '/academic/',
    }
