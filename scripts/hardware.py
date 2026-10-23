#!/usr/bin/env python3
"""Host-specific GPU selection and bounded tests in the Jellyfin image."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from stack_env import ROOT, compose, load_settings

FFMPEG = '/usr/lib/jellyfin-ffmpeg/ffmpeg'
VENDORS = {'0x8086': 'intel', '0x1002': 'amd'}


def hardware_dir(env):
    return Path(env.get('STACK_HARDWARE_DIR', ROOT / '.hardware'))


def discover(drm_root=Path('/sys/class/drm'), dev_root=Path('/dev/dri')):
    devices = []
    for entry in sorted(drm_root.glob('renderD*')):
        device = dev_root / entry.name
        try:
            vendor = VENDORS.get((entry / 'device/vendor').read_text().strip())
            if vendor and device.exists():
                devices.append({'mode': vendor, 'device': str(device),
                                'group': str(device.stat().st_gid),
                                'label': f'{vendor.upper()} {device} ({(entry / "device/device").read_text().strip()})'})
        except OSError:
            continue
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=uuid,name', '--format=csv,noheader'],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                gpu_id, name = line.split(',', 1)
                devices.append({'mode': 'nvidia', 'device': gpu_id.strip(),
                                'label': f'NVIDIA {name.strip()} ({gpu_id.strip()})'})
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return devices


def select_device(devices, mode='auto', device=None, interactive=False):
    software = {'mode': 'software', 'device': '', 'label': 'Software (CPU)'}
    if mode == 'software':
        return software
    candidates = [d for d in devices if mode == 'auto' or d['mode'] == mode]
    if device:
        candidates = [d for d in candidates if d['device'] == device]
    if not candidates:
        if mode != 'auto' or device:
            raise ValueError('Requested GPU was not found. Check the driver/passthrough or select software.')
        return software
    # Prefer Intel for the existing deployment; multiple GPUs remain selectable.
    candidates.sort(key=lambda d: ['intel', 'amd', 'nvidia'].index(d['mode']))
    if interactive:
        options = candidates + [software]
        print('Transcoding devices:')
        for n, d in enumerate(options, 1):
            print(f"  {n}. {d['label']}")
        while True:
            answer = input('Choose a device [1]: ').strip() or '1'
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            print('Enter one of the displayed numbers.')
    return candidates[0]


def compose_override(selected):
    jelly = {}
    if selected['mode'] in ['intel', 'amd']:
        jelly['devices'] = [f"{selected['device']}:{selected['device']}"]
        jelly['group_add'] = [selected['group']]
        jelly['environment'] = {'LIBVA_DRIVER_NAME': 'iHD' if selected['mode'] == 'intel' else 'radeonsi'}
    elif selected['mode'] == 'nvidia':
        jelly['deploy'] = {'resources': {'reservations': {'devices': [{
            'driver': 'nvidia', 'device_ids': [selected['device']], 'capabilities': ['gpu']}]}}}
        jelly['environment'] = {'NVIDIA_VISIBLE_DEVICES': selected['device'],
                                'NVIDIA_DRIVER_CAPABILITIES': 'compute,video,utility'}
    return {'services': {'jellyfin': jelly}}


def decoder_capabilities(vainfo):
    def supported(profile):
        return bool(re.search(r'\b' + re.escape(profile) + r'\s*:\s*VAEntrypointVLD\b', vainfo))
    mappings = [('h264', 'VAProfileH264High'), ('hevc', 'VAProfileHEVCMain'),
                ('mpeg2video', 'VAProfileMPEG2Main'), ('vc1', 'VAProfileVC1Advanced'),
                ('vp9', 'VAProfileVP9Profile0'), ('av1', 'VAProfileAV1Profile0')]
    return {'decoders': [c for c, p in mappings if supported(p)],
            'hevc10': supported('VAProfileHEVCMain10'), 'vp910': supported('VAProfileVP9Profile2')}


def encoding_settings(baseline, selected, caps):
    settings = copy.deepcopy(baseline)
    mode = selected['mode']
    settings.update(HardwareAccelerationType={'intel': 'qsv', 'amd': 'vaapi',
                    'nvidia': 'nvenc', 'software': 'none'}[mode],
        EnableHardwareEncoding=mode != 'software', HardwareDecodingCodecs=caps.get('decoders', []),
        EnableDecodingColorDepth10Hevc=caps.get('hevc10', False),
        EnableDecodingColorDepth10Vp9=caps.get('vp910', False),
        EnableDecodingColorDepth10HevcRext=False, EnableDecodingColorDepth12HevcRext=False,
        EnableIntelLowPowerH264HwEncoder=caps.get('low_power_h264', False),
        EnableIntelLowPowerHevcHwEncoder=caps.get('low_power_hevc', False),
        AllowHevcEncoding=caps.get('hevc_encode', False), AllowAv1Encoding=caps.get('av1_encode', False),
        EnableTonemapping=caps.get('tonemapping', False), EnableVppTonemapping=caps.get('vpp', False),
        PreferSystemNativeHwDecoder=mode in ['intel', 'amd'], EnableEnhancedNvdecDecoder=True)
    if mode in ['intel', 'amd']:
        settings.update(VaapiDevice=selected['device'], QsvDevice=selected['device'])
    else:
        settings.update(VaapiDevice='', QsvDevice='')
    return settings


def load_encoding(env, baseline):
    path = hardware_dir(env) / 'profile.json'
    return json.loads(path.read_text())['encoding'] if path.exists() else baseline


class Probe:
    def __init__(self, env):
        self.env = env

    def command(self, executable, args, timeout=45):
        name = 'stack-gpu-probe-' + uuid.uuid4().hex
        try:
            result = compose(['run', '--rm', '--name', name, '--no-deps', '-T', '--entrypoint', executable,
                              'jellyfin', *args], self.env, capture_output=True, text=True, timeout=timeout)
            return True, result.stdout + result.stderr
        except subprocess.CalledProcessError as exc:
            return False, (exc.stdout or '') + (exc.stderr or '')
        except subprocess.TimeoutExpired:
            subprocess.run(['docker', 'rm', '-f', name], env=self.env, capture_output=True, timeout=15)
            raise ValueError('GPU probe timed out; inspect Docker, then retry or select software.') from None

    def ffmpeg(self, args):
        return self.command(FFMPEG, ['-hide_banner', '-loglevel', 'error', '-nostdin', *args])[0]

    def encode(self, mode, device, codec, low_power=False):
        init = []
        vf = 'format=nv12'
        if mode in ['intel', 'amd']:
            init = ['-init_hw_device', f'vaapi=va:{device}']
            if mode == 'intel':
                init += ['-init_hw_device', 'qsv=qs@va', '-filter_hw_device', 'qs']
            else:
                init += ['-filter_hw_device', 'va']
            vf += ',hwupload=extra_hw_frames=64'
        encoder = codec + {'intel': '_qsv', 'amd': '_vaapi', 'nvidia': '_nvenc'}[mode]
        args = [*init, '-f', 'lavfi', '-i', 'testsrc2=size=128x128:rate=30',
                '-vf', vf, '-c:v', encoder, '-b:v', '1000000']
        if low_power:
            args += ['-low_power', '1']
        return self.ffmpeg([*args, '-frames:v', '3', '-f', 'null', '-'])

    def nvidia_decode(self, codec, ten_bit=False):
        filename = '/cache/.stack-gpu-probe-' + uuid.uuid4().hex + '.mkv'
        encoder = {'h264': 'libx264', 'hevc': 'libx265', 'vp9': 'libvpx-vp9',
                   'av1': 'libsvtav1', 'mpeg2video': 'mpeg2video'}[codec]
        args = ['-f', 'lavfi', '-i', 'testsrc2=size=128x128:rate=30',
                '-pix_fmt', 'yuv420p10le' if ten_bit else 'yuv420p', '-c:v', encoder, '-threads', '1']
        if codec == 'hevc':
            args += ['-x265-params', 'pools=1:frame-threads=1:log-level=error']
        if codec == 'vp9':
            args += ['-deadline', 'realtime', '-cpu-used', '8']
        if codec == 'av1':
            args += ['-preset', '12', '-svtav1-params', 'lp=1']
        try:
            if not self.ffmpeg([*args, '-frames:v', '2', '-y', filename]):
                return False
            decoder = ('mpeg2' if codec == 'mpeg2video' else codec) + '_cuvid'
            return self.ffmpeg(['-c:v', decoder, '-i', filename, '-frames:v', '2', '-f', 'null', '-'])
        finally:
            self.command('/bin/rm', ['-f', '--', filename])

    def capabilities(self, selected):
        mode, device = selected['mode'], selected['device']
        if mode == 'software':
            return {}
        caps = {}
        if mode in ['intel', 'amd']:
            ok, output = self.command('/usr/lib/jellyfin-ffmpeg/vainfo', ['--display', 'drm', '--device', device])
            if not ok:
                raise ValueError('VA-API driver/device initialization failed. Check permissions and drivers.')
            caps.update(decoder_capabilities(output))
        else:
            caps['decoders'] = [c for c in ['h264', 'hevc', 'mpeg2video', 'vp9', 'av1'] if self.nvidia_decode(c)]
            caps['hevc10'] = self.nvidia_decode('hevc', ten_bit=True)
            caps['vp910'] = self.nvidia_decode('vp9', ten_bit=True)
        if not self.encode(mode, device, 'h264'):
            raise ValueError('GPU H.264 encode failed. Check drivers/toolkit, choose another GPU, or use software.')
        caps['hevc_encode'] = self.encode(mode, device, 'hevc')
        caps['av1_encode'] = self.encode(mode, device, 'av1')
        if mode == 'intel':
            caps['low_power_h264'] = self.encode(mode, device, 'h264', low_power=True)
            caps['low_power_hevc'] = caps['hevc_encode'] and self.encode(mode, device, 'hevc', low_power=True)
            if caps.get('hevc10'):
                caps['vpp'] = self.ffmpeg(['-init_hw_device', f'vaapi=va:{device}', '-filter_hw_device', 'va',
                    '-f', 'lavfi', '-i', 'testsrc2=size=128x128:rate=30', '-vf',
                    'format=p010le,hwupload,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc,'
                    'tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709',
                    '-c:v', 'h264_vaapi', '-frames:v', '3', '-f', 'null', '-'])
                caps['tonemapping'] = self.ffmpeg(['-init_hw_device', f'vaapi=va:{device}',
                    '-init_hw_device', 'opencl=ocl@va', '-f', 'lavfi', '-i', 'color=size=128x128',
                    '-frames:v', '1', '-f', 'null', '-'])
        elif mode == 'amd' and caps.get('hevc10'):
            caps['tonemapping'] = self.ffmpeg(['-init_hw_device', f'drm=dr:{device}',
                '-init_hw_device', 'vulkan=vk@dr', '-f', 'lavfi', '-i', 'color=size=128x128',
                '-frames:v', '1', '-f', 'null', '-'])
        elif mode == 'nvidia':
            caps['tonemapping'] = caps.get('hevc10', False)
        return caps


def prepare(env, mode='auto', device=None, interactive=False):
    selected = select_device(discover(), mode, device, interactive)
    folder = hardware_dir(env);folder.mkdir(parents=True, exist_ok=True)
    override = folder / 'compose.json'
    previous = override.read_bytes() if override.exists() else None
    override.write_text(json.dumps(compose_override(selected), indent=2) + '\n')
    try:
        compose(['build', 'jellyfin'], env)
        caps = Probe(env).capabilities(selected)
        baseline = json.loads((ROOT / 'config/stack-defaults.json').read_text())['jellyfin']['encoding']
        profile = {'selected': selected, 'capabilities': caps,
                   'encoding': encoding_settings(baseline, selected, caps)}
        path = folder / 'profile.json'
        temporary = folder / 'profile.json.tmp'
        temporary.write_text(json.dumps(profile, indent=2) + '\n');temporary.replace(path)
    except Exception:
        if previous is None:
            override.unlink(missing_ok=True)
        else:
            override.write_bytes(previous)
        raise
    print(f"Transcoding selected: {selected['label']}")
    print('Hardware decoders:', ', '.join(caps.get('decoders', [])) or 'software')
    print('HEVC/AV1 hardware encoding:', bool(caps.get('hevc_encode')), bool(caps.get('av1_encode')))
    print('HDR tone-mapping initialization:', bool(caps.get('tonemapping') or caps.get('vpp')))
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transcoding', choices=['auto', 'intel', 'amd', 'nvidia', 'software'], default='auto')
    parser.add_argument('--gpu-device', help='DRM render path or NVIDIA UUID when multiple GPUs are present')
    args = parser.parse_args()
    try:
        prepare(load_settings(), args.transcoding, args.gpu_device, sys.stdin.isatty())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'Hardware setup failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
