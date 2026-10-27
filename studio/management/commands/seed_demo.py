import io
from datetime import timedelta
from PIL import Image, ImageDraw, ImageFilter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from studio.models import Request, Event, Preset
from studio.provider import demo_glb, demo_fbx


def artwork(kind, background):
    image = Image.new('RGB', (960, 680), background)
    d = ImageDraw.Draw(image)
    # These are local vector-like teaching illustrations, not Meshy outputs.
    for x in range(25, 960, 38):
        for y in range(25, 680, 38):
            d.ellipse((x, y, x + 2, y + 2), fill='#ffffff')
    shadow = Image.new('RGBA', image.size)
    ImageDraw.Draw(shadow).ellipse((270, 525, 710, 575), fill=(59, 75, 42, 35))
    shadow = shadow.filter(ImageFilter.GaussianBlur(15))
    image.paste(shadow, mask=shadow.getchannel('A'))
    d = ImageDraw.Draw(image)
    if kind == 'robot':
        d.line((480, 178, 480, 115), fill='#8e9679', width=12)
        d.ellipse((462, 91, 498, 127), fill='#e5a478')
        d.rounded_rectangle((343, 176, 617, 365), 45, fill='#ccd2a6', outline='#9fa980', width=3)
        d.rounded_rectangle((370, 215, 590, 310), 28, fill='#4e6652')
        d.ellipse((409, 246, 436, 273), fill='#ecf3d0'); d.ellipse((521, 246, 548, 273), fill='#ecf3d0')
        d.arc((454, 268, 506, 292), 0, 180, fill='#c7d9a6', width=5)
        d.rounded_rectangle((365, 378, 595, 510), 32, fill='#bdc797', outline='#99a778', width=3)
        d.rounded_rectangle((416, 403, 544, 474), 14, fill='#e8c69c')
        d.ellipse((463, 419, 497, 453), fill='#d68e66')
        for x in (301, 612): d.rounded_rectangle((x, 380, x + 48, 482), 18, fill='#b0bb8d')
        for x in (396, 514): d.rounded_rectangle((x, 502, x + 53, 560), 12, fill='#96a77f')
        d.rounded_rectangle((319, 237, 342, 304), 8, fill='#adb98c'); d.rounded_rectangle((618, 237, 641, 304), 8, fill='#adb98c')
    elif kind == 'dino':
        d.polygon([(330, 467), (212, 407), (270, 475), (390, 521)], fill='#89a894')
        d.ellipse((326, 325, 602, 542), fill='#9cbaa0')
        d.rounded_rectangle((488, 222, 577, 451), 42, fill='#9cbaa0')
        d.rounded_rectangle((466, 188, 666, 311), 43, fill='#acc7a9')
        d.ellipse((583, 217, 600, 234), fill='#3d5c4b')
        d.line((612, 266, 651, 263), fill='#607e64', width=4)
        for x, y in [(350, 358), (390, 325), (434, 309), (478, 320)]:
            d.polygon([(x, y), (x + 12, y - 45), (x + 39, y - 4)], fill='#d8b780')
        for x in (382, 519):d.rounded_rectangle((x, 484, x + 57, 560), 16, fill='#8ca98e')
        d.ellipse((413, 391, 520, 481), fill='#dce2b9')
    elif kind == 'rocket':
        d.polygon([(386, 437), (314, 541), (398, 522)], fill='#c38f7b'); d.polygon([(574, 437), (646, 541), (562, 522)], fill='#c38f7b')
        d.rounded_rectangle((389, 207, 571, 522), 70, fill='#e8ddd0')
        d.polygon([(389, 275), (480, 128), (571, 275)], fill='#d39b83')
        d.ellipse((423, 292, 537, 406), fill='#b5a28d'); d.ellipse((437, 306, 523, 392), fill='#7597a8')
        d.ellipse((447, 317, 472, 342), fill='#cadce0')
        d.rounded_rectangle((419, 492, 541, 531), 6, fill='#bdae96')
        d.polygon([(437, 533), (480, 605), (523, 533)], fill='#f0c27f')
    else:
        d.rounded_rectangle((367, 431, 593, 556), 35, fill='#c78e6d')
        d.polygon([(361, 424), (600, 424), (586, 455), (374, 455)], fill='#dfb193')
        d.line((481, 431, 481, 254), fill='#6e916a', width=12)
        d.ellipse((360, 251, 482, 334), fill='#9caf7b'); d.ellipse((477, 202, 598, 285), fill='#809e78')
        d.ellipse((415, 152, 496, 271), fill='#b4bd8a')
    out = io.BytesIO(); image.save(out, 'JPEG', quality=94)
    return out.getvalue()


class Command(BaseCommand):
    help = 'Seed local-only demo users and illustrated requests. Refuses live/production mode.'

    def add_arguments(self, parser):
        parser.add_argument('--refresh-exports', action='store_true', help='Rebuild the synthetic demo FBX/ZIP exports.')

    def handle(self, *args, **options):
        if not settings.DEBUG or settings.MESHY_MODE != 'demo':
            raise CommandError('Demo seed requires DEBUG=1 and MESHY_MODE=demo.')
        call_command('bootstrap')
        users = {}
        for username, name in [('demo_teacher', 'Анна'), ('demo_student', 'Саша'), ('demo_mira', 'Мира'), ('demo_lev', 'Лев')]:
            user, created = get_user_model().objects.get_or_create(username=username, defaults={'first_name': name})
            if created:
                user.set_unusable_password(); user.save()
            users[username] = user
        users['demo_teacher'].groups.add(Group.objects.get(name='Преподаватели'))
        presets = list(Preset.objects.order_by('pk'))
        examples = [
            ('Робот-помощник', 'demo_student', 'robot', '#e5ead8', 'pending', 0, 'Он помогает поливать растения на нашей космической станции.'),
            ('Динозавр по имени Дино', 'demo_mira', 'dino', '#e3e9e2', 'pending', 3, 'Это добрый динозавр из моего рисунка.'),
            ('Ракета «Вперёд»', 'demo_lev', 'rocket', '#e7e4ee', 'running', 2, 'Ракета для путешествия на Марс.'),
            ('Маленький сад', 'demo_student', 'plant', '#f1e6d9', 'ready', 1, 'Мой первый цветок в 3D.'),
            ('Космический корабль', 'demo_student', 'rocket', '#e5e8ef', 'rejected', 2, 'Хочу добавить больше деталей на корпус.'),
            ('Друг из будущего', 'demo_mira', 'robot', '#ede8df', 'approved', 1, 'Игрушечный робот с матовой поверхностью.'),
        ]
        for title, owner, kind, color, status, preset_index, note in examples:
            if Request.objects.filter(student=users[owner], title=title).exists():
                continue
            preset = presets[preset_index]
            item = Request(student=users[owner], title=title, note=note, preset=preset, preset_name=preset.name,
                           prompt_snapshot=preset.texture_prompt, status=status)
            item.image.save('source.jpg', ContentFile(artwork(kind, color)), save=False)
            if status not in ('pending', 'rejected'):
                item.reviewer = users['demo_teacher']; item.reviewed_at = timezone.now()
            if status in ('running', 'ready'):
                item.provider_mode = 'demo'; item.provider_task_id = f'demo-{item.pk}'
                item.submitted_at = timezone.now(); item.progress = 42
            if status == 'ready':
                item.progress = 100; item.consumed_credits = 0; item.finished_at = timezone.now()
                item.model_glb.save('demo.glb', ContentFile(demo_glb()), save=False)
                item.model_fbx.save('demo.fbx', ContentFile(demo_fbx()), save=False)
            if status == 'rejected':
                item.reviewer = users['demo_teacher']; item.reviewed_at = timezone.now()
                item.review_note = 'Отличная идея! Давай сфотографируем корабль целиком на однотонном фоне. Так Meshy лучше поймёт его форму.'
            item.save()
            Event.objects.create(request=item, actor=item.student, text='Заявка отправлена преподавателю на проверку.')
            if status != 'pending':
                Event.objects.create(request=item, actor=users['demo_teacher'], text=(item.review_note if status == 'rejected' else 'Преподаватель одобрил идею.'))
            if status == 'ready':
                Event.objects.create(request=item, text='Демонстрационная модель готова и сохранена в мастерской.')
        # Upgrade existing local demo records after adding Unity export. No live calls.
        from studio.unity import build_unity_package
        for item in Request.objects.filter(status='ready', provider_mode='demo'):
            if not item.model_fbx or options['refresh_exports']:
                item.model_fbx.save('demo.fbx', ContentFile(demo_fbx()), save=True)
            if not item.unity_package or options['refresh_exports']:
                build_unity_package(item, [], demo=True)
        self.stdout.write(self.style.SUCCESS('Demo ready. Use the two demo buttons on the login page. No passwords or API key required.'))
