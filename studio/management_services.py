from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .access import is_teacher, student_accounts
from .models import Event, Policy, Request, StudentRecord


@transaction.atomic
def close_request(pk, actor, hide=False):
    if not is_teacher(actor):
        raise ValidationError('Недостаточно прав.')
    Policy.objects.select_for_update().get(pk=1)
    item = Request.objects.select_for_update().get(pk=pk)
    if hide and item.status in ('ready', 'failed', 'rejected', 'canceled'):
        item.deleted_at = timezone.now()
        item.save(update_fields=['deleted_at', 'updated_at'])
        Event.objects.create(request=item, actor=actor, text='Заявка перемещена в корзину. Результаты сохранены.')
        return item
    item.cancel_requested = True
    if hide:
        item.deleted_at = timezone.now()
    leased = item.lease_until and item.lease_until > timezone.now()
    waiting = item.status in ('submitting', 'running', 'uncertain') or (item.status == 'saving' and leased)
    if not waiting:
        item.status, item.finished_at = 'canceled', timezone.now()
        item.next_poll_at, item.lease_until = None, None
        item.error = 'Заявка закрыта преподавателем.'
    item.save(update_fields=['cancel_requested', 'deleted_at', 'status', 'finished_at', 'next_poll_at', 'lease_until', 'error', 'updated_at'])
    text = 'Заявка перемещена в корзину.' if hide else 'Преподаватель запросил завершение обработки.'
    if waiting:
        text += ' Уже отправленная задача отслеживается до завершения; новые платные этапы не запускаются.'
    Event.objects.create(request=item, actor=actor, text=text)
    return item


@transaction.atomic
def restore_request(pk, actor):
    if not is_teacher(actor):
        raise ValidationError('Недостаточно прав.')
    item = Request.objects.select_for_update().get(pk=pk)
    item.deleted_at = None
    item.save(update_fields=['deleted_at', 'updated_at'])
    Event.objects.create(request=item, actor=actor, text='Заявка восстановлена из корзины. Генерация повторно не отправляется.')
    return item


@transaction.atomic
def edit_student(pk, actor, form):
    if not is_teacher(actor):
        raise ValidationError('Недостаточно прав.')
    user = get_user_model().objects.select_for_update().get(pk=pk)
    if not student_accounts().filter(pk=pk).exists() or is_teacher(user):
        raise ValidationError('Можно изменять только аккаунты учеников.')
    user.username = form.cleaned_data['username']
    user.first_name = form.cleaned_data['first_name']
    user.is_active = form.cleaned_data['is_active']
    if form.cleaned_data.get('password'):
        user.set_password(form.cleaned_data['password'])
    user.save(update_fields=['username', 'first_name', 'is_active', 'password'])
    return user


@transaction.atomic
def delete_student(pk, actor):
    if not is_teacher(actor):
        raise ValidationError('Недостаточно прав.')
    user = get_user_model().objects.select_for_update().get(pk=pk)
    if not student_accounts().filter(pk=pk).exists() or is_teacher(user):
        raise ValidationError('Можно удалять только аккаунты учеников.')
    user.is_active = False
    user.set_unusable_password()
    user.save(update_fields=['is_active', 'password'])
    record, _ = StudentRecord.objects.get_or_create(user=user)
    record.deleted_at = timezone.now()
    record.save()
    for pk in Request.objects.filter(student=user, status__in=[
            'pending', 'approved', 'image_ready', 'submitting', 'running', 'saving', 'uncertain']).values_list('pk', flat=True):
        close_request(pk, actor)
    # Requests retain their owner, provider IDs and billing history. Active jobs
    # still count toward capacity and are tracked by the existing queue.
    return user
