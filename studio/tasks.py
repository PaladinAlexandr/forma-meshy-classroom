"""Durable state machine. Celery is a wakeup mechanism; DB is the source of truth.

No task retries a POST. A lost response or stale submit is reconciled by a teacher.
Every poll/save has a lease, is repeatable and can recover after a worker restart.
"""
from datetime import timedelta
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Request, Event, Policy
from .provider import Meshy, ProviderError, PermanentProviderError, SubmissionUncertain, safe_message, archive_result, archive_image, demo_image, demo_glb, demo_fbx

LEASE = timedelta(minutes=12)  # longer than the Celery hard limit


def event(item, text):
    Event.objects.create(request=item, text=text)


WORKER_FIELDS = ['status', 'progress', 'provider_task_id', 'provider_mode', 'provider_model',
    'image_task_id', 'model_task_id', 'rig_task_id', 'image_model', 'image_credits', 'rig_credits',
    'generation_stage', 'submitted_at', 'finished_at', 'next_poll_at', 'lease_until', 'poll_failures',
    'error', 'model_glb', 'model_fbx', 'image', 'unity_package', 'rigged_fbx', 'consumed_credits', 'preview', 'updated_at']


def save_worker(item):
    # Worker snapshots must never undo a teacher's deletion/cancellation.
    with transaction.atomic():
        current = Request.objects.select_for_update().get(pk=item.pk)
        if current.status == 'canceled' or (current.cancel_requested and item.status in ('ready', 'image_ready', 'approved', 'failed')):
            item.status = 'canceled'
            item.finished_at, item.next_poll_at = timezone.now(), None
            item.error = 'Заявка закрыта преподавателем.'
        item.save(update_fields=WORKER_FIELDS)


def cancellation_requested(item):
    return Request.objects.filter(pk=item.pk, cancel_requested=True).exists()


def finish_canceled(item):
    item.status, item.finished_at, item.next_poll_at = 'canceled', timezone.now(), None
    item.error = 'Заявка закрыта преподавателем. Генерация в Meshy завершена.'
    event(item, item.error)


def sweep():
    now = timezone.now()
    Request.objects.filter(status='saving', cancel_requested=True).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lt=now)).update(
            status='canceled', finished_at=now, lease_until=None, next_poll_at=None)
    # Crash after POST may have created a paid provider task. Never resubmit.
    for pk in Request.objects.filter(status='submitting', lease_until__lt=now).values_list('pk', flat=True):
        if Request.objects.filter(pk=pk, status='submitting', lease_until__lt=now).update(
                status='uncertain', error='Отправка была прервана. Сверьте список задач в аккаунте Meshy.', lease_until=None):
            Event.objects.create(request_id=pk, text='Автоматическая отправка остановлена: требуется сверка с Meshy.')


@shared_task
def dispatch():
    sweep()
    now = timezone.now()
    ready = Request.objects.filter(status__in=['approved', 'running', 'saving']).filter(
        Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now)).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lt=now)).order_by('reviewed_at', 'created_at', 'id')
    for pk in list(ready.values_list('pk', flat=True)[:50]):
        process.delay(str(pk))


def claim(pk):
    now = timezone.now()
    with transaction.atomic():
        # Same lock order as approval; serializes global concurrency reservations.
        policy = Policy.objects.select_for_update().get(pk=1)
        item = Request.objects.select_for_update().get(pk=pk)
        if item.lease_until and item.lease_until > now:
            return None
        if item.next_poll_at and item.next_poll_at > now:
            return None
        if item.status == 'approved':
            if not policy.generation_enabled:
                return None
            oldest = Request.objects.filter(status='approved').order_by('reviewed_at', 'created_at', 'id').values_list('pk', flat=True).first()
            if oldest != item.pk:
                return None  # Late/duplicate message cannot jump ahead of another child.
            if Request.objects.filter(status__in=['submitting', 'running', 'saving', 'uncertain']).count() >= policy.max_parallel:
                return None
            item.status = 'submitting'
            if not item.image_task_id and not item.is_animation:
                item.provider_mode = settings.MESHY_MODE
            item.provider_model = settings.MESHY_AI_MODEL
            if item.generation_stage == 'image':
                item.image_model = settings.MESHY_IMAGE_MODEL
            item.submitted_at = now
        elif item.status not in ('running', 'saving'):
            return None
        item.lease_until = now + LEASE
        item.save()
        return item


@shared_task
def process(pk):
    item = claim(pk)
    if item is None:
        return
    try:
        if item.status == 'submitting':
            try:
                task_id = f'demo-{item.id}-{item.generation_stage}' if item.provider_mode == 'demo' else Meshy().submit(item)
            except SubmissionUncertain as exc:
                item.status, item.error = 'uncertain', str(exc)
                item.lease_until = None
                save_worker(item)
                event(item, 'Отправка требует проверки преподавателя. Повторная отправка заблокирована.')
                return
            except ProviderError as exc:
                item.status, item.error = 'failed', str(exc)
                item.finished_at = timezone.now()
                item.lease_until = None
                save_worker(item)
                event(item, item.error)
                return
            item.provider_task_id, item.status, item.progress = task_id, 'running', 5
            if item.generation_stage == 'image':
                item.image_task_id = task_id
            elif item.generation_stage == 'rig':
                item.rig_task_id = task_id
            elif item.generation_stage == 'model':
                item.model_task_id = task_id
            item.error = ''
            save_worker(item)
            event(item, 'Заявка принята Meshy. Началась генерация.' if item.provider_mode == 'live' else 'Началась демонстрационная генерация. Кредиты Meshy не используются.')
            item.next_poll_at = timezone.now() + timedelta(seconds=10)
            item.lease_until = None
            save_worker(item)
            return

        if item.provider_mode == 'demo':
            age = (timezone.now() - item.submitted_at).total_seconds()
            if age < 30:
                item.progress = min(95, max(10, int(age * 3)))
                item.next_poll_at = timezone.now() + timedelta(seconds=5)
            elif cancellation_requested(item):
                finish_canceled(item)
            elif item.is_animation:
                from .demo_animation import finish_demo_animation
                finish_demo_animation(item)
            elif item.generation_stage == 'image':
                if not item.image:
                    item.image.save('demo-image.jpg', ContentFile(demo_image()), save=False)
                finish_image(item, 0)
            else:
                if not item.model_glb:
                    item.model_glb.save('demo.glb', ContentFile(demo_glb()), save=False)
                if not item.model_fbx:
                    item.model_fbx.save('demo.fbx', ContentFile(demo_fbx()), save=False)
                if not item.unity_package:
                    from .unity import build_unity_package
                    build_unity_package(item, [], demo=True)
                finish(item, 0)
        else:
            result = Meshy().poll(item)
            if result['status'] == 'SUCCEEDED':
                item.status = 'saving'
                item.save(update_fields=['status', 'updated_at'])
                if cancellation_requested(item):
                    finish_canceled(item)
                elif item.generation_stage == 'rig':
                    from .animations import finish_rig
                    finish_rig(item, result)
                elif item.generation_stage == 'animation':
                    from .animations import archive_animation
                    archive_animation(item, result)
                elif item.generation_stage == 'image':
                    archive_image(item, result)
                    finish_image(item, result.get('consumed_credits'))
                else:
                    archive_result(item, result)
                    finish(item, result.get('consumed_credits'))
            elif result['status'] in ('FAILED', 'CANCELED'):
                item.status = 'failed'
                detail = result.get('task_error') or {}
                detail = safe_message(detail.get('message', detail.get('error_code', '')) if isinstance(detail, dict) else detail)
                item.error = ('Meshy не смог подготовить скелет или анимацию. Для риггинга нужен текстурированный гуманоид с чёткими руками и ногами в T/A-позе.' if item.is_animation else 'Meshy не смог выполнить генерацию. Преподаватель поможет подготовить новую заявку.')
                if detail:
                    item.error += ' Meshy: ' + detail
                item.finished_at = timezone.now()
                event(item, item.error)
            else:
                item.progress = max(item.progress, min(99, int(result.get('progress', 0))))
                item.next_poll_at = timezone.now() + timedelta(seconds=15)
            item.poll_failures = 0
            item.error = '' if item.status not in ('failed', 'canceled') else item.error
        item.lease_until = None
        save_worker(item)
    except PermanentProviderError as exc:
        item.refresh_from_db()
        item.status, item.error = 'failed', str(exc)[:1000]
        item.finished_at, item.next_poll_at, item.lease_until = timezone.now(), None, None
        save_worker(item)
        event(item, item.error)
    except Exception as exc:
        # Do not downgrade/overwrite a committed task ID after a partial failure.
        item.refresh_from_db()
        if item.status == 'submitting':
            item.status = 'uncertain'
            item.error = 'Не удалось подтвердить отправку. Преподавателю нужно сверить задачи Meshy.'
        else:
            item.poll_failures += 1
            item.error = str(exc)[:500] if isinstance(exc, ProviderError) else 'Временная ошибка обработки. Автоматически повторим проверку.'
            item.next_poll_at = timezone.now() + timedelta(seconds=min(900, 15 * 2 ** min(item.poll_failures, 6)))
            if item.status == 'saving' and item.poll_failures >= 8:
                item.status, item.finished_at, item.next_poll_at = 'failed', timezone.now(), None
                item.error = 'Не удалось сохранить готовый результат после 8 проверок. Заявка завершена; можно создать новую. ' + item.error
        item.lease_until = None
        save_worker(item)


def finish(item, credits):
    item.status, item.progress = 'ready', 100
    item.finished_at = timezone.now()
    known_image_cost = item.source != 'text' or item.image_credits is not None
    item.consumed_credits = credits + (item.image_credits or 0) if credits is not None and known_image_cost else None
    item.error = ''
    item.next_poll_at = None
    event(item, 'Модель готова! Файлы сохранены в мастерской и доступны для скачивания.')


def finish_image(item, credits):
    item.status, item.progress = 'image_ready', 100
    item.image_credits = credits
    item.error = ''
    item.next_poll_at = None
    event(item, 'Изображение сохранено. Ученик может выбрать его для 3D или завершить проект. Создание 3D потребует отдельного одобрения преподавателя.')
