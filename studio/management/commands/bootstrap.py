from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from studio.models import Policy, Preset

PRESETS = [
    ('Классика', 'Сохраним цвета и материалы твоего рисунка.', 'Preserve the original image colors and materials, clean consistent surface texture.', '◈', 'mint'),
    ('Глиняная игрушка', 'Мягкие цвета и тёплая матовая поверхность.', 'Hand-painted clay toy texture, warm pastel colors, soft matte ceramic surface.', '◎', 'peach'),
    ('Космический металл', 'Металлические поверхности и яркие акценты.', 'Brushed sci-fi metal texture, silver panels, subtle cyan accents, clean surface.', '✧', 'blue'),
    ('Сказочный мир', 'Насыщенные цвета и рисованные детали.', 'Whimsical hand-painted fantasy texture, rich colors, charming illustrated surface details.', '✿', 'lilac'),
]

class Command(BaseCommand):
    help = 'Create center policy, teacher group and initial approved texture presets. Does not create users.'

    def handle(self, *args, **options):
        Policy.objects.get_or_create(pk=1)
        teachers, _ = Group.objects.get_or_create(name='Преподаватели')
        teachers.permissions.add(Permission.objects.get(codename='moderate_request', content_type__app_label='studio'))
        for name, description, prompt, icon, color in PRESETS:
            Preset.objects.get_or_create(name=name, defaults={'description': description, 'texture_prompt': prompt, 'icon': icon, 'color': color})
        self.stdout.write(self.style.SUCCESS('Center initialized.'))
