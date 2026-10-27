import io
import tempfile
from datetime import timedelta
from unittest.mock import Mock, patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from studio.forms import RequestForm
from studio.models import Request, Policy, Preset
from studio.provider import Meshy, ProviderError, SubmissionUncertain, safe_asset_url, demo_glb
from studio.services import create_request, review_request
from studio.tasks import process, sweep


def photo():
    out = io.BytesIO()
    img = Image.new('RGB', (100, 100), 'green')
    exif = img.getexif(); exif[315] = 'private author'
    img.save(out, 'JPEG', exif=exif)
    return SimpleUploadedFile('photo.jpg', out.getvalue(), 'image/jpeg')


class WorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.override = override_settings(MEDIA_ROOT=cls.media.name,
            MESHY_MODE='demo', MESHY_API_KEY='',
            SECURE_SSL_REDIRECT=False,
            CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
            STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                      'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
        cls.override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.override.disable(); cls.media.cleanup()

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.student = User.objects.create_user(username='student', password='Test-classroom-942!')
        self.other = User.objects.create_user(username='other', password='Test-classroom-942!')
        self.teacher = User.objects.create_user(username='teacher', password='Test-classroom-942!')
        self.teacher.user_permissions.add(Permission.objects.get(codename='moderate_request'))
        Policy.objects.create(pk=1, monthly_limit=3, daily_student_limit=2)
        self.preset = Preset.objects.create(name='Clay', description='Clay toy', texture_prompt='Matte clay texture.')

    def make_form(self, **values):
        data = {'title': 'Robot', 'note': '<script>alert(1)</script>', 'preset': self.preset.pk, 'consent': True}
        data.update(values)
        return RequestForm(data, {'image': photo()})

    def item(self, owner=None):
        form = self.make_form()
        self.assertTrue(form.is_valid(), form.errors)
        return create_request(owner or self.student, form)

    def approve(self, item):
        return review_request(item.pk, self.teacher, 'approve', 'Great idea!')

    def test_student_cannot_access_another_students_record_or_photo_or_model(self):
        item = self.item(self.other)
        self.client.force_login(self.student)
        for url in [reverse('detail', args=[item.pk]), reverse('file', args=[item.pk, 'image']), reverse('file', args=[item.pk, 'glb'])]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get('/'), item.title)

    def test_teacher_sees_and_reviews_all_requests(self):
        item = self.item(self.other)
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse('detail', args=[item.pk])), item.title)
        response = self.client.post(reverse('review', args=[item.pk]), {'decision': 'approve', 'note': 'OK'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db(); self.assertEqual(item.status, 'approved')

    def test_student_cannot_review_or_create_users_or_change_policy(self):
        item = self.item()
        self.client.force_login(self.student)
        for url in [reverse('review', args=[item.pk]), '/students/', '/settings/']:
            self.assertEqual(self.client.post(url, {'decision': 'approve'}).status_code, 403)
        item.refresh_from_db(); self.assertEqual(item.status, 'pending')

    def test_csrf_is_required_for_approval_and_submission(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post(reverse('review', args=[self.item().pk]), {'decision': 'approve'}).status_code, 403)
        client.force_login(self.student)
        self.assertEqual(client.post('/new/', {}).status_code, 403)

    def test_rejection_needs_reason_and_stays_out_of_provider(self):
        item = self.item()
        with self.assertRaises(ValidationError):
            review_request(item.pk, self.teacher, 'reject', '  ')
        review_request(item.pk, self.teacher, 'reject', 'Upload a clearer photo')
        with patch('studio.tasks.Meshy') as provider:
            process(str(item.pk)); provider.assert_not_called()
        item.refresh_from_db(); self.assertEqual(item.review_note, 'Upload a clearer photo')

    def test_pending_request_never_calls_meshy(self):
        with patch('studio.tasks.Meshy') as provider:
            process(str(self.item().pk)); provider.assert_not_called()

    def test_repeated_approval_is_rejected(self):
        item = self.approve(self.item())
        with self.assertRaises(ValidationError):
            review_request(item.pk, self.teacher, 'approve')
        self.assertEqual(item.events.filter(text__contains='одобрил').count(), 1)

    def test_daily_submission_limit(self):
        first = self.item(); Request.objects.filter(pk=first.pk).update(status='rejected')
        second = self.item(); Request.objects.filter(pk=second.pk).update(status='rejected')
        form = self.make_form(); self.assertTrue(form.is_valid())
        with self.assertRaises(ValidationError):
            create_request(self.student, form)

    def test_monthly_generation_limit_includes_failures(self):
        Policy.objects.filter(pk=1).update(monthly_limit=1)
        first = self.approve(self.item())
        Request.objects.filter(pk=first.pk).update(status='failed')
        with self.assertRaises(ValidationError):
            self.approve(self.item())

    def test_image_is_reencoded_and_exif_removed(self):
        item = self.item()
        with item.image.open('rb') as file:
            img = Image.open(file)
            self.assertEqual(img.format, 'JPEG')
            self.assertEqual(dict(img.getexif()), {})

    def test_invalid_image_or_disabled_preset_rejected(self):
        form = RequestForm({'title': 'x', 'preset': self.preset.pk, 'consent': True},
                           {'image': SimpleUploadedFile('evil.jpg', b'<script>bad</script>', 'image/jpeg')})
        self.assertFalse(form.is_valid())
        self.preset.active = False; self.preset.save()
        self.assertFalse(self.make_form().is_valid())

    def test_preset_snapshot_does_not_change_after_review(self):
        item = self.item()
        Preset.objects.filter(pk=self.preset.pk).update(texture_prompt='changed', name='changed')
        item.refresh_from_db()
        self.assertEqual(item.prompt_snapshot, 'Matte clay texture.')
        self.assertEqual(item.preset_name, 'Clay')

    def test_notes_are_escaped_in_page(self):
        item = self.item(); self.client.force_login(self.teacher)
        response = self.client.get(reverse('detail', args=[item.pk]))
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    @override_settings(MESHY_MODE='live', MESHY_API_KEY='test-secret')
    def test_duplicate_worker_delivery_creates_one_paid_task(self):
        item = self.approve(self.item())
        with patch('studio.tasks.Meshy') as provider:
            provider.return_value.submit.return_value = 'provider-task-001'
            process(str(item.pk)); process(str(item.pk))
            provider.return_value.submit.assert_called_once()
        item.refresh_from_db(); self.assertEqual(item.provider_task_id, 'provider-task-001')

    @override_settings(MESHY_MODE='live', MESHY_API_KEY='test-secret')
    def test_timeout_on_submit_is_never_retried(self):
        item = self.approve(self.item())
        with patch('studio.tasks.Meshy') as provider:
            provider.return_value.submit.side_effect = SubmissionUncertain('Timeout')
            process(str(item.pk)); process(str(item.pk))
            provider.return_value.submit.assert_called_once()
        item.refresh_from_db(); self.assertEqual(item.status, 'uncertain')

    def test_stale_submit_recovers_to_uncertain_without_new_post(self):
        item = self.item()
        Request.objects.filter(pk=item.pk).update(status='submitting', lease_until=timezone.now() - timedelta(seconds=1))
        sweep(); item.refresh_from_db(); self.assertEqual(item.status, 'uncertain')

    @override_settings(MESHY_MODE='live')
    def test_poll_failure_is_retryable_and_keeps_provider_id(self):
        item = self.item()
        Request.objects.filter(pk=item.pk).update(status='running', provider_mode='live', provider_task_id='task-123')
        with patch('studio.tasks.Meshy') as provider:
            provider.return_value.poll.side_effect = ProviderError('Temporary')
            process(str(item.pk))
        item.refresh_from_db()
        self.assertEqual(item.status, 'running'); self.assertEqual(item.provider_task_id, 'task-123')
        self.assertIsNotNone(item.next_poll_at); self.assertIsNone(item.lease_until)

    def test_parallel_limit_and_pause_prevent_new_post(self):
        one, two = self.item(), self.item(self.other)
        self.approve(one); self.approve(two)
        Policy.objects.filter(pk=1).update(max_parallel=1)
        process(str(one.pk)); process(str(two.pk))
        two.refresh_from_db(); self.assertEqual(two.status, 'approved')
        Policy.objects.filter(pk=1).update(generation_enabled=False, max_parallel=2)
        process(str(two.pk)); two.refresh_from_db(); self.assertEqual(two.status, 'approved')

    @override_settings(MESHY_MODE='live')
    def test_ready_is_not_reported_until_files_are_archived(self):
        item = self.item()
        Request.objects.filter(pk=item.pk).update(status='running', provider_mode='live', provider_task_id='task-123')
        with patch('studio.tasks.Meshy') as provider, patch('studio.tasks.archive_result', side_effect=ProviderError('Download failed')):
            provider.return_value.poll.return_value = {'status': 'SUCCEEDED', 'model_urls': {}}
            process(str(item.pk))
        item.refresh_from_db(); self.assertEqual(item.status, 'saving')
        self.assertFalse(item.model_glb)

    @override_settings(MESHY_MODE='demo')
    def test_full_demo_from_approval_to_private_download(self):
        item = self.approve(self.item())
        process(str(item.pk))
        Request.objects.filter(pk=item.pk).update(submitted_at=timezone.now() - timedelta(seconds=45), next_poll_at=None)
        process(str(item.pk)); item.refresh_from_db()
        self.assertEqual(item.status, 'ready'); self.assertTrue(item.model_glb); self.assertTrue(item.model_fbx)
        self.assertTrue(item.unity_package)
        self.client.force_login(self.student)
        response = self.client.get(reverse('file', args=[item.pk, 'glb']) + '?download=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content)[:4], b'glTF')
        self.assertIn('attachment', response['Content-Disposition'])
        response = self.client.get(reverse('file', args=[item.pk, 'fbx']))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'FBX 7.4.0', b''.join(response.streaming_content))
        response = self.client.get(reverse('file', args=[item.pk, 'unity']))
        import zipfile
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            self.assertTrue(any(name.endswith('model.fbx') for name in archive.namelist()))
            self.assertTrue(any(name.endswith('IMPORT_IN_UNITY.txt') for name in archive.namelist()))
        self.assertEqual(self.client.get(reverse('file', args=[item.pk, 'stl'])).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('file', args=[item.pk, 'glb'])).status_code, 404)

    def test_live_mode_has_no_demo_signin(self):
        with override_settings(MESHY_MODE='live', DEBUG=True):
            self.assertEqual(self.client.post('/demo/teacher/').status_code, 404)
        with override_settings(MESHY_MODE='demo', DEBUG=False):
            self.assertEqual(self.client.post('/demo/teacher/').status_code, 404)

    def test_classroom_demo_uses_ordinary_accounts_not_demo_login_buttons(self):
        with override_settings(MESHY_MODE='demo', DEBUG=False):
            response = self.client.get('/login/')
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Я преподаватель')
            self.assertNotContains(response, 'Я ученик')

    def test_installer_status_reports_readiness_without_exposing_api_key(self):
        import json
        output = io.StringIO()
        with override_settings(MESHY_MODE='live', MESHY_API_KEY='private-key-never-print'):
            call_command('setup_status', stdout=output)
        self.assertNotIn('private-key-never-print', output.getvalue())
        data = json.loads(output.getvalue())
        self.assertFalse(data['admin_exists'])
        self.assertTrue(data['api_configured'])
        self.assertEqual(data['mode'], 'live')
        get_user_model().objects.create_superuser(username='setup-admin', password='Admin-setup-983!')
        output = io.StringIO()
        call_command('setup_status', stdout=output)
        self.assertTrue(json.loads(output.getvalue())['admin_exists'])

    def test_login_throttled(self):
        for _ in range(12):
            self.client.post('/login/', {'username': 'nonexistent', 'password': 'wrong'})
        self.assertEqual(self.client.post('/login/', {'username': 'nonexistent', 'password': 'wrong'}).status_code, 429)

    def test_server_screens_render_for_both_roles(self):
        item = self.item()
        self.client.force_login(self.teacher)
        for url in ['/', '/students/', '/settings/', reverse('detail', args=[item.pk]), '/password/']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.force_login(self.student)
        for url in ['/', '/new/', reverse('detail', args=[item.pk]), '/updates/']:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_update_revision_is_scoped_to_student(self):
        self.client.force_login(self.student)
        before = self.client.get('/updates/').json()
        self.item(self.other)
        self.assertEqual(self.client.get('/updates/').json(), before)
        self.item(self.student)
        self.assertNotEqual(self.client.get('/updates/').json(), before)

    @override_settings(MESHY_API_KEY='super-secret-key')
    def test_api_payload_has_moderation_and_style_but_not_student_note(self):
        item = self.item(); item.provider_model = 'meshy-7.1'
        with patch('studio.provider.requests.Session') as factory:
            response = Mock(status_code=200); response.json.return_value = {'result': 'new-meshy-task'}
            factory.return_value.post.return_value = response
            self.assertEqual(Meshy().submit(item), 'new-meshy-task')
            payload = factory.return_value.post.call_args.kwargs['json']
            self.assertTrue(payload['moderation'])
            self.assertEqual(payload['texture_prompt'], item.prompt_snapshot)
            self.assertEqual(payload['target_formats'], ['glb', 'fbx'])
            self.assertTrue(payload['enable_pbr'])
            self.assertTrue(payload['image_url'].startswith('data:image/jpeg;base64,'))
            self.assertNotIn(item.note, str(payload)); self.assertNotIn('super-secret-key', str(payload))

    def test_non_meshy_and_private_asset_hosts_are_blocked(self):
        for url in ['http://assets.meshy.ai/file', 'https://evil.test/file', 'https://assets.meshy.ai.evil.test/x',
                    'https://127.0.0.1/x', 'https://assets.meshy.ai:444/x', 'https://user@assets.meshy.ai/x']:
            with self.assertRaises(ProviderError): safe_asset_url(url)
        with patch('studio.provider.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ProviderError): safe_asset_url('https://assets.meshy.ai/model.glb')

    def test_demo_glb_header_length_is_valid(self):
        import struct
        blob = demo_glb()
        self.assertEqual(struct.unpack('<I', blob[8:12])[0], len(blob))

    def test_second_request_blocked_in_every_unfinished_state(self):
        from studio.models import UNFINISHED_STATUSES
        item = self.item()
        for status in UNFINISHED_STATUSES:
            with self.subTest(status=status):
                Request.objects.filter(pk=item.pk).update(status=status)
                form = self.make_form(); self.assertTrue(form.is_valid())
                with self.assertRaises(ValidationError):
                    create_request(self.student, form)
        self.client.force_login(self.student)
        self.assertContains(self.client.get('/new/'), 'Одна идея')
        self.client.post('/new/', {'title': 'Another', 'preset': self.preset.pk, 'consent': True, 'image': photo()})
        self.assertEqual(Request.objects.filter(student=self.student).count(), 1)

    def test_database_itself_prevents_two_unfinished_requests(self):
        from django.db import IntegrityError, transaction
        first = self.item()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Request.objects.create(student=self.student, title='Bypass', preset=self.preset,
                preset_name='Clay', prompt_snapshot='Clay', image=first.image.name, status='approved')

    def test_database_limits_provider_capacity_to_ten(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Policy.objects.filter(pk=1).update(max_parallel=11)

    def test_completed_rejected_or_failed_request_allows_next_idea(self):
        for index, status in enumerate(('ready', 'rejected', 'failed')):
            with self.subTest(status=status):
                owner = get_user_model().objects.create_user(username=f'finished-{index}')
                first = self.item(owner)
                Request.objects.filter(pk=first.pk).update(status=status)
                self.assertEqual(self.item(owner).status, 'pending')

    @override_settings(MESHY_MODE='live', MESHY_API_KEY='test-key')
    def test_ten_students_get_ten_slots_and_eleventh_waits(self):
        Policy.objects.filter(pk=1).update(max_parallel=10, monthly_limit=100)
        items = []
        for index in range(11):
            owner = get_user_model().objects.create_user(username=f'pupil-{index}')
            items.append(self.approve(self.item(owner)))
        with patch('studio.tasks.Meshy') as factory:
            factory.return_value.submit.side_effect = [f'task-for-student-{i}' for i in range(10)]
            for item in items:
                process(str(item.pk))
            self.assertEqual(factory.return_value.submit.call_count, 10)
        self.assertEqual(Request.objects.filter(status='running').count(), 10)
        items[-1].refresh_from_db(); self.assertEqual(items[-1].status, 'approved')
        Request.objects.filter(pk=items[0].pk).update(status='ready')
        with patch('studio.tasks.Meshy') as factory:
            factory.return_value.submit.return_value = 'eleventh-task'
            process(str(items[-1].pk)); factory.return_value.submit.assert_called_once()

    def test_newer_approval_cannot_jump_fifo_order(self):
        older = self.approve(self.item())
        newer = self.approve(self.item(self.other))
        process(str(newer.pk)); newer.refresh_from_db()
        self.assertEqual(newer.status, 'approved')
        process(str(older.pk)); process(str(newer.pk)); newer.refresh_from_db()
        self.assertEqual(newer.status, 'running')

    def test_urp_packing_inverts_roughness_and_preserves_metallic(self):
        from studio.unity import pack_urp
        metallic = Image.new('RGBA', (2, 2), (64, 64, 64, 255))
        roughness = Image.new('RGBA', (2, 2), (50, 50, 50, 255))
        packed = pack_urp(metallic, roughness)
        self.assertEqual(packed.getpixel((0, 0)), (64, 255, 0, 205))

    def test_unity_zip_contains_fbx_all_maps_and_converted_urp_map(self):
        import zipfile
        from studio.unity import build_unity_package
        from studio.provider import demo_fbx
        item = self.item()
        item.model_fbx.save('model.fbx', ContentFile(demo_fbx()))
        image = Image.new('RGBA', (2, 2), (100, 100, 100, 255))
        with patch('studio.unity.texture_bytes', return_value=image):
            build_unity_package(item, [{'base_color': 'one', 'normal': 'two', 'metallic': 'three', 'roughness': 'four'}])
        with item.unity_package.open('rb') as file, zipfile.ZipFile(file) as archive:
            names = archive.namelist()
            self.assertEqual(sum(n.endswith('.png') for n in names), 5)
            self.assertTrue(any(n.endswith('model.fbx') for n in names))
            converted = next(n for n in names if n.endswith('urp_metallic_smoothness.png'))
            packed = Image.open(io.BytesIO(archive.read(converted)))
            self.assertEqual(packed.getpixel((0, 0)), (100, 255, 0, 155))


    def text_item(self, owner=None):
        form = RequestForm({'title': 'Text robot', 'source': 'text', 'text_prompt': 'Добрый синий робот на колёсах',
                            'preset': self.preset.pk, 'consent': True})
        self.assertTrue(form.is_valid(), form.errors)
        return create_request(owner or self.student, form)

    def image_ready(self, item):
        self.approve(item)
        process(str(item.pk))
        Request.objects.filter(pk=item.pk).update(submitted_at=timezone.now() - timedelta(seconds=45), next_poll_at=None)
        process(str(item.pk))
        item.refresh_from_db()
        self.assertEqual(item.status, 'image_ready')
        return item

    def test_text_request_needs_prompt_but_no_upload(self):
        item = self.text_item()
        self.assertFalse(item.image)
        self.assertEqual(item.generation_stage, 'image')
        self.assertEqual(item.prompt_snapshot, self.preset.texture_prompt)
        form = RequestForm({'title': 'x', 'source': 'text', 'text_prompt': ' ', 'preset': self.preset.pk, 'consent': True})
        self.assertFalse(form.is_valid())
        self.assertIn('text_prompt', form.errors)
        form = RequestForm({'title': 'x', 'source': 'photo', 'preset': self.preset.pk, 'consent': True})
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_photo_custom_prompt_overrides_texture_preset_and_is_snapshotted(self):
        form = self.make_form(text_prompt='Жёлтый матовый пластик')
        self.assertTrue(form.is_valid(), form.errors)
        item = create_request(self.student, form)
        self.assertEqual(item.prompt_snapshot, 'Жёлтый матовый пластик')
        self.assertEqual(item.generation_stage, 'model')

    def test_text_screens_render_before_and_after_image(self):
        item = self.text_item()
        for user in (self.student, self.teacher):
            self.client.force_login(user)
            self.assertContains(self.client.get('/'), item.text_prompt)
            self.assertContains(self.client.get(reverse('detail', args=[item.pk])), item.text_prompt)
        self.assertEqual(self.client.get(reverse('file', args=[item.pk, 'image'])).status_code, 404)
        self.image_ready(item)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse('detail', args=[item.pk])), 'Хочу 3D по этой картинке')
        self.assertEqual(self.client.get(reverse('file', args=[item.pk, 'image'])).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('file', args=[item.pk, 'image'])).status_code, 404)

    def test_text_demo_requires_student_choice_and_second_teacher_approval(self):
        from studio.services import choose_generated_image, monthly_usage
        Policy.objects.filter(pk=1).update(monthly_limit=1)
        item = self.image_ready(self.text_item())
        self.assertFalse(item.model_fbx)
        self.assertTrue(item.image_task_id)
        self.assertEqual(item.image_credits, 0)
        process(str(item.pk)); item.refresh_from_db()
        self.assertEqual(item.status, 'image_ready')
        choose_generated_image(item.pk, self.student, True)
        item.refresh_from_db()
        self.assertEqual(item.generation_stage, 'model')
        self.assertEqual(item.status, 'pending')
        self.assertIsNone(item.provider_task_id)
        process(str(item.pk)); item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.approve(item)
        self.assertEqual(monthly_usage(timezone.localdate().replace(day=1)), 1)
        process(str(item.pk))
        Request.objects.filter(pk=item.pk).update(submitted_at=timezone.now() - timedelta(seconds=45), next_poll_at=None)
        process(str(item.pk)); item.refresh_from_db()
        self.assertEqual(item.status, 'ready')
        self.assertTrue(item.unity_package)
        self.assertNotEqual(item.provider_task_id, item.image_task_id)

    def test_image_choice_owner_only_post_csrf_and_no_duplicate_transition(self):
        item = self.image_ready(self.text_item())
        url = reverse('choose_image', args=[item.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {'decision': 'accept'}).status_code, 404)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(url, {'decision': 'accept'}).status_code, 404)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.student)
        self.assertEqual(strict.post(url, {'decision': 'accept'}).status_code, 403)
        self.client.post(url, {'decision': 'accept'})
        self.client.post(url, {'decision': 'accept'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.assertEqual(item.events.filter(text__contains='Ученик выбрал').count(), 1)

    def test_closing_image_frees_student_but_keeps_monthly_usage(self):
        from studio.services import choose_generated_image, monthly_usage
        item = self.image_ready(self.text_item())
        choose_generated_image(item.pk, self.student, False)
        item.refresh_from_db()
        self.assertEqual(item.status, 'rejected')
        self.assertEqual(monthly_usage(timezone.localdate().replace(day=1)), 1)
        self.assertEqual(self.item().status, 'pending')

    def test_ready_image_releases_global_slot_but_blocks_its_student(self):
        Policy.objects.filter(pk=1).update(max_parallel=1)
        item = self.image_ready(self.text_item())
        with self.assertRaises(ValidationError): self.item()
        other = self.approve(self.item(self.other))
        process(str(other.pk)); other.refresh_from_db()
        self.assertEqual(other.status, 'running')

    @override_settings(MESHY_API_KEY='test-secret')
    def test_text_api_payload_and_poll_use_text_to_image_endpoint(self):
        item = self.text_item(); item.image_model = 'nano-banana'
        with patch('studio.provider.requests.Session') as factory:
            response = Mock(status_code=200); response.json.return_value = {'result': 'image-task-001'}
            factory.return_value.post.return_value = response
            self.assertEqual(Meshy().submit(item), 'image-task-001')
            call = factory.return_value.post.call_args
            self.assertTrue(call.args[0].endswith('/text-to-image'))
            self.assertEqual(call.kwargs['json']['prompt'], item.text_prompt)
            self.assertEqual(call.kwargs['json']['ai_model'], 'nano-banana')
            self.assertNotIn('image_url', call.kwargs['json'])
            item.provider_task_id = 'image-task-001'
            factory.return_value.get.return_value.json.return_value = {'status': 'IN_PROGRESS'}
            Meshy().poll(item)
            self.assertTrue(factory.return_value.get.call_args.args[0].endswith('/text-to-image/image-task-001'))

    @override_settings(MESHY_MODE='live', MESHY_API_KEY='test-secret')
    def test_image_post_timeout_is_uncertain_never_resent(self):
        item = self.approve(self.text_item())
        with patch('studio.tasks.Meshy') as factory:
            factory.return_value.submit.side_effect = SubmissionUncertain('Timeout')
            process(str(item.pk)); process(str(item.pk))
            factory.return_value.submit.assert_called_once()
        item.refresh_from_db()
        self.assertEqual(item.status, 'uncertain')
        self.assertEqual(item.generation_stage, 'image')

    @override_settings(MESHY_MODE='live', MESHY_API_KEY='test-secret')
    def test_image_archive_failure_retries_get_not_post(self):
        item = self.text_item()
        Request.objects.filter(pk=item.pk).update(status='running', provider_mode='live', provider_task_id='image-123')
        with patch('studio.tasks.Meshy') as factory, patch('studio.tasks.archive_image', side_effect=ProviderError('temporary')):
            factory.return_value.poll.return_value = {'status': 'SUCCEEDED', 'image_urls': ['x']}
            process(str(item.pk))
            item.refresh_from_db()
            self.assertEqual(item.status, 'saving')
            self.assertEqual(item.provider_task_id, 'image-123')
            factory.return_value.submit.assert_not_called()

    def test_archive_image_downloads_normalizes_and_keeps_private(self):
        from studio.provider import archive_image
        item = self.text_item()
        buffer = io.BytesIO(); Image.new('RGBA', (8, 8), (20, 80, 60, 255)).save(buffer, 'PNG')
        def download(target, field, url, filename, max_size):
            self.assertEqual(max_size, 10 * 1024 * 1024)
            target.image.save(filename, ContentFile(buffer.getvalue()), save=True)
        with patch('studio.provider.download_to_field', side_effect=download) as mocked:
            archive_image(item, {'image_urls': ['https://assets.meshy.ai/source.png']})
            mocked.assert_called_once()
        with item.image.open('rb') as source:
            self.assertEqual(Image.open(source).format, 'JPEG')
        self.assertEqual(item.status, 'pending')

    def test_credit_total_includes_image_and_model(self):
        from studio.tasks import finish
        item = self.text_item(); item.image_credits = 3
        finish(item, 20)
        self.assertEqual(item.consumed_credits, 23)


    def test_new_form_exposes_source_and_prompt_without_required_upload(self):
        self.client.force_login(self.student)
        response = self.client.get('/new/')
        self.assertContains(response, 'id_source')
        self.assertContains(response, 'id_text_prompt')
        self.assertEqual(response.context['form'].fields['image'].required, False)
        response = self.client.post('/new/', {'title': 'Robot idea', 'source': 'text',
            'text_prompt': '<script>alert(5)</script>', 'preset': self.preset.pk, 'consent': 'on'})
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(response.url)
        self.assertContains(detail, '&lt;script&gt;alert(5)&lt;/script&gt;')
        self.assertNotContains(detail, '<script>alert(5)</script>')

    @override_settings(MESHY_MODE='live', MESHY_API_KEY='test-secret')
    def test_live_image_then_model_uses_two_approved_submissions(self):
        from studio.services import choose_generated_image
        item = self.approve(self.text_item())
        def archived_image(target, result):
            from studio.provider import demo_image
            target.image.save('result.jpg', ContentFile(demo_image()), save=True)
        def archived_model(target, result):
            from studio.provider import demo_fbx
            target.model_glb.save('result.glb', ContentFile(demo_glb()), save=True)
            target.model_fbx.save('result.fbx', ContentFile(demo_fbx()), save=True)
            target.unity_package.save('result.zip', ContentFile(b'zip-fixture'), save=True)
        with patch('studio.tasks.Meshy') as factory, patch('studio.tasks.archive_image', side_effect=archived_image), patch('studio.tasks.archive_result', side_effect=archived_model):
            factory.return_value.submit.side_effect = ['image-task-live', 'model-task-live']
            factory.return_value.poll.return_value = {'status': 'SUCCEEDED', 'consumed_credits': 3}
            process(str(item.pk))
            Request.objects.filter(pk=item.pk).update(next_poll_at=None)
            process(str(item.pk)); item.refresh_from_db()
            self.assertEqual(item.status, 'image_ready')
            process(str(item.pk))
            self.assertEqual(factory.return_value.submit.call_count, 1)
            choose_generated_image(item.pk, self.student, True)
            process(str(item.pk))
            self.assertEqual(factory.return_value.submit.call_count, 1)
            self.approve(item)
            process(str(item.pk))
            self.assertEqual(factory.return_value.submit.call_count, 2)
            factory.return_value.poll.return_value = {'status': 'SUCCEEDED', 'consumed_credits': 20}
            Request.objects.filter(pk=item.pk).update(next_poll_at=None)
            process(str(item.pk)); item.refresh_from_db()
            self.assertEqual(item.status, 'ready')
            self.assertEqual(item.image_task_id, 'image-task-live')
            self.assertEqual(item.provider_task_id, 'model-task-live')
            self.assertEqual(item.consumed_credits, 23)
