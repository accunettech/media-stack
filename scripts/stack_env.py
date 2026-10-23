#!/usr/bin/env python3
"""Load public settings plus vault-injected secrets without executing dotenv text."""
import os
from pathlib import Path
import re
import subprocess
import sys

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SECRET_KEYS = {
    'OPENVPN_PASSWORD', 'UI_PASS', 'SAB_SRV_PASS', 'JELLYFIN_PASSWORD',
    'USENET_DEFAULT_APIKEY', 'MQTT_PASS',
}


def load_settings(env_file=None, secrets_file=None, inherited=None):
    inherited = dict(os.environ if inherited is None else inherited)
    public = Path(env_file or inherited.get('STACK_ENV_FILE', ROOT / '.env'))
    private = Path(secrets_file or inherited.get('STACK_SECRETS_FILE', ROOT / '.env.secrets'))
    if not public.is_file():
        raise ValueError(f'Missing public configuration: {public}')
    settings = {k: v for k, v in dotenv_values(public, interpolate=False).items() if v is not None}
    if private.is_file():
        settings.update({k: v for k, v in dotenv_values(private, interpolate=False).items() if v is not None})
    settings.update(inherited)
    settings['STACK_ROOT'] = str(ROOT)
    settings.setdefault('CONTAINER_HOME', str(ROOT))
    pattern = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
    # Expand paths only. Passwords/API keys are opaque, including literal $ characters.
    for _ in range(12):
        changed = False
        for key in list(settings):
            if key not in SECRET_KEYS and (key.endswith(('_DIR', '_HOME', '_CFG', '_FILE'))):
                value = pattern.sub(lambda m: settings.get(m[1], m[0]), settings[key])
                changed |= value != settings[key]
                settings[key] = value
        if not changed:
            break
    return settings


def validate(settings, require_secrets=True):
    required = ['CONTAINER_HOME', 'CONF_HOME', 'SCRIPTS_HOME', 'MEDIA_DIR', 'MOVIES_DIR',
                'SHOWS_DIR', 'DOWNLOADS_DIR', 'UI_USER', 'JELLYFIN_USER', 'OPENVPN_USER']
    if require_secrets:
        required += ['UI_PASS', 'OPENVPN_PASSWORD', 'JELLYFIN_PASSWORD']
    if settings.get('SAB_CONFIG_PROVIDER', 'false').lower() == 'true':
        required += ['SAB_SRV_HOST', 'SAB_SRV_USER']
        if require_secrets:
            required.append('SAB_SRV_PASS')
    if require_secrets and 'NZBgeek' in settings.get('INDEXERS', ''):
        required.append('USENET_DEFAULT_APIKEY')
    missing = [k for k in required if not settings.get(k)]
    if missing:
        raise ValueError('Missing settings: ' + ', '.join(missing))
    if Path(settings['CONTAINER_HOME']).resolve() != ROOT:
        raise ValueError('CONTAINER_HOME must resolve to this clone; use ${STACK_ROOT}')
    for key in ['CONF_HOME', 'SCRIPTS_HOME', 'MEDIA_DIR', 'MOVIES_DIR', 'SHOWS_DIR', 'DOWNLOADS_DIR']:
        if '${' in settings[key] or not Path(settings[key]).is_absolute():
            raise ValueError(f'{key} must resolve to an absolute path')
    if settings.get('VPN_WATCHDOG_ENABLED', 'false').lower() == 'true' and not settings.get('MQTT_HOST'):
        raise ValueError('MQTT_HOST is required when the watchdog is enabled')


def compose(args, settings, **kwargs):
    files = ['--file', settings.get('STACK_COMPOSE_FILE', str(ROOT / 'docker-compose.yml'))]
    hardware = Path(settings.get('STACK_HARDWARE_DIR', ROOT / '.hardware')) / 'compose.json'
    if hardware.exists() and not settings.get('STACK_COMPOSE_FILE'):
        files += ['--file', str(hardware)]
    return subprocess.run(['docker', 'compose', '--project-directory', str(ROOT), *files, *args],
                          env=settings, check=True, **kwargs)


def main():
    try:
        settings = load_settings()
        if len(sys.argv) > 1 and sys.argv[1] == 'compose':
            # ps/logs/stop must remain usable even without vault access.
            compose(sys.argv[2:], settings)
        else:
            validate(settings, require_secrets='--public-only' not in sys.argv)
            print('Configuration valid (values not printed).')
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f'Configuration error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
