def is_teacher(user):
    return user.is_authenticated and user.has_perm('studio.moderate_request')


def visible_requests(user):
    from .models import Request
    qs = Request.objects.select_related('student', 'preset', 'reviewer')
    return qs if is_teacher(user) else qs.filter(student=user)
