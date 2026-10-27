from django.conf import settings
from .access import is_teacher

def common(request):
    return {'teacher': is_teacher(request.user), 'demo_mode': settings.MESHY_MODE == 'demo',
            'demo_login_enabled': settings.DEBUG and settings.MESHY_MODE == 'demo'}
