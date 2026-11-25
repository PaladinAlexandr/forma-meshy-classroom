"""Only this module talks to Meshy. The API key never leaves the server."""
import base64
import io
import ipaddress
import json
import re
import socket
import struct
import tempfile
from urllib.parse import urlsplit
import requests
from django.conf import settings
from django.core.files import File


class ProviderError(Exception):
    pass


class SubmissionUncertain(ProviderError):
    pass


class PermanentProviderError(ProviderError):
    """A definitive rejection or an asset which cannot be archived by retrying."""
    pass


def safe_message(value, limit=350):
    text = str(value or '')
    if settings.MESHY_API_KEY:
        text = text.replace(settings.MESHY_API_KEY, '[скрыто]')
    text = re.sub(r'https?://\S+|data:[^\s]+|Bearer\s+\S+', '[адрес или ключ скрыт]', text, flags=re.I)
    return ' '.join(text.split())[:limit]


def task_id(value):
    # Opaque ID, encoded as a path segment by the caller. Never interpret it as a URL.
    return isinstance(value, str) and 1 <= len(value) <= 100 and not any(ord(c) < 33 or c in '/\\?#' for c in value)


class Meshy:
    url = 'https://api.meshy.ai/openapi/v1/image-to-3d'

    def __init__(self):
        self.session = requests.Session()
        self.session.headers['Authorization'] = f'Bearer {settings.MESHY_API_KEY}'

    def submit(self, item):
        if not settings.MESHY_API_KEY:
            raise ProviderError('На сервере не задан MESHY_API_KEY. Сообщите администратору.')
        if item.is_animation:
            return self.submit_animation(item)
        url = self.task_url(item)
        if item.generation_stage == 'image':
            payload = {'ai_model': item.image_model, 'prompt': item.text_prompt,
                       'aspect_ratio': '1:1', 'generate_multi_view': False}
        else:
            with item.image.open('rb') as f:
                uri = 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode('ascii')
            payload = {'image_url': uri, 'ai_model': item.provider_model, 'model_type': 'standard',
                       'should_texture': True, 'texture_prompt': item.prompt_snapshot,
                       'moderation': True, 'enable_pbr': True, 'texture_resolution': '2k',
                       'target_formats': ['glb', 'fbx']}
        return self.post_task(url, payload)

    def submit_animation(self, item):
        from .animations import ACTIONS
        if item.model_kind != 'humanoid':
            raise ProviderError('Автоматическая анимация доступна только для гуманоидов.')
        if item.generation_stage == 'rig':
            if not item.model_task_id:
                raise ProviderError('Нет ID исходной модели Meshy.')
            payload = {'input_task_id': item.model_task_id, 'height_meters': 1.7}
        else:
            if not item.rig_task_id or not item.animation_actions or any(key not in ACTIONS for key in item.animation_actions):
                raise ProviderError('Нет скелета или выбранных движений.')
            payload = {'rig_task_id': item.rig_task_id, 'action_ids': [ACTIONS[key]['id'] for key in item.animation_actions]}
        return self.post_task(self.task_url(item), payload)

    def post_task(self, url, payload):
        try:
            response = self.session.post(url, json=payload, timeout=(10, 90), allow_redirects=False)
        except requests.RequestException as exc:
            raise SubmissionUncertain('Связь прервалась при отправке. Проверьте задачи Meshy перед любым повтором.') from exc
        if response.status_code >= 500 or response.status_code == 408:
            raise SubmissionUncertain('Meshy не подтвердил отправку. Требуется сверка задач в аккаунте.')
        # Creating a task normally returns 202 Accepted, not only 200/201.
        if not 200 <= response.status_code < 300:
            messages = {401: 'API-ключ Meshy отклонён.', 402: 'В аккаунте Meshy недостаточно кредитов.',
                        429: 'Meshy отклонил отправку: превышен лимит частоты.',
                        400: 'Meshy отклонил параметры или изображение.', 422: 'Meshy отклонил параметры или изображение.'}
            if 400 <= response.status_code < 500:
                detail = ''
                try:
                    body = response.json()
                    detail = safe_message(body.get('message', '')) if isinstance(body, dict) else ''
                except (ValueError, TypeError):
                    pass
                label = 'Слишком большой файл.' if response.status_code == 413 else messages.get(response.status_code, 'Meshy отклонил запрос.')
                raise PermanentProviderError(f'{label} HTTP {response.status_code}.' + (f' Meshy: {detail}' if detail else ''))
            raise SubmissionUncertain(f'Не удалось подтвердить создание задачи (HTTP {response.status_code}). Повторная отправка заблокирована до сверки.')
        try:
            identifier = response.json()['result']
            if not task_id(identifier):
                raise ValueError()
            return identifier
        except (ValueError, KeyError, TypeError) as exc:
            raise SubmissionUncertain('Meshy принял запрос, но ID задачи не удалось прочитать.') from exc

    def task_url(self, item):
        endpoints = {'image': 'text-to-image', 'model': 'image-to-3d', 'rig': 'rigging', 'animation': 'animations'}
        return 'https://api.meshy.ai/openapi/v1/' + endpoints[item.generation_stage]

    def poll(self, item):
        from urllib.parse import quote
        if not task_id(item.provider_task_id):
            raise PermanentProviderError('Некорректный ID задачи Meshy.')
        try:
            response = self.session.get(f'{self.task_url(item)}/{quote(item.provider_task_id, safe="")}', timeout=(10, 30), allow_redirects=False)
            if response.status_code in (404, 410) and item.status == 'saving':
                raise PermanentProviderError('Готовая задача Meshy больше недоступна. Сохранение завершено ошибкой; можно создать новую заявку.')
            if response.status_code >= 400:
                raise ProviderError(f'Не удалось проверить Meshy (HTTP {response.status_code}). Повторим проверку; преподавателю доступна сверка задачи.')
            response.raise_for_status()
            result = response.json()
            if result.get('status') not in ('PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'CANCELED'):
                raise ValueError('Unknown task status')
            return result
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise ProviderError('Не удалось проверить состояние Meshy. Проверка будет повторена автоматически.') from exc

    def list_tasks(self, item, page=1):
        """Read-only recovery. A teacher chooses a task; timestamps never auto-bind it."""
        try:
            response = self.session.get(self.task_url(item), params={'page_num': page, 'page_size': 50},
                timeout=(10, 30), allow_redirects=False)
            response.raise_for_status()
            body = response.json()
            records = body if isinstance(body, list) else body.get('result', [])
            if not isinstance(records, list):
                raise ValueError()
            return [row for row in records[:50] if isinstance(row, dict) and task_id(row.get('id'))]
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            raise ProviderError('Не удалось загрузить список задач Meshy. Проверьте подключение и API-ключ.') from exc


def safe_asset_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
        or not any(host == root or host.endswith('.' + root) for root in settings.MESHY_ASSET_HOSTS)):
        raise ProviderError('Meshy вернул адрес файла вне разрешённого хранилища.')
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(addr[4][0]).is_global for addr in addresses):
            raise ProviderError('Адрес хранилища не является публичным.')
    except socket.gaierror as exc:
        raise ProviderError('Хранилище модели временно недоступно.') from exc
    return url


def download_to_field(item, field_name, url, filename, max_size=250 * 1024 * 1024):
    """No auth header forwarded to asset hosts; no redirects, bounded streaming."""
    safe_asset_url(url)
    try:
        with requests.get(url, timeout=(10, 90), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                if response.status_code in (404, 410, 413):
                    raise PermanentProviderError(f'Файл Meshy недоступен или слишком большой (HTTP {response.status_code}). Преподаватель может закрыть заявку.')
                raise ProviderError('Не удалось сохранить файл модели. Загрузка будет повторена.')
            try:
                size_hint = int(response.headers.get('Content-Length', 0))
            except (TypeError, ValueError):
                size_hint = 0
            if size_hint > max_size:
                raise PermanentProviderError(f'Файл превышает лимит сохранения {max_size // (1024 * 1024)} МБ. Заявка завершена; можно создать новую.')
            with tempfile.TemporaryFile() as tmp:
                size = 0
                for chunk in response.iter_content(128 * 1024):
                    size += len(chunk)
                    if size > max_size:
                        raise PermanentProviderError(f'Файл превышает лимит сохранения {max_size // (1024 * 1024)} МБ. Можно создать новую заявку.')
                    tmp.write(chunk)
                if not size:
                    raise ProviderError('Meshy вернул пустой файл.')
                tmp.seek(0)
                field = getattr(item, field_name)
                field.save(filename, File(tmp), save=False)
                item.save(update_fields=[field_name])
    except requests.RequestException as exc:
        raise ProviderError('Ошибка загрузки модели. Попытка будет повторена автоматически.') from exc


def archive_result(item, result):
    urls = result.get('model_urls', {})
    for fmt in ('glb', 'fbx'):
        field = f'model_{fmt}'
        if not getattr(item, field):
            if not urls.get(fmt):
                raise ProviderError(f'Meshy не вернул файл {fmt.upper()}. Сохранение будет повторено.')
            download_to_field(item, field, urls[fmt], f'model.{fmt}')
    if not item.unity_package:
        from .unity import build_unity_package
        build_unity_package(item, result.get('texture_urls', []))


def archive_image(item, result):
    """Archive signed image URLs before presenting a choice; normalize to private JPEG."""
    from PIL import Image, ImageOps
    import warnings
    from django.core.files.base import ContentFile
    if not item.image:
        urls = result.get('image_urls')
        if not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], str):
            raise ProviderError('Meshy не вернул одно изображение. Проверка будет повторена.')
        download_to_field(item, 'image', urls[0], 'generated-source', max_size=10 * 1024 * 1024)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with item.image.open('rb') as source, Image.open(source) as image:
                if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 20_000_000:
                    raise ValueError('Unsupported image')
                image.load()
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((2048, 2048))
                out = io.BytesIO()
                image.save(out, 'JPEG', quality=92)
        item.image.save('generated.jpg', ContentFile(out.getvalue()), save=False)
        item.save(update_fields=['image'])
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        # Remove an invalid download so a later poll can fetch a corrected provider result.
        item.image.delete(save=False)
        item.save(update_fields=['image'])
        raise PermanentProviderError('Изображение Meshy повреждено или превышает допустимый размер. Можно создать новую заявку.') from exc


def demo_image():
    """Local illustrative placeholder; never presented as the child's generated idea."""
    from PIL import Image, ImageDraw
    out = io.BytesIO()
    image = Image.new('RGB', (640, 640), '#eef2e8')
    draw = ImageDraw.Draw(image)
    draw.polygon([(320, 80), (480, 260), (430, 440), (320, 560), (210, 440), (160, 260)], fill='#398b72', outline='#235542')
    draw.line([(320, 80), (270, 270), (320, 560), (360, 270), (320, 80)], fill='#a9d7bc', width=6)
    draw.text((24, 24), 'FORMA DEMO - NOT AI GENERATED', fill='#235542')
    image.save(out, 'JPEG')
    return out.getvalue()


def demo_glb():
    """Small, valid GLB: an emerald low-poly crystal, clearly labeled demo in UI."""
    vertices = [(0, 1.4, 0), (-.8, 0, .6), (.8, 0, .6), (.8, 0, -.6), (-.8, 0, -.6), (0, -1, 0)]
    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1), (5, 2, 1), (5, 3, 2), (5, 4, 3), (5, 1, 4)]
    positions = b''.join(struct.pack('<3f', *vertices[i]) for face in faces for i in face)
    document = {'asset': {'version': '2.0', 'generator': 'FORMA classroom demo'},
        'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'material': 0}]}],
        'materials': [{'doubleSided': True, 'pbrMetallicRoughness': {'baseColorFactor': [.14, .72, .55, 1], 'metallicFactor': .2, 'roughnessFactor': .5}}],
        'buffers': [{'byteLength': len(positions)}],
        'bufferViews': [{'buffer': 0, 'byteLength': len(positions), 'target': 34962}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 24, 'type': 'VEC3', 'min': [-.8, -1, -.6], 'max': [.8, 1.4, .6]}]}
    meta = json.dumps(document, separators=(',', ':')).encode()
    meta += b' ' * ((-len(meta)) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(meta) + 8 + len(positions))
            + struct.pack('<I4s', len(meta), b'JSON') + meta
            + struct.pack('<I4s', len(positions), b'BIN\x00') + positions)


def demo_fbx():
    """Valid ASCII FBX 7.4, the same teaching crystal used in the GLB preview."""
    vertices = [(0, 1.4, 0), (-.8, 0, .6), (.8, 0, .6), (.8, 0, -.6), (-.8, 0, -.6), (0, -1, 0)]
    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1), (5, 2, 1), (5, 3, 2), (5, 4, 3), (5, 1, 4)]
    coords = ','.join(str(x) for point in vertices for x in point)
    indices = ','.join(str(i if j < 2 else -i - 1) for face in faces for j, i in enumerate(face))
    return f'''; FBX 7.4.0 project file
; FORMA demo crystal - generated locally, not by Meshy
FBXHeaderExtension: {{
    FBXHeaderVersion: 1003
    FBXVersion: 7400
    Creator: "FORMA classroom"
}}
GlobalSettings: {{
    Version: 1000
    Properties70: {{
        P: "UpAxis", "int", "Integer", "",1
        P: "UpAxisSign", "int", "Integer", "",1
        P: "FrontAxis", "int", "Integer", "",2
        P: "FrontAxisSign", "int", "Integer", "",-1
        P: "CoordAxis", "int", "Integer", "",0
        P: "CoordAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",100
    }}
}}
Documents: {{
    Count: 1
    Document: 1, "Scene", "Scene" {{
        RootNode: 0
    }}
}}
Definitions: {{
    Version: 100
    Count: 3
    ObjectType: "Geometry" {{
        Count: 1
    }}
    ObjectType: "Model" {{
        Count: 1
    }}
    ObjectType: "Material" {{
        Count: 1
    }}
}}
Objects: {{
    Geometry: 1001, "Geometry::Crystal", "Mesh" {{
        GeometryVersion: 124
        Vertices: *18 {{
            a: {coords}
        }}
        PolygonVertexIndex: *24 {{
            a: {indices}
        }}
        LayerElementMaterial: 0 {{
            Version: 101
            Name: ""
            MappingInformationType: "AllSame"
            ReferenceInformationType: "IndexToDirect"
            Materials: *1 {{
                a: 0
            }}
        }}
        Layer: 0 {{
            Version: 100
            LayerElement: {{
                Type: "LayerElementMaterial"
                TypedIndex: 0
            }}
        }}
    }}
    Model: 1002, "Model::Crystal", "Mesh" {{
        Version: 232
        Properties70: {{
            P: "Lcl Translation", "Lcl Translation", "", "A",0,0,0
            P: "Lcl Rotation", "Lcl Rotation", "", "A",0,0,0
            P: "Lcl Scaling", "Lcl Scaling", "", "A",1,1,1
        }}
        Shading: T
        Culling: "CullingOff"
    }}
    Material: 1003, "Material::CrystalMint", "" {{
        Version: 102
        ShadingModel: "phong"
        MultiLayer: 0
        Properties70: {{
            P: "DiffuseColor", "Color", "", "A",0.14,0.72,0.55
            P: "DiffuseFactor", "Number", "", "A",1
        }}
    }}
}}
Connections: {{
    C: "OO",1001,1002
    C: "OO",1003,1002
    C: "OO",1002,0
}}
'''.replace('    ', '\t').encode('utf-8')
