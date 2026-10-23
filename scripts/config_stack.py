#!/usr/bin/env python3
"""Reconcile the audited configuration. No downloads, searches, or bulk imports."""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import requests

from stack_env import ROOT, compose, load_settings, validate
from hardware import hardware_dir, load_encoding


class SetupError(RuntimeError):
    pass


class API:
    def __init__(self, base, headers=None, session=None):
        self.base = base.rstrip('/')
        self.session = session or requests.Session()
        self.session.headers.update(headers or {})

    def call(self, method, path='', **kwargs):
        try:
            response = self.session.request(method, self.base + '/' + path.lstrip('/'),
                                            timeout=kwargs.pop('timeout', 120), **kwargs)
        except requests.RequestException:
            raise SetupError(f'{method} {path}: connection failed') from None
        if not response.ok:
            # Do not echo response bodies, URLs with credentials, or request payloads.
            fields = []
            try:
                errors = response.json()
                if isinstance(errors, list):
                    fields = [e.get('propertyName', '') for e in errors if isinstance(e, dict)]
            except ValueError:
                pass
            suffix = f"; fields: {', '.join(filter(None, fields))}" if any(fields) else ''
            raise SetupError(f'{method} {path}: HTTP {response.status_code}{suffix}')
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


def wait_for(check, description, timeout=300):
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = check()
            if value:
                return value
        except (SetupError, OSError, ET.ParseError, ValueError, requests.RequestException):
            pass
        if time.monotonic() >= deadline:
            raise SetupError(f'Timed out waiting for {description}')
        time.sleep(2)


def expand(value, variables):
    """Resolve only complete credential references; leave passwords uninterpreted."""
    if isinstance(value, str) and re.fullmatch(r'\$\{[A-Z_]+\}', value):
        key = value[2:-1]
        if key not in variables:
            raise SetupError(f'Missing runtime reference {key}')
        return variables[key]
    if isinstance(value, list):
        return [expand(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, variables) for k, v in value.items()}
    return value


def verify_homepage():
    """Verify the bundled dashboard and its assets through the published port."""
    assets = ['index.html', 'style.css', 'links.js']
    assets += [f'icons/{name}.svg' for name in
               ('jellyfin', 'sonarr', 'radarr', 'prowlarr', 'qbittorrent', 'sabnzbd')]
    session = requests.Session()
    session.trust_env = False
    try:
        for asset in assets:
            expected = (ROOT / 'homepage' / asset).read_bytes()

            def served():
                path = '' if asset == 'index.html' else asset
                response = session.get(f'http://127.0.0.1/{path}', timeout=5)
                return response.status_code == 200 and response.content == expected

            wait_for(served, f'homepage {asset}', timeout=60)
    finally:
        session.close()
    print('Homepage and all dashboard assets verified at http://127.0.0.1/.')


def equivalent(actual, wanted):
    """Compare declared settings only, not API decoration and read-only metadata."""
    if isinstance(wanted, dict):
        return isinstance(actual, dict) and all(k in actual and equivalent(actual[k], v)
                                                 for k, v in wanted.items())
    if isinstance(wanted, list):
        if not isinstance(actual, list):
            return False
        if wanted and all(isinstance(v, dict) and 'name' in v for v in wanted):
            lookup = {v['name']: v for v in actual}
            return all(v['name'] in lookup and equivalent(lookup[v['name']], v) for v in wanted)
        return len(actual) == len(wanted) and all(equivalent(a, b) for a, b in zip(actual, wanted))
    return actual == wanted


def upsert(api, resource, wanted, match='name'):
    current = next((x for x in api.call('GET', resource) if x.get(match) == wanted.get(match)), None)
    if current and equivalent(current, wanted):
        return current
    payload = copy.deepcopy(current or {})
    payload.update(wanted)
    result = api.call('PUT' if current else 'POST',
                      f"{resource}/{current['id']}" if current else resource, json=payload)
    return result


def patch_resource(api, resource, changes):
    current = api.call('GET', resource)
    if equivalent(current, changes):
        return
    current.update(changes)
    api.call('PUT', resource, json=current)


def enabled_qualities(items):
    result = []
    for item in items:
        if item.get('quality') and item.get('allowed'):
            result.append(item['quality']['id'])
        result.extend(enabled_qualities(item.get('items', [])))
    return result


def set_allowed_qualities(items, allowed):
    for item in items:
        if item.get('quality'):
            item['allowed'] = item['quality']['id'] in allowed
        if item.get('items'):
            set_allowed_qualities(item['items'], allowed)
            item['allowed'] = bool(set(enabled_qualities(item['items'])) & set(allowed))


class Stack:
    def __init__(self, settings):
        self.env = settings
        self.defaults = json.loads((ROOT / 'config/stack-defaults.json').read_text())
        self.defaults['jellyfin']['encoding'] = load_encoding(settings, self.defaults['jellyfin']['encoding'])
        self.arr = {}
        self.variables = dict(settings)
        self.jelly = None
        self.qbt = None

    def backup(self):
        # App-native backups capture SQLite WAL consistently while apps run.
        for app, api in self.arr.items():
            command = api.call('POST', 'command', json={'name': 'Backup'})
            self.wait_command(api, command['id'], f'{app} backup')
        result = self.sab('config', name='create_backup')
        if not result.get('value', {}).get('result'):
            raise SetupError('SABnzbd configuration backup failed')
        print('Application-native configuration backups completed.')

    @staticmethod
    def wait_command(api, command_id, description):
        def done():
            result = api.call('GET', f'command/{command_id}')
            if result.get('status') in ['failed', 'aborted']:
                raise RuntimeError(f'{description} failed')
            return result.get('status') == 'completed'
        wait_for(done, description)

    def connect_arr(self):
        conf = Path(self.env['CONF_HOME'])
        for app, port, version in [('sonarr', 8989, 3), ('radarr', 7878, 3), ('prowlarr', 9696, 1)]:
            key = wait_for(lambda: ET.parse(conf / app / 'config.xml').findtext('ApiKey'), f'{app} API key')
            api = API(self.env.get(app.upper() + '_URL', f'http://127.0.0.1:{port}') + f'/api/v{version}',
                      {'X-Api-Key': key})
            wait_for(lambda: api.call('GET', 'system/status'), f'{app} API')
            self.arr[app] = api
            self.variables[app.upper() + '_API_KEY'] = key

    def sab(self, mode, **values):
        return self.sab_api.call('POST', '', data={'apikey': self.variables['SAB_API_KEY'],
                                                  'mode': mode, 'output': 'json', **values})

    def connect_sab(self):
        path = Path(self.env['CONF_HOME']) / 'sabnzbd/sabnzbd.ini'
        def key():
            match = re.search(r'^\s*api_key\s*=\s*(\S+)', path.read_text(), re.M)
            return match[1] if match else None
        self.variables['SAB_API_KEY'] = wait_for(key, 'SAB API key')
        self.sab_api = API(self.env.get('SAB_URL', 'http://127.0.0.1:8081') + '/api')
        wait_for(lambda: self.sab('queue').get('queue'), 'SAB API')

    def configure_sab(self):
        misc = dict(self.defaults['sabnzbd']['misc'], username=self.env['UI_USER'],
                    password=self.env['UI_PASS'], host_whitelist=self.env.get('SAB_WHITELIST',
                    f"sabnzbd,localhost,127.0.0.1,{self.env.get('STACK_HOSTNAME', 'stuff')}"))
        for key, value in misc.items():
            if isinstance(value, bool):
                value = int(value)
            result = self.sab('set_config', section='misc', keyword=key, value=value)
            if 'config' not in result:
                raise SetupError(f'SAB configuration failed for {key}')
        for category in self.defaults['sabnzbd']['categories']:
            fields = {k: v for k, v in category.items() if k != 'name'}
            result = self.sab('set_config', section='categories', keyword=category['name'], **fields)
            if 'config' not in result:
                raise SetupError(f"SAB category {category['name']} configuration failed")
        if self.env.get('SAB_CONFIG_PROVIDER', 'false').lower() == 'true':
            fields = {key: self.env[env] for key, env in {
                'host': 'SAB_SRV_HOST', 'port': 'SAB_SRV_PORT', 'ssl': 'SAB_SRV_SSL',
                'username': 'SAB_SRV_USER', 'password': 'SAB_SRV_PASS',
                'connections': 'SAB_SRV_CONNS'}.items()}
            fields.update(enable=1, ssl_verify=3, priority=int(self.env.get('SAB_SRV_PRIORITY', 0)))
            result = self.sab('set_config', section='servers', keyword=self.env['SAB_SRV_NAME'], **fields)
            if 'config' not in result:
                raise SetupError('SAB NNTP provider configuration failed')
        print('SAB browser authentication, categories, paths and NNTP provider configured.')

    def connect_qbt(self, allow_initialize=False):
        self.logout_qbt()
        self.qbt = API(self.env.get('QBT_URL', 'http://127.0.0.1:8080') + '/api/v2')
        wait_for(lambda: requests.get(self.qbt.base.rsplit('/api/v2', 1)[0], timeout=5).ok,
                 'qBittorrent Web UI')
        try:
            if self.qbt.call('POST', 'auth/login', data={'username': self.env['UI_USER'],
                                                       'password': self.env['UI_PASS']}) in ('Ok.', None):
                return
        except SetupError as exc:
            # qBittorrent 5.3 returns 204 on success and 401 on failure. A fresh
            # instance still needs the temporary-password initialization below.
            if str(exc) != 'POST auth/login: HTTP 401':
                raise
        if not allow_initialize:
            raise SetupError('qBittorrent login failed; run setup to initialize a fresh instance')
        # Fresh LinuxServer container logs its random initial password. Never print it.
        result = compose(['logs', '--no-color', '--tail', '200', 'qbittorrent'], self.env,
                         capture_output=True, text=True)
        matches = re.findall(r'temporary password[^:]*:\s*(\S+)', result.stdout, re.I)
        if not matches or self.qbt.call('POST', 'auth/login', data={'username': 'admin',
                                                                  'password': matches[-1]}) not in ('Ok.', None):
            raise SetupError('qBittorrent login failed; check UI_PASS or initial container startup')
        self.qbt.call('POST', 'app/setPreferences', data={'json': json.dumps({
            'web_ui_username': self.env['UI_USER'], 'web_ui_password': self.env['UI_PASS']})})
        if self.qbt.call('POST', 'auth/login', data={'username': self.env['UI_USER'],
                                                   'password': self.env['UI_PASS']}) not in ('Ok.', None):
            raise SetupError('qBittorrent configured credentials failed verification')

    def configure_qbt(self):
        self.qbt.call('POST', 'app/setPreferences', data={'json': json.dumps(self.defaults['qbittorrent'])})
        existing = self.qbt.call('GET', 'torrents/categories')
        for name, category in self.defaults['qbittorrentCategories'].items():
            if name in existing and existing[name].get('savePath', '') == category['savePath']:
                continue
            self.qbt.call('POST', 'torrents/editCategory' if name in existing else 'torrents/createCategory',
                          data={'category': name, 'savePath': category['savePath']})
        print('qBittorrent authentication, download paths and categories configured.')

    def configure_arr(self):
        for app in ['sonarr', 'radarr']:
            api = self.arr[app]
            desired = self.defaults['arr'][app]
            for endpoint, changes in desired.items():
                if endpoint.startswith('config/'):
                    patch_resource(api, endpoint, changes)
            # English CF IDs are regenerated on each installation.
            formats = {f['name']: upsert(api, 'customformat', f)['id'] for f in desired['customformats']}
            profiles = api.call('GET', 'qualityprofile')
            for template in desired['qualityprofiles']:
                wanted = copy.deepcopy(template)
                allowed = wanted.pop('allowedQualityIds')
                current = next((p for p in profiles if p['name'] == wanted['name']), None)
                if current is None:
                    raise SetupError(f"Missing native quality profile {wanted['name']} on {app}; check pinned version")
                wanted['items'] = copy.deepcopy(current['items'])
                set_allowed_qualities(wanted['items'], allowed)
                wanted['formatItems'] = [dict(f, format=formats[f['name']]) for f in wanted['formatItems']]
                # Preserve unrelated CFs while restoring declared scores.
                wanted['formatItems'] += [f for f in current['formatItems'] if f['name'] not in formats]
                upsert(api, 'qualityprofile', wanted)
            for wanted in desired['delayprofiles']:
                # Default delay profile has no name; match its order, not a historical ID.
                upsert(api, 'delayprofile', wanted, match='order')
            path = '/shows' if app == 'sonarr' else '/movies'
            if not any(r['path'] == path for r in api.call('GET', 'rootfolder')):
                api.call('POST', 'rootfolder', json={'path': path})
            for wanted in desired['downloadclients']:
                upsert(api, 'downloadclient', expand(wanted, self.variables))
        for app, api in self.arr.items():
            patch_resource(api, 'config/host', {'authenticationMethod': 'forms',
                'authenticationRequired': 'enabled', 'username': self.env['UI_USER'],
                'password': self.env['UI_PASS'], 'passwordConfirmation': self.env['UI_PASS']})
            if app != 'prowlarr':
                host = api.call('GET', 'config/host')
                patch_resource(api, 'config/host', {k: v for k, v in {
                    'updateMechanism': 'docker', 'updateAutomatically': False}.items() if k in host})
        print('Arr profiles, English rules, 60-minute Usenet fallback, clients and authentication configured.')

    def configure_prowlarr(self):
        api = self.arr['prowlarr']
        desired = self.defaults['prowlarr']
        labels = {label for ep in ['indexer', 'indexerproxy', 'applications']
                  for item in desired[ep] for label in item.get('tags', [])}
        tags = {label: upsert(api, 'tag', {'label': label}, match='label')['id'] for label in labels}
        profiles = {p['name']: upsert(api, 'appprofile', p)['id'] for p in desired['appprofile']}
        wanted_names = {s.strip().lower().replace(' ', '') for s in self.env['INDEXERS'].split(',')}
        for endpoint in ['indexerproxy', 'indexer', 'applications']:
            if endpoint == 'indexerproxy' and self.env.get('CREATE_PROXY', 'true').lower() != 'true':
                continue
            for template in desired[endpoint]:
                wanted = expand(template, self.variables)
                if endpoint == 'indexer' and wanted['name'].lower().replace(' ', '') not in wanted_names:
                    continue
                wanted['tags'] = [tags[t] for t in wanted.get('tags', [])]
                if 'appProfileName' in wanted:
                    wanted['appProfileId'] = profiles[wanted.pop('appProfileName')]
                upsert(api, endpoint, wanted)
        command = api.call('POST', 'command', json={'name': 'ApplicationIndexerSync'})
        self.wait_command(api, command['id'], 'Prowlarr application sync')
        print('Prowlarr proxy, four indexers and full Arr sync configured.')

    def jelly_headers(self, token=None):
        value = 'MediaBrowser Client="StackSetup", Device="Maintenance", DeviceId="stack-setup", Version="1.0"'
        if token:
            value += f', Token="{token}"'
        return {'Authorization': value}

    def connect_jellyfin(self, allow_startup=False):
        base = self.env.get('JELLYFIN_URL', 'http://127.0.0.1:8096')
        self.jelly = API(base, self.jelly_headers())
        info = wait_for(lambda: self.jelly.call('GET', 'System/Info/Public'), 'Jellyfin API')
        if not info.get('StartupWizardCompleted'):
            if not allow_startup:
                raise SetupError('Jellyfin startup is incomplete; run setup before verification')
            self.jelly.call('POST', 'Startup/Configuration', json={
                'UICulture': 'en-US', 'MetadataCountryCode': 'US', 'PreferredMetadataLanguage': 'en'})
            self.jelly.call('GET', 'Startup/User')
            self.jelly.call('POST', 'Startup/User', json={
                'Name': self.env['JELLYFIN_USER'], 'Password': self.env['JELLYFIN_PASSWORD']})
            self.jelly.call('POST', 'Startup/RemoteAccess', json={
                'EnableRemoteAccess': True, 'EnableAutomaticPortMapping': False})
            self.jelly.call('POST', 'Startup/Complete')
        result = self.jelly.call('POST', 'Users/AuthenticateByName', json={
            'Username': self.env['JELLYFIN_USER'], 'Pw': self.env['JELLYFIN_PASSWORD']})
        self.jelly.session.headers.update(self.jelly_headers(result['AccessToken']))
        self.jelly_user = result['User']
        if not self.jelly_user.get('Policy', {}).get('IsAdministrator'):
            raise SetupError('JELLYFIN_USER must be an administrator to configure the server')

    def configure_jellyfin(self):
        desired = self.defaults['jellyfin']
        # Back up public settings before updates; avoid database edits or daemon restarts.
        snapshot = {key: self.jelly.call('GET', 'System/Configuration' + ('/' + key if key else ''))
                    for key in ['', 'encoding']}
        snapshot['user'] = self.jelly_user['Configuration']
        backup = Path(self.env['CONF_HOME']) / 'jellyfin/config' / (
            'setup-settings-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
        backup.write_text(json.dumps(snapshot, indent=2)); backup.chmod(0o600)
        for key, changes in [('', desired['system']), ('encoding', desired['encoding'])]:
            endpoint = 'System/Configuration' + ('/' + key if key else '')
            current = self.jelly.call('GET', endpoint)
            if not equivalent(current, changes):
                current.update(changes)
                self.jelly.call('POST', endpoint, json=current)
        user = self.jelly_user['Configuration']
        user.update(desired['user'])
        self.jelly.call('POST', 'Users/Configuration', params={'userId': self.jelly_user['Id']}, json=user)
        existing = self.jelly.call('GET', 'Library/VirtualFolders')
        for library in desired['libraries']:
            current = next((l for l in existing if l['Name'] == library['name']), None)
            options = copy.deepcopy(library['options'])
            if current:
                # Keep extra library paths and user customizations. Add only a missing declared path.
                if library['path'] not in current['Locations']:
                    self.jelly.call('POST', 'Library/VirtualFolders/Paths', params={
                        'refreshLibrary': False}, json={'Name': library['name'],
                                                       'Path': library['path']})
                opts = current['LibraryOptions']; opts.update(options)
                self.jelly.call('POST', 'Library/VirtualFolders/LibraryOptions', json={
                    'Id': current['ItemId'], 'LibraryOptions': opts})
            else:
                self.jelly.call('POST', 'Library/VirtualFolders', params={
                    'name': library['name'], 'collectionType': library['type'], 'refreshLibrary': False},
                    json={'LibraryOptions': {**options, 'PathInfos': [{'Path': library['path']}]}})
        print('Jellyfin user, English audio, libraries and audited Intel QSV settings configured.')

    def verify(self, external=True):
        for app, api in self.arr.items():
            if app != 'prowlarr':
                for client in api.call('GET', 'downloadclient'):
                    api.call('POST', 'downloadclient/test', json=client)
                roots = api.call('GET', 'rootfolder')
                if not any(r.get('accessible') and r['path'] == ('/shows' if app == 'sonarr' else '/movies') for r in roots):
                    raise SetupError(f'{app} root folder is not accessible')
            for health in api.call('GET', 'health'):
                print(f"{app} health: {health['source']} ({health['type']})")
                if health['type'] == 'error' and health['source'] != 'UpdateCheck':
                    raise SetupError(f"{app} health failure: {health['source']}")
        if external:
            for idx in self.arr['prowlarr'].call('GET', 'indexer'):
                if idx.get('enable'):
                    self.arr['prowlarr'].call('POST', 'indexer/test', json=idx)
            if self.env.get('SAB_CONFIG_PROVIDER', 'false').lower() == 'true':
                result = self.sab('config', name='test_server', server=self.env['SAB_SRV_NAME'],
                    host=self.env['SAB_SRV_HOST'], port=self.env['SAB_SRV_PORT'],
                    ssl=self.env['SAB_SRV_SSL'], ssl_verify=3, username=self.env['SAB_SRV_USER'],
                    password=self.env['SAB_SRV_PASS'], connections=self.env['SAB_SRV_CONNS'], timeout=15)
                if not result.get('value', {}).get('result'):
                    raise SetupError('NNTP server test failed; confirm provider credentials/subscription')
        for app in ['sonarr', 'radarr']:
            api = self.arr[app];desired = self.defaults['arr'][app]
            for ep in ['config/mediamanagement', 'config/naming', 'config/indexer', 'config/downloadclient']:
                if not equivalent(api.call('GET', ep), desired[ep]):
                    raise SetupError(f'{app}: settings differ at {ep}')
            formats = {f['name']: f['id'] for f in api.call('GET', 'customformat')}
            profiles = api.call('GET', 'qualityprofile')
            for expected in desired['qualityprofiles']:
                actual = next((p for p in profiles if p['name'] == expected['name']), {})
                compare = {k: v for k, v in expected.items() if k not in ['allowedQualityIds', 'formatItems']}
                if not equivalent(actual, compare) or set(enabled_qualities(actual.get('items', []))) != set(expected['allowedQualityIds']):
                    raise SetupError(f"{app}: quality profile differs: {expected['name']}")
                for f in expected['formatItems']:
                    if not any(x.get('format') == formats.get(f['name']) and x['score'] == f['score'] for x in actual['formatItems']):
                        raise SetupError(f'{app}: English format score differs')
            if not all(any(equivalent(p, e) for p in api.call('GET', 'delayprofile')) for e in desired['delayprofiles']):
                raise SetupError(f'{app}: delay settings differ')
        sab_config = self.sab('get_config')['config']
        if not equivalent(sab_config['misc'], self.defaults['sabnzbd']['misc']):
            raise SetupError('SAB processing/path settings differ')
        if sab_config['misc'].get('username') != self.env['UI_USER']:
            raise SetupError('SAB browser username differs')
        for category in self.defaults['sabnzbd']['categories']:
            actual = next((c for c in sab_config['categories'] if c['name'] == category['name']), {})
            if not equivalent(actual, category):
                raise SetupError(f"SAB category differs: {category['name']}")
        login = requests.Session()
        try:
            response = login.post(self.sab_api.base.rsplit('/api', 1)[0] + '/login/',
                                  data={'username': self.env['UI_USER'], 'password': self.env['UI_PASS']}, timeout=15)
            if not response.ok or not login.cookies:
                raise SetupError('SAB browser form login failed')
        except requests.RequestException:
            raise SetupError('SAB browser form login request failed') from None
        if not equivalent(self.jelly.call('GET', 'System/Configuration'), self.defaults['jellyfin']['system']):
            raise SetupError('Jellyfin metadata/bitrate defaults differ')
        libraries = self.jelly.call('GET', 'Library/VirtualFolders')
        for expected in self.defaults['jellyfin']['libraries']:
            actual = next((l for l in libraries if l['Name'] == expected['name']), {})
            if expected['path'] not in actual.get('Locations', []):
                raise SetupError(f"Jellyfin library path missing: {expected['name']}")
            if not equivalent(actual.get('LibraryOptions', {}), expected['options']):
                raise SetupError(f"Jellyfin library options differ: {expected['name']}")
        # External tests and Arr polling can outlive/evict the original Web UI
        # session. Authenticate again before the final protected readback.
        self.connect_qbt()
        if not equivalent(self.qbt.call('GET', 'app/preferences'), self.defaults['qbittorrent']):
            raise SetupError('qBittorrent preferences differ')
        if not equivalent(self.jelly.call('GET', 'System/Configuration/encoding'), self.defaults['jellyfin']['encoding']):
            raise SetupError('Jellyfin encoding settings differ')
        users = self.jelly.call('GET', 'Users')
        actual = next(u for u in users if u['Id'] == self.jelly_user['Id'])
        if not equivalent(actual['Configuration'], self.defaults['jellyfin']['user']):
            raise SetupError('Jellyfin English playback preferences differ')
        print('Configuration readback and integration checks passed. No media downloads or searches submitted.')

    def logout_qbt(self):
        if self.qbt:
            try:
                self.qbt.call('POST', 'auth/logout')
            except SetupError:
                # A session already expired or evicted needs no logout.
                pass
            finally:
                self.qbt.session.close()
                self.qbt = None

    def logout(self):
        self.logout_qbt()
        if self.jelly and 'Token=' in self.jelly.session.headers.get('Authorization', ''):
            self.jelly.call('POST', 'Sessions/Logout')


def main():
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read settings and test integrations; do not change configuration')
    parser.add_argument('--skip-external-tests', action='store_true', help='Skip external indexer/provider tests')
    args = parser.parse_args()
    stack = None
    try:
        settings = load_settings();validate(settings)
        verify_homepage()
        if not args.check and not (hardware_dir(settings) / 'profile.json').exists():
            raise SetupError('Run ./setup.sh to select and probe hardware before configuring apps')
        stack = Stack(settings)
        stack.connect_arr();stack.connect_sab();stack.connect_qbt(allow_initialize=not args.check)
        if not args.check:
            stack.backup()
            stack.configure_sab();stack.configure_qbt();stack.configure_arr();stack.configure_prowlarr()
        stack.connect_jellyfin(allow_startup=not args.check)
        if not args.check:
            stack.configure_jellyfin()
        stack.verify(external=not args.skip_external_tests)
    except (SetupError, RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Setup failed: {exc}', file=sys.stderr)
        return 1
    finally:
        if stack:
            try:
                stack.logout()
            except SetupError:
                print('Could not log out the maintenance session.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
