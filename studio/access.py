def is_teacher(user):
    return user.is_authenticated and user.has_perm('studio.moderate_request')


def visible_requests(user, include_deleted=False):
    from .models import Request
    qs = Request.objects.select_related('student', 'preset', 'reviewer')
    if not (include_deleted and is_teacher(user)):
        qs = qs.filter(deleted_at__isnull=True)
    return qs if is_teacher(user) else qs.filter(student=user)


def student_accounts():
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(is_staff=False, is_superuser=False,
        student_record__deleted_at__isnull=True).exclude(
        groups__permissions__codename='moderate_request').exclude(
        user_permissions__codename='moderate_request').distinct()


def lock_active_student(student):
    """Call inside a transaction; serialize submissions with account deletion."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError
    from .models import StudentRecord
    current = get_user_model().objects.select_for_update().get(pk=student.pk)
    if not current.is_active or StudentRecord.objects.filter(user=current, deleted_at__isnull=False).exists():
        raise ValidationError('Доступ к аккаунту закрыт. Обратитесь к преподавателю.')
    return current
