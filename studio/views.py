import hashlib
from functools import wraps
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .access import is_teacher, visible_requests
from .forms import RequestForm, ReviewForm, StudentForm, PolicyForm
from .models import Event, Policy, Preset, Request, UNFINISHED_STATUSES
from .services import create_request, review_request, choose_generated_image, monthly_usage


def teacher_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not is_teacher(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


class StudioLoginView(LoginView):
    template_name = 'login.html'
    redirect_authenticated_user = True


def csrf_failure(request, reason=''):
    return render(request, 'session_refresh.html', status=403)


def health(request):
    try:
        Policy.objects.filter(pk=1).exists()
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})


@require_POST
def demo_login(request, role):
    if not settings.DEBUG or settings.MESHY_MODE != 'demo' or role not in ('teacher', 'student'):
        raise Http404
    # Accounts exist only after explicit `seed_demo`; cannot sign in arbitrary users.
    user = get_object_or_404(get_user_model(), username=f'demo_{role}', is_active=True)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('dashboard')


@login_required
def dashboard(request):
    base = visible_requests(request.user)
    status = request.GET.get('status', '')
    query = request.GET.get('q', '').strip()[:100]
    qs = base
    if status == 'active':
        qs = qs.filter(status__in=['approved', 'submitting', 'running', 'saving'])
    elif status in Request.Status.values:
        qs = qs.filter(status=status)
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(student__first_name__icontains=query) | Q(student__username__icontains=query))
    counts = dict(base.values('status').annotate(n=Count('pk')).values_list('status', 'n'))
    from django.core.paginator import Paginator
    page = Paginator(qs, 12).get_page(request.GET.get('page'))
    return render(request, 'dashboard.html', {
        'page': page, 'selected_status': status, 'query': query, 'total': base.count(),
        'pending_count': counts.get('pending', 0), 'ready_count': counts.get('ready', 0),
        'active_count': sum(counts.get(s, 0) for s in ['approved', 'submitting', 'running', 'saving']),
        'issue_count': counts.get('uncertain', 0) + counts.get('failed', 0),
    })


@login_required
def new_request(request):
    if is_teacher(request.user):
        return redirect('dashboard')
    unfinished = Request.objects.filter(student=request.user, status__in=UNFINISHED_STATUSES).first()
    if unfinished:
        return render(request, 'blocked.html', {'item': unfinished})
    form = RequestForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            item = create_request(request.user, form)
            messages.success(request, 'Готово! Преподаватель получил твою заявку.')
            return redirect('detail', pk=item.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
        except IntegrityError:
            form.add_error(None, 'У тебя уже есть незавершённый проект. Открой его в списке проектов.')
    return render(request, 'new.html', {'form': form, 'presets': Preset.objects.filter(active=True)})


@login_required
def detail(request, pk):
    item = get_object_or_404(visible_requests(request.user), pk=pk)
    return render(request, 'detail.html', {'item': item, 'events': item.events.select_related('actor'),
        'review_form': ReviewForm(), 'can_review': is_teacher(request.user) and item.status == 'pending'})


@teacher_required
@require_POST
def review(request, pk):
    form = ReviewForm(request.POST)
    if form.is_valid():
        try:
            review_request(pk, request.user, form.cleaned_data['decision'], form.cleaned_data['note'])
            messages.success(request, 'Решение сохранено. Ученик увидит обновлённый статус.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    else:
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
    return redirect('detail', pk=pk)


@login_required
@require_POST
def choose_image(request, pk):
    get_object_or_404(Request, pk=pk, student=request.user)
    decision = request.POST.get('decision')
    if decision not in ('accept', 'close'):
        raise PermissionDenied
    try:
        choose_generated_image(pk, request.user, decision == 'accept')
        messages.success(request, 'Картинка отправлена преподавателю для разрешения 3D.' if decision == 'accept' else 'Проект завершён. Можно отправить новую идею.')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('detail', pk=pk)


@login_required
def private_file(request, pk, kind):
    item = get_object_or_404(visible_requests(request.user), pk=pk)
    fields = {'image': ('image', 'image/jpeg'), 'glb': ('model_glb', 'model/gltf-binary'),
              'fbx': ('model_fbx', 'application/octet-stream'),
              'unity': ('unity_package', 'application/zip')}
    if kind not in fields or (kind != 'image' and item.status != 'ready'):
        raise Http404
    field, mime = fields[kind]
    value = getattr(item, field)
    if not value:
        raise Http404
    try:
        file = value.open('rb')
    except FileNotFoundError:
        raise Http404
    suffix = {'image': 'jpg', 'glb': 'glb', 'fbx': 'fbx', 'unity': 'zip'}[kind]
    response = FileResponse(file, content_type=mime, as_attachment=kind in ('fbx', 'unity') or request.GET.get('download') == '1',
                            filename=f'forma-{item.short_id}.{suffix}')
    return response


@login_required
def updates(request):
    qs = visible_requests(request.user)
    # Aggregate covers every page; no photos, task identifiers or other students' data.
    state = list(qs.order_by('pk').values_list('pk', 'status', 'progress', 'updated_at'))
    digest = hashlib.sha256(str(state).encode()).hexdigest()[:20]
    return JsonResponse({'revision': digest})


@teacher_required
def students(request):
    form = StudentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            user = get_user_model().objects.create_user(
                username=form.cleaned_data['username'], first_name=form.cleaned_data['first_name'],
                password=form.cleaned_data['password'])
            messages.success(request, f'Аккаунт {user.username} создан. Передайте ребёнку логин и начальный пароль.')
            return redirect('students')
        except IntegrityError:
            form.add_error('username', 'Этот логин уже занят.')
    people = get_user_model().objects.filter(is_staff=False, is_superuser=False).exclude(
        groups__permissions__codename='moderate_request').exclude(user_permissions__codename='moderate_request').distinct()
    people = people.annotate(request_count=Count('model_requests')).order_by('first_name', 'username')
    return render(request, 'students.html', {'form': form, 'students': people})


@teacher_required
def center_settings(request):
    policy = Policy.objects.get(pk=1)
    form = PolicyForm(request.POST or None, instance=policy)
    if request.method == 'POST' and form.is_valid():
        # Same row lock as approvals/worker reservations.
        with transaction.atomic():
            policy = Policy.objects.select_for_update().get(pk=1)
            for field, value in form.cleaned_data.items():
                setattr(policy, field, value)
            policy.save()
        messages.success(request, 'Настройки центра сохранены.')
        return redirect('settings')
    month = timezone.localdate().replace(day=1)
    used = monthly_usage(month)
    return render(request, 'settings.html', {'form': form, 'used': used, 'policy': policy,
        'api_configured': bool(settings.MESHY_API_KEY), 'ai_model': settings.MESHY_AI_MODEL})


@teacher_required
@require_POST
def reconcile(request, pk):
    """Manual recovery without a second paid POST, after checking provider history."""
    import re
    from .provider import Meshy, ProviderError
    task_id = request.POST.get('task_id', '').strip()
    if request.POST.get('confirmed') != 'on':
        messages.error(request, 'Подтвердите, что сверили задачу с аккаунтом Meshy.')
        return redirect('detail', pk=pk)
    if task_id and not re.fullmatch(r'[A-Za-z0-9-]{10,100}', task_id):
        messages.error(request, 'Некорректный ID задачи Meshy.')
        return redirect('detail', pk=pk)
    item = get_object_or_404(Request, pk=pk)
    if item.status != 'uncertain':
        messages.error(request, 'Эта заявка уже обработана.')
        return redirect('detail', pk=pk)
    if task_id:
        item.provider_task_id = task_id
        try:
            if item.provider_mode == 'live':
                Meshy().poll(item)
        except ProviderError:
            messages.error(request, 'Не удалось найти задачу Meshy. Проверьте ID и подключение.')
            return redirect('detail', pk=pk)
    try:
        with transaction.atomic():
            item = Request.objects.select_for_update().get(pk=pk)
            if item.status != 'uncertain':
                raise ValidationError('Состояние заявки уже изменилось.')
            item.provider_task_id = task_id or None
            if task_id and item.generation_stage == 'image':
                item.image_task_id = task_id
            item.status = 'running' if task_id else 'failed'
            item.error = '' if task_id else 'Преподаватель подтвердил, что задача не была создана. Можно подать новую заявку.'
            item.next_poll_at = None
            item.lease_until = None
            item.save()
            Event.objects.create(request=item, actor=request.user, text=(
                f'После сверки преподаватель привязал задачу Meshy {task_id}.' if task_id
                else 'После сверки преподаватель подтвердил отсутствие задачи Meshy. Отправка закрыта.'))
        messages.success(request, 'Результат сверки сохранён.')
    except (IntegrityError, ValidationError):
        messages.error(request, 'ID уже привязан к другой заявке или состояние изменилось.')
    return redirect('detail', pk=pk)
