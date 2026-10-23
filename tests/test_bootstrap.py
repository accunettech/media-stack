import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stack_env import load_settings, validate, ROOT
from config_stack import API, SetupError, Stack, enabled_qualities, equivalent, expand, set_allowed_qualities, upsert


class EnvironmentTests(unittest.TestCase):
    def test_vault_precedence_and_passwords_are_opaque(self):
        with tempfile.TemporaryDirectory() as td:
            public = Path(td) / '.env';private = Path(td) / '.env.secrets'
            public.write_text('CONTAINER_HOME=${STACK_ROOT}\nCONF_HOME=${CONTAINER_HOME}/conf\nUI_PASS=public\n')
            private.write_text("UI_PASS='a&b^c${HOME}$x'\n")
            settings = load_settings(public, private, inherited={})
            self.assertEqual(settings['UI_PASS'], 'a&b^c${HOME}$x')
            self.assertEqual(settings['CONF_HOME'], str(ROOT / 'conf'))
            self.assertEqual(load_settings(public, private, {'UI_PASS': 'vault'})['UI_PASS'], 'vault')

    def test_missing_secrets_report_names_only(self):
        settings = load_settings(secrets_file='/tmp/nonexistent-stack-test-secrets', inherited={})
        with self.assertRaisesRegex(ValueError, 'Missing settings:.*UI_PASS'):
            validate(settings)
        validate(settings, require_secrets=False)

    def test_public_env_has_no_secret_values(self):
        from dotenv import dotenv_values
        public = dotenv_values(ROOT / '.env', interpolate=False)
        for key in ['UI_PASS', 'OPENVPN_PASSWORD', 'SAB_SRV_PASS', 'JELLYFIN_PASSWORD', 'USENET_DEFAULT_APIKEY']:
            self.assertFalse(public.get(key), key)


class ReconciliationTests(unittest.TestCase):
    def test_runtime_references_do_not_expand_password_contents(self):
        self.assertEqual(expand({'value':'${UI_PASS}'}, {'UI_PASS':'abc${HOME}&x'}), {'value':'abc${HOME}&x'})
        with self.assertRaises(SetupError):expand('${MISSING}', {})

    def test_comparison_ignores_server_decoration(self):
        actual = {'fields':[{'name':'port','value':8080,'label':'Port'},{'name':'host','value':'gluetun'}], 'id':92}
        actual['fields'].append({'name':'serverDecoration','value':True})
        wanted = {'fields':[{'name':'host','value':'gluetun'},{'name':'port','value':8080}]}
        self.assertTrue(equivalent(actual,wanted))
        wanted['fields'][0]['value']='localhost'
        self.assertFalse(equivalent(actual,wanted))

    def test_upsert_uses_new_install_id_and_updates_existing(self):
        api=Mock();api.call.side_effect=[[{'id':92,'name':'sabnzbd','priority':2}],{'id':92}]
        upsert(api,'downloadclient',{'name':'sabnzbd','priority':1})
        args,kw=api.call.call_args
        self.assertEqual(args,('PUT','downloadclient/92'))
        self.assertEqual(kw['json']['priority'],1)

    def test_upsert_does_not_duplicate_matching_config(self):
        api=Mock();api.call.return_value=[{'id':92,'name':'sabnzbd','priority':1}]
        result=upsert(api,'downloadclient',{'name':'sabnzbd','priority':1})
        self.assertEqual(result['id'],92);self.assertEqual(api.call.call_count,1)

    def test_quality_groups_preserve_structure(self):
        items=[{'quality':{'id':1},'allowed':False},{'id':1000,'items':[{'quality':{'id':7},'allowed':True}], 'allowed':True}]
        set_allowed_qualities(items,[1]);self.assertEqual(enabled_qualities(items),[1]);self.assertFalse(items[1]['allowed'])

    def test_http_failures_do_not_expose_credentials(self):
        session=Mock();response=session.request.return_value;response.ok=False;response.status_code=400
        response.json.return_value=[{'propertyName':'apiKey','errorMessage':'private secret'}]
        with self.assertRaises(SetupError) as caught:API('http://local',session=session).call('POST','indexer',json={'apiKey':'private secret'})
        self.assertNotIn('private secret',str(caught.exception));self.assertIn('apiKey',str(caught.exception))

    def test_check_does_not_initialize_qbt_password(self):
        stack=Stack(load_settings());stack.qbt=Mock()
        fake=Mock();fake.call.return_value='Fails.'
        with patch('config_stack.API',return_value=fake),patch('config_stack.wait_for',return_value=True),patch('config_stack.compose') as docker:
            with self.assertRaises(SetupError):stack.connect_qbt(allow_initialize=False)
            docker.assert_not_called()
            self.assertEqual(fake.call.call_count,1)

    def test_unchanged_qbt_categories_are_not_resubmitted(self):
        stack=Stack(load_settings());stack.qbt=Mock()
        stack.qbt.call.side_effect=[None,copy.deepcopy(stack.defaults['qbittorrentCategories'])]
        stack.configure_qbt()
        self.assertEqual(stack.qbt.call.call_count,2)

    def test_qbt_53_unauthorized_initializes_only_when_allowed(self):
        fake=Mock()
        fake.call.side_effect=[SetupError('POST auth/login: HTTP 401'),None,None,None]
        logs=Mock(stdout='temporary password: private-initial-password')
        with patch('config_stack.API',return_value=fake),patch('config_stack.wait_for',return_value=True),patch('config_stack.compose',return_value=logs):
            Stack(load_settings()).connect_qbt(allow_initialize=True)
        self.assertEqual(fake.call.call_count,4)
        fake.call.side_effect=SetupError('POST auth/login: HTTP 401')
        with patch('config_stack.API',return_value=fake),patch('config_stack.wait_for',return_value=True),patch('config_stack.compose') as docker:
            with self.assertRaisesRegex(SetupError,'qBittorrent login failed'):
                Stack(load_settings()).connect_qbt(allow_initialize=False)
            docker.assert_not_called()

    def test_failed_backup_is_not_reported_as_success(self):
        api=Mock();api.call.return_value={'status':'failed'}
        with self.assertRaisesRegex(RuntimeError,'backup failed'):Stack.wait_command(api,5,'backup')


class DefaultsTests(unittest.TestCase):
    def setUp(self):self.d=json.loads((ROOT/'config/stack-defaults.json').read_text())

    def test_categories_match_real_arr_fields(self):
        for app,field,category in [('sonarr','tvCategory','tv-sonarr'),('radarr','movieCategory','radarr')]:
            cli=next(c for c in self.d['arr'][app]['downloadclients'] if c['implementation']=='QBittorrent')
            self.assertEqual(next(f['value'] for f in cli['fields'] if f['name']==field),category)
            self.assertIn(category,self.d['qbittorrentCategories'])

    def test_every_profile_requires_english_and_keeps_upgrades_off(self):
        for profile in self.d['arr']['sonarr']['qualityprofiles']:
            self.assertGreaterEqual(profile['minFormatScore'],0)
            self.assertEqual(next(f['score'] for f in profile['formatItems'] if f['name']=='Language: Not English'),-10000)
            self.assertFalse(profile['upgradeAllowed'])
        for profile in self.d['arr']['radarr']['qualityprofiles']:self.assertEqual(profile['language']['name'],'English')

    def test_delay_units_are_minutes(self):
        for app in ['sonarr','radarr']:
            p=self.d['arr'][app]['delayprofiles'][0]
            self.assertEqual((p['preferredProtocol'],p['usenetDelay'],p['torrentDelay']),('usenet',0,60))

    def test_encoding_and_audio_defaults(self):
        j=self.d['jellyfin'];e=j['encoding']
        self.assertNotIn('vc1',e['HardwareDecodingCodecs'])
        self.assertTrue(e['EnableIntelLowPowerH264HwEncoder']);self.assertTrue(e['EnableThrottling'])
        self.assertEqual(e['TranscodingTempPath'],'/cache/transcodes')
        self.assertEqual(j['user']['AudioLanguagePreference'],'eng');self.assertFalse(j['user']['PlayDefaultAudioTrack'])


if __name__=='__main__':unittest.main()
