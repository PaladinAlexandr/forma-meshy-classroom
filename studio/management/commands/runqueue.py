import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from studio.models import Request
from studio.tasks import process, sweep

class Command(BaseCommand):
    help = 'Single-process durable queue for local development, no Redis required.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write('Queue started. Ctrl+C to stop.')
        try:
            while True:
                close_old_connections()
                sweep()
                now = timezone.now()
                pks = list(Request.objects.filter(status__in=['approved', 'running', 'saving']).filter(
                    Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now)).order_by('reviewed_at', 'created_at', 'id').values_list('pk', flat=True)[:50])
                for pk in pks:
                    process(str(pk))
                if options['once']:
                    return
                time.sleep(3)
        except KeyboardInterrupt:
            self.stdout.write('Queue stopped.')
