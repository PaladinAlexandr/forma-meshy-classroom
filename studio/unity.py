"""Archive FBX and every returned PBR map; prepare URP metallic/smoothness.

This is an ordinary ZIP, not a .unitypackage. Shader assignment is explicit in
the included guide because FBX material bindings vary between provider models.
"""
import io
import json
import shutil
import tempfile
import warnings
import zipfile
import requests
from django.core.files import File
from PIL import Image, ImageOps
from .provider import ProviderError, safe_asset_url

UNITY_GUIDE = '''FORMA · Модель для Unity 6 / Universal Render Pipeline

1. Распакуйте ZIP. Скопируйте всю папку FORMA_<id> в Assets вашего Unity-проекта.
2. Выберите model.fbx. Во вкладке Model проверьте масштаб и импорт нормалей.
   Для учебной демо-модели без нормалей выберите Normals = Calculate.
3. Создайте Material, Shader = Universal Render Pipeline/Lit, Workflow = Metallic.
   Если у модели несколько материалов, создайте материал для каждого texture-set.
4. Назначьте Textures/texture_N_base_color.png в Base Map. sRGB включён.
5. Для texture_N_normal.png установите Texture Type = Normal map и назначьте
   в Normal Map. В зависимости от исходной конвенции при необходимости Flip Green.
6. texture_N_urp_metallic_smoothness.png: sRGB отключён, Alpha Source = Input
   Texture Alpha. Назначьте в Metallic Map, Smoothness Source = Metallic Alpha,
   Smoothness = 1. R = metallic, A = 1 - roughness, G = 1, B = 0.
   Это данные в линейном пространстве; не включайте sRGB для этой карты.
7. Во вкладке Materials файла FBX назначьте созданные URP-материалы слотам модели
   (Remapped Materials) и нажмите Apply. model_materials.json перечисляет наборы
   текстур по индексу из Meshy; конкретные слоты проверьте визуально.
8. Перетащите модель в сцену, проверьте масштаб, ориентацию, материалы и тени.
   Для игры при необходимости создайте Collider и Prefab.

Если в FBX уже встроены текстуры, используйте Extract Textures/Extract Materials
в его инспекторе. Импортируемый FBX не гарантирует автоматическое создание
URP-материала: формат геометрии и шейдер рендер-пайплайна — разные вещи.

Демо: выдаётся один учебный кристалл, не генерация по загруженному фото.
У демо нет UV и PBR-текстур; создайте однотонный URP/Lit материал мятного цвета.
В live-режиме архив содержит FBX и доступные текстуры реальной задачи Meshy.

Ссылки:
https://docs.unity3d.com/6000.0/Documentation/Manual/urp/lit-shader.html
https://docs.unity3d.com/6000.0/Documentation/Manual/urp/shaders-in-universalrp-channel-packed-texture.html
'''


def texture_bytes(url):
    safe_asset_url(url)
    try:
        with requests.get(url, stream=True, timeout=(10, 45), allow_redirects=False) as response:
            if response.status_code != 200:
                raise ProviderError('Не удалось сохранить текстуру Meshy. Повторим загрузку автоматически.')
            out = io.BytesIO()
            for part in response.iter_content(128 * 1024):
                out.write(part)
                if out.tell() > 32 * 1024 * 1024:
                    raise ProviderError('Текстура превышает лимит 32 МБ.')
            out.seek(0)
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(out)
                if image.width * image.height > 20_000_000:
                    raise ProviderError('Текстура превышает лимит 20 мегапикселей.')
                image.load()
                return image.convert('RGBA')
    except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ProviderError('Не удалось прочитать текстуру. Загрузка будет повторена.') from exc


def pack_urp(metallic, roughness):
    size = roughness.size if roughness is not None else metallic.size
    red = metallic.getchannel('R').resize(size) if metallic is not None else Image.new('L', size, 0)
    alpha = ImageOps.invert(roughness.getchannel('R').resize(size)) if roughness is not None else Image.new('L', size, 128)
    return Image.merge('RGBA', (red, Image.new('L', size, 255), Image.new('L', size, 0), alpha))


def png(image):
    out = io.BytesIO(); image.save(out, 'PNG'); return out.getvalue()


def build_unity_package(item, texture_sets, demo=False):
    if not isinstance(texture_sets, list) or len(texture_sets) > 8:
        raise ProviderError('Meshy вернул неподдерживаемое количество наборов текстур.')
    root = f'FORMA_{item.short_id}'
    manifest = {'format': 'FBX', 'target': 'Unity 6 URP', 'demo': demo, 'texture_sets': []}
    with tempfile.TemporaryFile() as tmp:
        with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            with item.model_fbx.open('rb') as source, archive.open(f'{root}/model.fbx', 'w', force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=128 * 1024)
            for index, urls in enumerate(texture_sets):
                if not isinstance(urls, dict):
                    raise ProviderError('Meshy вернул некорректный набор текстур.')
                images, mapping = {}, {'index': index}
                for kind in ('base_color', 'normal', 'metallic', 'roughness', 'emission'):
                    if urls.get(kind):
                        image = texture_bytes(urls[kind])
                        name = f'Textures/texture_{index}_{kind}.png'
                        archive.writestr(f'{root}/{name}', png(image))
                        mapping[kind] = name
                        if kind in ('metallic', 'roughness'):
                            images[kind] = image
                if images:
                    packed = pack_urp(images.get('metallic'), images.get('roughness'))
                    name = f'Textures/texture_{index}_urp_metallic_smoothness.png'
                    archive.writestr(f'{root}/{name}', png(packed))
                    mapping['urp_metallic_smoothness'] = name
                manifest['texture_sets'].append(mapping)
            archive.writestr(f'{root}/IMPORT_IN_UNITY.txt', UNITY_GUIDE.encode('utf-8'))
            archive.writestr(f'{root}/model_materials.json', json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8'))
        tmp.seek(0)
        item.unity_package.save('unity-fbx-textures.zip', File(tmp), save=True)
