"""Guard subtitle behavior and preservation of existing provider/profile settings."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bazarr import desired, reconcile


def test_full_english_external_audio_sync_and_optional_credentials():
    settings = {'UI_USER': 'test', 'SUBDL_API_KEY': 'private'}
    wanted = desired(settings, 7, {'SONARR_API_KEY': 's', 'RADARR_API_KEY': 'r'})
    assert wanted['general']['serie_default_profile'] == 7
    assert wanted['general']['movie_default_profile'] == 7
    assert wanted['general']['use_embedded_subs'] is False
    assert wanted['subsync']['use_subsync'] is True
    assert wanted['subsync']['force_audio'] is True
    assert wanted['subsync']['no_fix_framerate'] is False
    assert 'subdl' in wanted['general']['enabled_providers']
    assert wanted['subdl']['api_key'] == 'private'
    assert wanted['sonarr']['ip'] == 'sonarr'


def test_profile_creation_preserves_other_profiles_and_requires_full_english():
    api = Mock()
    other = {'profileId': 8, 'name': 'Other'}
    api.call.side_effect = [[other], {'auth': {'password': ''}}, None, None] + [{'data': []}] * 6
    with patch('bazarr.connect', return_value=api), patch('bazarr.API') as arr:
        arr.return_value.call.return_value = []
        reconcile({'UI_USER': 'test', 'UI_PASS': 'test'},
                  {'SONARR_API_KEY': 's', 'RADARR_API_KEY': 'r'})
    import json
    payload = api.call.call_args_list[3].kwargs['data']
    profiles = json.loads(payload['languages-profiles'])
    assert profiles[0] == other
    item = profiles[1]['items'][0]
    assert item['language'] == 'en'
    assert item['forced'] == 'False'
    assert item['audio_exclude'] == 'False'
    assert item['audio_only_include'] == 'False'


import pytest


@pytest.mark.stack
def test_live_bazarr_configuration_and_library_integration():
    from stack_env import load_settings
    from config_stack import Stack
    settings = load_settings()
    stack = Stack(settings)
    stack.connect_arr()
    api = reconcile(settings, stack.variables, check=True)
    assert api.call('GET', 'system/health') == {'data': []}
    assert api.call('GET', 'system/ping') == {'status': 'OK'}
