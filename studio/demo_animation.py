"""Local transform animation, explicitly labelled as a crystal demonstration."""
import json
import math
import struct
from django.core.files.base import ContentFile
from django.utils import timezone
from .animations import ACTIONS, build_animation_package, finish_rig
from .models import AnimationAsset, Event
from .provider import demo_fbx, demo_glb


def animated_glb(actions):
    raw = demo_glb()
    meta_size = struct.unpack_from('<I', raw, 12)[0]
    document = json.loads(raw[20:20 + meta_size])
    binary = raw[28 + meta_size:]
    times = struct.pack('<3f', 0, .5, 1)
    rotations = struct.pack('<12f', 0, 0, 0, 1, 0, 0, math.sin(math.pi / 12), math.cos(math.pi / 12), 0, 0, 0, 1)
    document['bufferViews'] += [
        {'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(times)},
        {'buffer': 0, 'byteOffset': len(binary) + len(times), 'byteLength': len(rotations)}]
    document['accessors'] += [
        {'bufferView': 1, 'componentType': 5126, 'count': 3, 'type': 'SCALAR', 'min': [0], 'max': [1]},
        {'bufferView': 2, 'componentType': 5126, 'count': 3, 'type': 'VEC4'}]
    document['animations'] = [{'name': ACTIONS[key]['clip'],
        'samplers': [{'input': 1, 'output': 2, 'interpolation': 'LINEAR'}],
        'channels': [{'sampler': 0, 'target': {'node': 0, 'path': 'rotation'}}]} for key in actions]
    binary += times + rotations
    document['buffers'][0]['byteLength'] = len(binary)
    meta = json.dumps(document, separators=(',', ':')).encode()
    meta += b' ' * (-len(meta) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28 + len(meta) + len(binary)) +
            struct.pack('<I4s', len(meta), b'JSON') + meta + struct.pack('<I4s', len(binary), b'BIN\x00') + binary)


def animated_fbx(actions):
    nodes, connections = [], []
    for index, key in enumerate(actions):
        stack, layer, node, curve = [2000 + index * 10 + j for j in range(4)]
        nodes.append(f'''
    AnimationStack: {stack}, "AnimStack::{ACTIONS[key]['clip']}", "" {{
        Properties70: {{
            P: "LocalStart", "KTime", "Time", "",0
            P: "LocalStop", "KTime", "Time", "",46186158000
        }}
    }}
    AnimationLayer: {layer}, "AnimLayer::BaseLayer", "" {{
    }}
    AnimationCurveNode: {node}, "AnimCurveNode::R", "" {{
        Properties70: {{
            P: "d|X", "Number", "", "A",0
            P: "d|Y", "Number", "", "A",0
            P: "d|Z", "Number", "", "A",0
        }}
    }}
    AnimationCurve: {curve}, "AnimCurve::", "" {{
        Default: 0
        KeyVer: 4008
        KeyTime: *3 {{
            a: 0,23093079000,46186158000
        }}
        KeyValueFloat: *3 {{
            a: 0,30,0
        }}
        KeyAttrFlags: *1 {{
            a: 24836
        }}
        KeyAttrDataFloat: *4 {{
            a: 0,0,0,0
        }}
        KeyAttrRefCount: *1 {{
            a: 3
        }}
    }}''')
        connections += [f'    C: "OO",{layer},{stack}', f'    C: "OO",{node},{layer}',
            f'    C: "OP",{node},1002,"Lcl Rotation"', f'    C: "OP",{curve},{node},"d|Z"']
        # Some importers require all three Euler channels, including constant axes.
        for offset, axis in enumerate(('X', 'Y'), start=1):
            nodes.append(f'''
    AnimationCurve: {curve + offset}, "AnimCurve::", "" {{
        Default: 0
        KeyVer: 4008
        KeyTime: *3 {{
            a: 0,23093079000,46186158000
        }}
        KeyValueFloat: *3 {{
            a: 0,0,0
        }}
        KeyAttrFlags: *1 {{
            a: 24836
        }}
        KeyAttrDataFloat: *4 {{
            a: 0,0,0,0
        }}
        KeyAttrRefCount: *1 {{
            a: 3
        }}
    }}''')
            connections.append(f'    C: "OP",{curve + offset},{node},"d|{axis}"')
    source = demo_fbx().decode()
    source = source.replace('\n}\nConnections: {', '\n' + '\n'.join(nodes).replace('    ', '\t') + '\n}\nConnections: {', 1)
    source = source.replace('Connections: {', 'Connections: {\n' + '\n'.join(connections).replace('    ', '\t'), 1)
    return source.encode()


def finish_demo_animation(item):
    if item.generation_stage == 'rig':
        item.rigged_fbx.save('demo-rig.fbx', ContentFile(demo_fbx()), save=False)
        finish_rig(item, {'consumed_credits': 0})
        return
    asset, _ = AnimationAsset.objects.get_or_create(pk=item.animation_job_id,
        defaults={'request': item, 'names': item.animation_names, 'mode': 'demo'})
    if not asset.fbx:
        asset.fbx.save('demo-animation.fbx', ContentFile(animated_fbx(item.animation_actions)), save=True)
    if not asset.glb:
        asset.glb.save('demo-animation.glb', ContentFile(animated_glb(item.animation_actions)), save=True)
    if not asset.unity_package:
        build_animation_package(item, asset)
    asset.ready, asset.consumed_credits = True, 0
    asset.save()
    item.status, item.progress = 'ready', 100
    item.finished_at, item.next_poll_at, item.error = timezone.now(), None, ''
    Event.objects.create(request=item, text='Демонстрация анимации готова: движение учебного кристалла. Это пример процесса, а не анимация персонажа Meshy.')
