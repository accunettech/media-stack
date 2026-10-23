#!/usr/bin/env python3
"""Host preparation and Compose orchestration; configuration lives in config_stack."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from stack_env import ROOT, compose, load_settings, validate
from hardware import hardware_dir, prepare


def runtime_settings():
    env = load_settings()
    validate(env)
    return env


def ensure_directories(env):
    dirs = [Path(env[k]) for k in ['MEDIA_DIR', 'MOVIES_DIR', 'SHOWS_DIR', 'DOWNLOADS_DIR']]
    conf = Path(env['CONF_HOME'])
    dirs += [conf / name for name in ['gluetun', 'qbittorrent', 'sonarr', 'radarr', 'prowlarr',
                                     'sabnzbd', 'jellyfin/config', 'jellyfin/cache']]
    # Never recursively chown or chmod an existing media collection.
    for path in dirs:
        if not path.exists():
            command = ['install', '-d', '-m', '0775', '-o', env['OWNER_UID'], '-g', env['OWNER_GID'], str(path)]
            if os.geteuid() != 0:
                command.insert(0, 'sudo')
            subprocess.run(command, check=True)
        elif not path.is_dir():
            raise ValueError(f'Expected directory: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure-only', action='store_true')
    parser.add_argument('--skip-external-tests', action='store_true')
    parser.add_argument('--transcoding', choices=['auto', 'intel', 'amd', 'nvidia', 'software'])
    parser.add_argument('--gpu-device', help='DRM render path or NVIDIA GPU UUID')
    parser.add_argument('--reprobe-hardware', action='store_true')
    parser.add_argument('--non-interactive', action='store_true')
    args = parser.parse_args()
    try:
        env = runtime_settings()
        profile = hardware_dir(env) / 'profile.json'
        reprobe = args.reprobe_hardware or args.transcoding is not None or args.gpu_device is not None or not profile.exists()
        if args.configure_only and reprobe:
            raise ValueError('Hardware selection requires container device changes; run full setup first.')
        if not args.configure_only:
            ensure_directories(env)
            if reprobe:
                prepare(env, args.transcoding or env.get('TRANSCODING_MODE', 'auto'),
                        args.gpu_device or env.get('GPU_DEVICE') or None,
                        interactive=sys.stdin.isatty() and not args.non_interactive)
            compose(['config', '--quiet'], env)
            compose(['up', '-d', '--build'], env)
            # Applying this optional service even when disabled removes obsolete restart policy.
            compose(['--profile', 'vpn-alerts', 'up', '-d', '--no-deps', 'vpn-watch'], env)
        command = [sys.executable, str(ROOT / 'scripts/config_stack.py')]
        if args.skip_external_tests:
            command.append('--skip-external-tests')
        subprocess.run(command, env=env, check=True)
        print('Setup complete. See README.md for browser URLs and verification limits.')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Setup failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
