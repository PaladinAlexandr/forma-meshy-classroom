import io
import warnings
from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError
from .models import Preset, Request, Policy


class RequestForm(forms.ModelForm):
    image = forms.FileField(label='Фото или рисунок', required=False, help_text='JPG, PNG или WebP, до 10 МБ')
    source = forms.ChoiceField(label='С чего начнём?', required=False, choices=[('photo', 'Загрузить фото или рисунок'), ('text', 'Описать идею → сначала создать картинку')])
    consent = forms.BooleanField(label='Моя идея и изображение не содержат лиц и личной информации.')

    class Meta:
        model = Request
        fields = ['model_kind', 'title', 'source', 'image', 'text_prompt', 'preset', 'note']
        widgets = {'note': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Что ты хочешь создать? Это сообщение прочитает преподаватель.'}),
                   'title': forms.TextInput(attrs={'placeholder': 'Например, мой космический робот', 'maxlength': 100}),
                   'text_prompt': forms.Textarea(attrs={'rows': 5, 'maxlength': 800, 'placeholder': 'Например: маленький добрый робот с круглой головой, синим корпусом и большими колёсами. Один предмет целиком на простом фоне.'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['preset'].queryset = Preset.objects.filter(active=True)
        self.fields['model_kind'].required = False
        self.fields['note'].required = False
        self.fields['image'].widget.attrs['accept'] = 'image/jpeg,image/png,image/webp'

    def clean_model_kind(self):
        return self.cleaned_data.get('model_kind') or 'object'

    def clean_source(self):
        return self.cleaned_data.get('source') or 'photo'

    def clean(self):
        data = super().clean()
        if data.get('source') == 'text':
            if not data.get('text_prompt', '').strip():
                self.add_error('text_prompt', 'Опиши, какую картинку ты хочешь получить.')
            data['image'] = None
        elif not data.get('image') and 'image' not in self.errors:
            self.add_error('image', 'Выбери фото или переключись на создание картинки по тексту.')
        return data

    def clean_image(self):
        photo = self.cleaned_data['image']
        if not photo or self.data.get('source') == 'text':
            return None
        if photo.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Фото больше 10 МБ. Выбери файл поменьше.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(photo)
                if image.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise forms.ValidationError('Подойдёт JPG, PNG или WebP.')
                if image.width * image.height > 20_000_000:
                    raise forms.ValidationError('Изображение слишком большое: максимум 20 мегапикселей.')
                image.load()
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((2048, 2048))
                out = io.BytesIO()
                # Re-encoding strips EXIF, GPS, embedded metadata and active payloads.
                image.save(out, 'JPEG', quality=92)
            return ContentFile(out.getvalue(), name='source.jpg')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, ValueError):
            raise forms.ValidationError('Не получилось открыть изображение. Попробуй другое фото.')


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(choices=[('approve', 'Одобрить'), ('reject', 'Вернуть')])
    note = forms.CharField(required=False, max_length=1000, widget=forms.Textarea)

    def clean(self):
        data = super().clean()
        if data.get('decision') == 'reject' and not data.get('note', '').strip():
            self.add_error('note', 'Объясните ребёнку, что нужно изменить.')
        return data


class StudentForm(forms.Form):
    username = forms.RegexField(label='Логин', regex=r'^[a-zA-Z0-9_.-]{3,40}$', max_length=40,
                                help_text='3–40 символов: латиница, цифры, точка, дефис.')
    first_name = forms.CharField(label='Имя или псевдоним', max_length=40)
    password = forms.CharField(label='Начальный пароль', widget=forms.PasswordInput, min_length=8, max_length=128)

    def clean_username(self):
        name = self.cleaned_data['username'].lower()
        if get_user_model().objects.filter(username__iexact=name).exists():
            raise forms.ValidationError('Этот логин уже занят.')
        return name

    def clean(self):
        data = super().clean()
        if data.get('password'):
            user = get_user_model()(username=data.get('username', ''), first_name=data.get('first_name', ''))
            try:
                password_validation.validate_password(data['password'], user)
            except forms.ValidationError as e:
                self.add_error('password', e)
        return data


class PolicyForm(forms.ModelForm):
    class Meta:
        model = Policy
        fields = ['monthly_limit', 'daily_student_limit', 'max_parallel', 'generation_enabled']

    def clean_max_parallel(self):
        n = self.cleaned_data['max_parallel']
        if not 1 <= n <= 10:
            raise forms.ValidationError('Укажите от 1 до 10. Сверьте ограничение с вашим тарифом Meshy.')
        return n
