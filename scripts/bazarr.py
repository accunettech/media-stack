"""Bazarr reconciliation through its API; credentials never enter public defaults."""
import hashlib
import json
from pathlib import Path
import re

from config_stack import API, SetupError, equivalent, wait_for


def connect(settings):
    path = Path(settings['CONF_HOME']) / 'bazarr/config/config.yaml'
    def api_key():
        match = re.search(r'^  apikey: [\'\"]?([a-fA-F0-9]+)', path.read_text(), re.M)
        return match[1] if match else None
    key = wait_for(api_key, 'Bazarr API key')
    api = API(settings.get('BAZARR_URL', 'http://127.0.0.1:6767') + '/api', {'X-API-KEY': key})
    wait_for(lambda: api.call('GET', 'system/settings'), 'Bazarr API')
    return api


def desired(settings, profile_id, arr_keys):
    values = {
        'general': {'use_sonarr': True, 'use_radarr': True, 'use_embedded_subs': False,
                    'enabled_providers': ['gestdown', 'yifysubtitles', 'subf2m'], 'subfolder': 'current',
                    'utf8_encode': True, 'serie_default_enabled': True,
                    'serie_default_profile': profile_id, 'movie_default_enabled': True,
                    'movie_default_profile': profile_id, 'upgrade_subs': True},
        'subsync': {'use_subsync': True, 'force_audio': True, 'no_fix_framerate': False,
                    'use_subsync_threshold': False, 'use_subsync_movie_threshold': False},
        'subf2m': {'user_agent': 'Bazarr/1.6 (media-stack subtitle manager)', 'verify_ssl': True},
        'auth': {'type': 'form', 'username': settings['UI_USER']},
    }
    for app, port in [('sonarr', 8989), ('radarr', 7878)]:
        values[app] = {'ip': app, 'port': port, 'base_url': '', 'ssl': False,
                       'apikey': arr_keys[app.upper() + '_API_KEY']}
    # Add credentialed providers when supplied; never overwrite existing provider credentials.
    if settings.get('SUBDL_API_KEY'):
        values['general']['enabled_providers'].append('subdl')
        values['subdl'] = {'api_key': settings['SUBDL_API_KEY']}
    if settings.get('OPENSUBTITLES_USERNAME') and settings.get('OPENSUBTITLES_PASSWORD'):
        values['general']['enabled_providers'].append('opensubtitlescom')
        values['opensubtitlescom'] = {'username': settings['OPENSUBTITLES_USERNAME'],
                                     'password': settings['OPENSUBTITLES_PASSWORD']}
    return values


def reconcile(settings, arr_keys, check=False):
    api = connect(settings)
    profiles = api.call('GET', 'system/languages/profiles')
    profile = next((p for p in profiles if p['name'] == 'English full'), None)
    profile_id = profile['profileId'] if profile else max([p['profileId'] for p in profiles] + [0]) + 1
    wanted_profile = {'profileId': profile_id, 'name': 'English full', 'cutoff': 1,
                      'items': [{'id': 1, 'language': 'en', 'hi': 'False', 'forced': 'False', 'audio_exclude': 'False', 'audio_only_include': 'False'}],
                      'mustContain': [], 'mustNotContain': [], 'originalFormat': False}
    if check and (not profile or not equivalent(profile, wanted_profile)):
        raise SetupError('Bazarr full English profile mismatch')
    values = desired(settings, profile_id, arr_keys)
    current = api.call('GET', 'system/settings')
    password_hash = hashlib.md5(settings['UI_PASS'].encode()).hexdigest()
    if check:
        if not equivalent(current, values) or current['auth']['password'] != password_hash:
            raise SetupError('Bazarr managed settings mismatch')
    else:
        payload = {}
        for section, entries in values.items():
            for key, value in entries.items():
                if current.get(section, {}).get(key) != value:
                    payload[f'settings-{section}-{key}'] = (str(value).lower() if isinstance(value, bool) else value)
        if current['auth']['password'] != password_hash:
            payload['settings-auth-password'] = settings['UI_PASS']
        if not profile or not equivalent(profile, wanted_profile):
            profiles = [p for p in profiles if p['profileId'] != profile_id] + [wanted_profile]
            payload['languages-profiles'] = json.dumps(profiles)
        if payload:
            api.call('POST', 'system/backups')
            payload['languages-enabled'] = ['en']
            api.call('POST', 'system/settings', data=payload)
    # Compare imported IDs to Arr so an empty, disconnected library cannot pass.
    for app, resource, id_key in [('sonarr', 'series', 'sonarrSeriesId'),
                                  ('radarr', 'movies', 'radarrId')]:
        arr = API(settings.get(app.upper() + '_URL',
                               f'http://127.0.0.1:{8989 if app == "sonarr" else 7878}').rstrip('/') + '/api/v3',
                  {'X-Api-Key': arr_keys[app.upper() + '_API_KEY']})
        expected = {r['id'] for r in arr.call('GET', 'series' if app == 'sonarr' else 'movie')}
        def imported():
            rows = api.call('GET', resource)['data']
            return expected.issubset({r[id_key] for r in rows})
        if not imported() and not check:
            api.call('POST', 'system/tasks', data={'taskid': 'update_series' if app == 'sonarr' else 'update_movies'})
        wait_for(imported, f'Bazarr {app} library import', timeout=300)
    for resource, id_key in [('series', 'seriesid'), ('movies', 'radarrid')]:
        rows = api.call('GET', resource)['data']
        missing = [r for r in rows if r.get('profileId') != profile_id]
        if check and missing:
            raise SetupError(f'Bazarr {resource} English profile assignment mismatch')
        if missing:
            api.call('POST', resource, data={id_key: [r['sonarrSeriesId' if resource == 'series' else 'radarrId'] for r in missing],
                                            'profileid': [str(profile_id)] * len(missing)})
    print('Bazarr English subtitles, audio synchronization, authentication and Arr integrations verified.')
    return api
