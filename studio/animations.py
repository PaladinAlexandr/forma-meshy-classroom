"""Approved humanoid animation stages share the existing per-child queue slot."""
import tempfile
import uuid
import zipfile
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from .access import is_teacher
from .models import AnimationAsset, Event, Request, UNFINISHED_STATUSES
from .provider import ProviderError, download_to_field

# Documented Meshy library IDs; do not accept arbitrary IDs from the browser.
ACTIONS = {
    'idle': {'id': 243, 'label': 'Ожидание', 'clip': 'Idle_3'},
    'walk': {'id': 566, 'label': 'Ходьба', 'clip': 'walking_2'},
    'run': {'id': 530, 'label': 'Бег', 'clip': 'run_fast_3'},
}


class AnimationForm(forms.Form):
    actions = forms.MultipleChoiceField(label='Выбери движения',
        choices=[(key, value['label']) for key, value in ACTIONS.items()],
        widget=forms.CheckboxSelectMultiple)

    def clean_actions(self):
        actions = self.cleaned_data['actions']
        if len(actions) != len(set(actions)) or not 1 <= len(actions) <= 3:
            raise forms.ValidationError('Выбери от одного до трёх разных движений.')
        return actions


@transaction.atomic
def request_animation(pk, student, actions):
    get_user_model().objects.select_for_update().get(pk=student.pk)
    item = Request.objects.select_for_update().get(pk=pk, student=student)
    if is_teacher(student) or item.model_kind != 'humanoid':
        raise ValidationError('Анимация доступна только ученику для его гуманоидной модели.')
    if (item.status != 'ready' and not (item.is_animation and item.status in ('failed', 'rejected'))
            or not item.model_fbx or not item.model_glb):
        raise ValidationError('Сначала дождись готовой 3D-модели и завершения текущего этапа.')
    if Request.objects.filter(student=student, status__in=UNFINISHED_STATUSES).exists():
        raise ValidationError('Сначала заверши текущий проект. Одновременно разрешён один незавершённый проект, включая анимацию.')
    if (not isinstance(actions, list) or not 1 <= len(actions) <= 3 or
            any(key not in ACTIONS for key in actions) or len(set(actions)) != len(actions)):
        raise ValidationError('Выбери от одного до трёх движений из списка.')
    if not item.is_animation:
        item.model_task_id = item.model_task_id or item.provider_task_id
    if item.provider_mode not in ('demo', 'live') or not item.model_task_id:
        raise ValidationError('У модели нет сохранённого ID генерации Meshy. Обратитесь к преподавателю.')
    item.animation_job_id = uuid.uuid4()
    item.animation_actions = actions
    item.generation_stage = 'animation' if item.rig_task_id and item.rigged_fbx else 'rig'
    item.status, item.progress = 'pending', 0
    item.provider_task_id = None
    item.review_note, item.error = '', ''
    item.reviewer, item.reviewed_at = None, None
    item.submitted_at, item.finished_at = None, None
    item.next_poll_at, item.lease_until, item.poll_failures = None, None, 0
    item.save()
    Event.objects.create(request=item, actor=student,
        text=f'Ученик запросил анимацию: {item.animation_names}. Ожидаем одобрения преподавателя.')
    return item


def finish_rig(item, result):
    url = result.get('result', {}).get('rigged_character_fbx_url')
    if not item.rigged_fbx:
        if not url:
            raise ProviderError('Meshy не вернул FBX со скелетом. Сохранение будет повторено.')
        download_to_field(item, 'rigged_fbx', url, 'rigged.fbx')
    item.rig_credits = result.get('consumed_credits')
    # A new reservation lets other children enter before the second paid stage.
    item.generation_stage, item.status, item.progress = 'animation', 'approved', 0
    item.provider_task_id = None
    item.reviewed_at = timezone.now()
    item.next_poll_at, item.lease_until, item.poll_failures = None, None, 0
    item.error = ''
    Event.objects.create(request=item, text='Скелет сохранён. Выбранные анимации поставлены в общую очередь; повторного одобрения не требуется.')


def archive_animation(item, result):
    asset, _ = AnimationAsset.objects.get_or_create(pk=item.animation_job_id,
        defaults={'request': item, 'names': item.animation_names, 'mode': item.provider_mode})
    urls = result.get('result', {})
    if not asset.fbx:
        if not urls.get('animation_fbx_url'):
            raise ProviderError('Meshy не вернул FBX анимации. Сохранение будет повторено.')
        download_to_field(asset, 'fbx', urls['animation_fbx_url'], 'animation.fbx')
    if not asset.glb and urls.get('animation_glb_url'):
        download_to_field(asset, 'glb', urls['animation_glb_url'], 'animation.glb')
    if not asset.unity_package:
        build_animation_package(item, asset)
    asset.consumed_credits = result.get('consumed_credits')
    asset.ready = True
    asset.save()
    item.status, item.progress = 'ready', 100
    item.finished_at = timezone.now()
    item.next_poll_at, item.error = None, ''
    Event.objects.create(request=item, text=f'Анимация готова: {asset.names}. Скачай анимированный FBX или ZIP для Unity.')


def build_animation_package(item, asset):
    guide = f'''FORMA — анимация для Unity 6 URP

Выбрано: {asset.names}.
Meshy называет клипы: {', '.join(ACTIONS[k]['clip'] for k in item.animation_actions)}.
animation.fbx — персонаж с движениями. Импортируйте его в Assets.
Inspector → Rig → Animation Type: Humanoid → Avatar Definition: Create From This Model → Apply.
Нажмите Configure, проверьте сопоставление костей. Если Unity не распознал скелет, исправьте
сопоставление; для неподходящего скелета можно использовать Generic для этого же персонажа.
На вкладке Animation включите Import Animation, проверьте клипы и Loop Time для циклов.
Создайте Animator Controller, добавьте нужный клип и назначьте контроллер персонажу.
Ходьба и бег могут содержать перемещение корневого объекта: проверьте Apply Root Motion.

В original-model.zip лежат исходный FBX, текстуры и инструкция для URP/Lit.
Материалы может понадобиться назначить анимированному персонажу вручную.
Исходная модель сохраняется отдельно: анимация не заменяет её.
{'ДЕМО: файл показывает движение учебного кристалла, а не скелет гуманоидного персонажа. Используйте Rig: Generic.' if asset.mode == 'demo' else ''}
'''
    with tempfile.TemporaryFile() as tmp:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as archive:
            for field, name in [(asset.fbx, 'animation.fbx'), (item.unity_package, 'original-model.zip')]:
                if field:
                    with field.open('rb') as source, archive.open(name, 'w') as target:
                        while chunk := source.read(128 * 1024):
                            target.write(chunk)
            archive.writestr('UNITY-ANIMATION.txt', guide)
        tmp.seek(0)
        asset.unity_package.save('unity-animation.zip', File(tmp), save=True)
