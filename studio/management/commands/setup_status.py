import json
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from studio.models import Policy, Request


class Command(BaseCommand):
    help = 'Machine-readable local installer status. Does not expose secrets.'

    def handle(self, *args, **options):
        policy = Policy.objects.filter(pk=1).first()
        self.stdout.write(json.dumps({
            'admin_exists': get_user_model().objects.filter(is_superuser=True, is_staff=True, is_active=True).exists(),
            'mode': settings.MESHY_MODE,
            'api_configured': bool(settings.MESHY_API_KEY),
            'generation_tasks': Request.objects.filter(status__in=['approved', 'submitting', 'running', 'saving', 'uncertain', 'image_ready']).count(),
            'max_parallel': policy.max_parallel if policy else None,
        }))
