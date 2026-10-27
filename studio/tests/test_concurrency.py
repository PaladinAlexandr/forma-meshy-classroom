"""Real row-lock tests. Executed by CI with PostgreSQL, skipped on local SQLite."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from studio.forms import RequestForm
from studio.models import Request, Preset, Policy
from studio.services import create_request, review_request
from studio.tasks import process
from .test_workflow import photo


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks; CI provides PostgreSQL.')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], MESHY_MODE='live', MESHY_API_KEY='test-only')
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.student = get_user_model().objects.create_user(username='concurrent-student')
        self.teacher = get_user_model().objects.create_user(username='concurrent-teacher')
        self.teacher.user_permissions.add(Permission.objects.get(codename='moderate_request'))
        self.preset = Preset.objects.create(name='Clay', description='Clay', texture_prompt='Clay')
        Policy.objects.create(pk=1, max_parallel=10)

    def simultaneous(self, functions):
        barrier = Barrier(len(functions))
        def run(fn):
            connections.close_all()
            barrier.wait(timeout=15)
            try:
                return fn()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            return list(pool.map(run, functions))

    def test_two_tabs_cannot_create_two_projects(self):
        import tempfile
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            def send():
                form = RequestForm({'title': 'Parallel', 'preset': self.preset.pk, 'consent': True}, {'image': photo()})
                assert form.is_valid(), form.errors
                try:
                    create_request(get_user_model().objects.get(pk=self.student.pk), form)
                    return 'created'
                except ValidationError:
                    return 'blocked'
            results = self.simultaneous([send, send])
            self.assertCountEqual(results, ['created', 'blocked'])
            self.assertEqual(Request.objects.count(), 1)

    def test_two_workers_cannot_take_an_eleventh_slot(self):
        pending = []
        for index in range(11):
            owner = get_user_model().objects.create_user(username=f'slot-{index}')
            item = Request.objects.create(student=owner, title='Slot', image='unused.jpg', preset=self.preset,
                preset_name='Clay', prompt_snapshot='Clay', status='running' if index < 9 else 'approved',
                provider_mode='live', provider_task_id=f'previous-{index}' if index < 9 else None,
                reviewed_at=timezone.now())
            if index >= 9:
                pending.append(item)
        with patch('studio.tasks.Meshy') as provider:
            provider.return_value.submit.return_value = 'only-tenth-task'
            self.simultaneous([lambda: process(str(pending[0].pk)), lambda: process(str(pending[1].pk))])
            provider.return_value.submit.assert_called_once()
        self.assertEqual(Request.objects.filter(status='running').count(), 10)
        self.assertEqual(Request.objects.filter(status='approved').count(), 1)
