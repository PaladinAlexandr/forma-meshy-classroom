from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .access import is_teacher
from .models import Request, Event, Policy, UNFINISHED_STATUSES


@transaction.atomic
def create_request(student, form):
    get_user_model().objects.select_for_update().get(pk=student.pk)
    if Request.objects.filter(student=student, status__in=UNFINISHED_STATUSES).exists():
        raise ValidationError('У тебя уже есть незавершённый проект. Дождись результата или решения преподавателя, прежде чем отправлять новую заявку.')
    policy = Policy.objects.get(pk=1)
    today = timezone.localdate()
    count = Request.objects.filter(student=student, created_at__date=today).count()
    if count >= policy.daily_student_limit:
        raise ValidationError('На сегодня лимит заявок исчерпан. Продолжим завтра!')
    item = form.save(commit=False)
    item.student = student
    item.generation_stage = 'image' if item.source == 'text' else 'model'
    item.text_prompt = item.text_prompt.strip()
    item.prompt_snapshot = item.text_prompt if item.source == 'photo' and item.text_prompt else item.preset.texture_prompt
    item.preset_name = item.preset.name
    item.save()
    Event.objects.create(request=item, actor=student, text='Заявка отправлена преподавателю на проверку.')
    return item


@transaction.atomic
def review_request(item_id, reviewer, decision, note=''):
    if not is_teacher(reviewer):
        raise ValidationError('Недостаточно прав.')
    policy = Policy.objects.select_for_update().get(pk=1)
    item = Request.objects.select_for_update().get(pk=item_id)
    if item.status != Request.Status.PENDING:
        raise ValidationError('Решение по этой заявке уже принято. Обновите страницу.')
    if decision not in ('approve', 'reject'):
        raise ValidationError('Неизвестное решение.')
    if decision == 'reject' and not note.strip():
        raise ValidationError('Укажите причину возврата.')
    if decision == 'approve':
        if item.is_animation and item.model_kind != 'humanoid':
            raise ValidationError('Анимация доступна только для гуманоидов.')
        if Request.objects.filter(student=item.student, status__in=UNFINISHED_STATUSES).exclude(pk=item.pk).exists():
            raise ValidationError('У ученика уже есть незавершённый проект. Сначала завершите предыдущий.')
        month = timezone.localdate().replace(day=1)
        used = monthly_usage(month)
        if not item.first_approved_at and used >= policy.monthly_limit:
            raise ValidationError('Месячный лимит одобрений исчерпан. Измените лимит в настройках центра.')
        if item.first_approved_at is None:
            item.first_approved_at = timezone.now()
        item.status = Request.Status.APPROVED
    else:
        item.status = Request.Status.REJECTED
    item.review_note = note.strip()
    item.reviewer = reviewer
    item.reviewed_at = timezone.now()
    item.save()
    text = (f'Преподаватель одобрил заявку. Этап: {item.get_generation_stage_display()}. Она поставлена в очередь генерации.'
            if decision == 'approve' else f'Преподаватель вернул заявку: {note.strip()}')
    if decision == 'approve' and note.strip():
        text += f' Комментарий: {note.strip()}'
    Event.objects.create(request=item, actor=reviewer, text=text[:1000])
    return item


def monthly_usage(month):
    return Request.objects.filter(Q(first_approved_at__date__gte=month) | (
        Q(first_approved_at__isnull=True, reviewed_at__date__gte=month) & ~Q(status__in=['pending', 'rejected']))).count()


@transaction.atomic
def choose_generated_image(item_id, student, accept):
    item = Request.objects.select_for_update().get(pk=item_id, student=student)
    if item.status != 'image_ready' or not item.image or item.generation_stage != 'image':
        raise ValidationError('Изображение уже обработано или ещё не готово.')
    if accept:
        item.generation_stage = 'model'
        item.status = 'pending'
        item.provider_task_id = None
        item.progress = 0
        item.submitted_at = None
        item.next_poll_at = None
        item.lease_until = None
        text = 'Ученик выбрал изображение. Преподавателю нужно разрешить создание 3D-модели.'
    else:
        item.status = 'rejected'
        item.finished_at = timezone.now()
        text = 'Ученик завершил проект без 3D-модели. Можно отправить новую идею.'
    item.error = ''
    item.save()
    Event.objects.create(request=item, actor=student, text=text)
    return item
