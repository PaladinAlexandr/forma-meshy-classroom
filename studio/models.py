import uuid
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q

UNFINISHED_STATUSES = ('pending', 'approved', 'submitting', 'running', 'saving', 'uncertain', 'image_ready')

def upload_path(instance, filename):
    return f'private/{instance.pk}/{uuid.uuid4().hex}/{filename.rsplit("/", 1)[-1]}'


class Preset(models.Model):
    name = models.CharField('Название', max_length=80)
    description = models.CharField('Описание', max_length=200)
    texture_prompt = models.CharField('Промпт текстуры (англ.)', max_length=800)
    icon = models.CharField(max_length=8, default='◈')
    color = models.CharField(max_length=16, default='mint')
    active = models.BooleanField('Доступен детям', default=True)

    def __str__(self):
        return self.name


class Policy(models.Model):
    """Single center, one billing account. Lock this row when approving."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    monthly_limit = models.PositiveIntegerField('Лимит одобрений центра за месяц', default=100)
    daily_student_limit = models.PositiveIntegerField('Лимит заявок ребёнка за день', default=5)
    max_parallel = models.PositiveSmallIntegerField('Параллельных генераций', default=10,
                                                  validators=[MinValueValidator(1), MaxValueValidator(10)])
    generation_enabled = models.BooleanField('Разрешить отправку в Meshy', default=True)

    class Meta:
        verbose_name = 'Лимиты центра'
        verbose_name_plural = 'Лимиты центра'
        constraints = [models.CheckConstraint(condition=Q(max_parallel__gte=1, max_parallel__lte=10),
                                              name='center_capacity_between_1_and_10')]

    def __str__(self):
        return 'Лимиты центра'


class Request(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'На проверке'
        APPROVED = 'approved', 'Принята · в очереди'
        SUBMITTING = 'submitting', 'Отправляем в Meshy'
        RUNNING = 'running', 'Создаётся модель'
        SAVING = 'saving', 'Сохраняем модель'
        READY = 'ready', 'Модель готова'
        REJECTED = 'rejected', 'Нужны изменения'
        FAILED = 'failed', 'Ошибка генерации'
        UNCERTAIN = 'uncertain', 'Нужна проверка отправки'
        IMAGE_READY = 'image_ready', 'Выбери: создавать модель?'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='model_requests')
    title = models.CharField('Название модели', max_length=100)
    note = models.TextField('Идея', max_length=600, blank=True)
    image = models.ImageField('Фото', upload_to=upload_path, blank=True)
    source = models.CharField('Начать с', max_length=8, choices=[('photo', 'Фото или рисунок'), ('text', 'Текст → изображение → 3D')], default='photo')
    text_prompt = models.TextField('Твой промпт', max_length=800, blank=True)
    generation_stage = models.CharField(max_length=8, choices=[('image', 'Изображение'), ('model', '3D-модель')], default='model')
    image_task_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    image_model = models.CharField(max_length=40, blank=True)
    image_credits = models.PositiveIntegerField(null=True, blank=True)
    first_approved_at = models.DateTimeField(null=True, blank=True)
    preset = models.ForeignKey(Preset, on_delete=models.PROTECT)
    prompt_snapshot = models.CharField(max_length=800)
    preset_name = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='reviews')
    review_note = models.TextField('Комментарий преподавателя', max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    provider_task_id = models.CharField(max_length=100, blank=True, unique=True, null=True)
    provider_mode = models.CharField(max_length=8, blank=True)
    provider_model = models.CharField(max_length=40, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    consumed_credits = models.PositiveIntegerField(null=True, blank=True)
    model_glb = models.FileField(upload_to=upload_path, blank=True)
    model_fbx = models.FileField(upload_to=upload_path, blank=True)
    unity_package = models.FileField(upload_to=upload_path, blank=True)
    preview = models.FileField(upload_to=upload_path, blank=True)
    error = models.TextField(blank=True)
    next_poll_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    poll_failures = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        permissions = [('moderate_request', 'Может проверять заявки детей')]
        constraints = [
            models.CheckConstraint(condition=Q(progress__lte=100), name='progress_at_most_100'),
            models.UniqueConstraint(fields=['student'], condition=Q(status__in=UNFINISHED_STATUSES),
                                    name='one_unfinished_project_per_student'),
        ]
        indexes = [models.Index(fields=['status', 'next_poll_at'])]

    @property
    def status_label(self):
        if self.generation_stage == 'image':
            return {'pending': 'Промпт на проверке', 'approved': 'Изображение · в очереди',
                    'running': 'Создаётся изображение', 'saving': 'Сохраняем изображение'}.get(self.status, self.get_status_display())
        if self.source == 'text' and self.status == 'pending':
            return 'Изображение на проверке · перед 3D'
        return self.get_status_display()

    @property
    def short_id(self):
        return self.id.hex[:6].upper()

    @property
    def active(self):
        return self.status in ('approved', 'submitting', 'running', 'saving')

    def __str__(self):
        return f'{self.short_id} · {self.title}'


class Event(models.Model):
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    text = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
