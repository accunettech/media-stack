"""Post-setup acceptance tests. Never reconcile settings or import library media.

Live tests require --run-stack; disposable downloads additionally require
--run-transfers. Run serially: these tests share authenticated service sessions.
"""
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import parse_qs, urljoin, urlsplit
import uuid

import pytest
import requests

from config_stack import Stack, verify_homepage
from hardware import Probe, hardware_dir
from stack_env import compose, load_settings, validate
from stack_test_helpers import torrent_infohash

pytestmark = pytest.mark.stack


class PrivateSettings(dict):
    def __repr__(self):
        return '<stack settings: credentials redacted>'


@pytest.fixture(scope='module')
def settings():
    env = load_settings()
    validate(env)
    return PrivateSettings(env)


@pytest.fixture(scope='module')
def live(settings):
    stack = Stack(settings)
    try:
        stack.connect_arr()
        stack.connect_sab()
        stack.connect_qbt()
        stack.connect_jellyfin()
        yield stack
    finally:
        stack.logout()


def docker(settings, *args, timeout=45, input=None):
    """Capture output: Docker configuration may contain credentials."""
    try:
        result = subprocess.run(['docker', *args], env=settings, capture_output=True,
                                input=input, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        pytest.fail('Docker command unavailable or timed out', pytrace=False)
    assert result.returncode == 0, 'Docker operation failed (output withheld to protect credentials)'
    return result.stdout


def test_dashboard_assets():
    verify_homepage()


def test_runtime_images_health_and_vpn_namespace(settings):
    config = json.loads(compose(['config', '--format', 'json'], settings,
                               capture_output=True, text=True).stdout)
    containers = {}
    for name, service in config['services'].items():
        if service.get('profiles') and settings.get('VPN_WATCHDOG_ENABLED', 'false').lower() != 'true':
            continue
        info = json.loads(docker(settings, 'inspect', service['container_name']))[0]
        containers[name] = info
        assert info['State']['Running'], f'{name} is not running'
        health = info['State'].get('Health', {}).get('Status')
        assert health in (None, 'healthy'), f'{name} health is {health}'
        ref = service.get('image', config['name'] + '-' + name)
        image = json.loads(docker(settings, 'image', 'inspect', ref))[0]
        assert info['Image'] == image['Id'], f'{name} is not using the configured image'
    mode = containers['qbittorrent']['HostConfig']['NetworkMode']
    assert mode == 'container:' + containers['gluetun']['Id'], 'qBittorrent must share Gluetun networking'
    caps = containers['gluetun']['HostConfig']['CapAdd'] or []
    assert 'NET_ADMIN' in {cap.removeprefix('CAP_') for cap in caps}, 'Gluetun lacks NET_ADMIN'


def test_declared_configuration_and_download_clients(live):
    # Includes root folders, every quality profile, language preferences,
    # categories, processing defaults, user authentication and library settings.
    live.verify(external=False)


@pytest.mark.parametrize('app', ['sonarr', 'radarr', 'prowlarr'])
def test_arr_health_and_version(live, app):
    api = live.arr[app]
    assert api.call('GET', 'system/status')['version']
    command = api.call('POST', 'command', json={'name': 'CheckHealth'})
    live.wait_command(api, command['id'], app + ' health refresh')
    errors = [h['source'] for h in api.call('GET', 'health') if h['type'] == 'error']
    assert not errors, f'{app} health errors: {errors}'


@pytest.mark.parametrize('app', ['sonarr', 'radarr'])
def test_arr_indexer_connections(live, app):
    api = live.arr[app]
    indexers = api.call('GET', 'indexer')
    assert indexers, f'{app} has no synced indexers'
    for indexer in indexers:
        if any(indexer.get(k) for k in ('enableRss', 'enableAutomaticSearch', 'enableInteractiveSearch')):
            api.call('POST', 'indexer/test', json=indexer)


@pytest.mark.parametrize('resource', ['indexer', 'indexerproxy', 'applications'])
def test_prowlarr_connections(live, resource):
    api = live.arr['prowlarr']
    entries = api.call('GET', resource)
    if resource == 'indexer':
        entries = [i for i in entries if i.get('enable')]
    if resource == 'indexerproxy' and live.env.get('CREATE_PROXY', 'true').lower() != 'true':
        pytest.skip('Indexer proxy is disabled in this setup')
    assert entries, f'Prowlarr has no configured {resource}'
    for entry in entries:
        api.call('POST', resource + '/test', json=entry)


def test_prowlarr_sync(live):
    api = live.arr['prowlarr']
    command = api.call('POST', 'command', json={'name': 'ApplicationIndexerSync'})
    live.wait_command(api, command['id'], 'Prowlarr application indexer sync')
    for app in ('sonarr', 'radarr'):
        assert live.arr[app].call('GET', 'indexer'), f'{app} has no synced indexers'


def test_nntp_provider(live):
    env = live.env
    if env.get('SAB_CONFIG_PROVIDER', 'false').lower() != 'true':
        pytest.skip('No NNTP provider declared in setup')
    result = live.sab('config', name='test_server', server=env['SAB_SRV_NAME'],
                      host=env['SAB_SRV_HOST'], port=env['SAB_SRV_PORT'],
                      ssl=env['SAB_SRV_SSL'], ssl_verify=3,
                      username=env['SAB_SRV_USER'], password=env['SAB_SRV_PASS'],
                      connections=env['SAB_SRV_CONNS'], timeout=15)
    assert result.get('value', {}).get('result'), 'NNTP provider authentication/TLS test failed'


def test_vpn_route_egress_and_firewall(settings):
    route = compose(['exec', '-T', 'qbittorrent', 'ip', 'route', 'get', '1.1.1.1'],
                    settings, capture_output=True, text=True).stdout
    assert 'dev tun0' in route, 'Public traffic does not route through tun0'
    vpn_ip = compose(['exec', '-T', 'gluetun', 'cat', '/tmp/gluetun/ip'], settings,
                     capture_output=True, text=True).stdout.strip()
    actual_ip = compose(['exec', '-T', 'qbittorrent', 'curl', '-fsS', '--max-time', '20',
                         'https://api.ipify.org'], settings, capture_output=True, text=True).stdout.strip()
    assert bool(vpn_ip) and vpn_ip == actual_ip, 'qBittorrent egress differs from VPN egress'
    # A direct eth0 request must not bypass the firewall, even with the VPN up.
    result = subprocess.run(['docker', 'exec', 'qbittorrent', 'curl', '--interface', 'eth0',
                             '-fsS', '--connect-timeout', '4', '--max-time', '8',
                             'https://1.1.1.1/cdn-cgi/trace'], env=settings,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 28, 'Direct eth0 traffic was not blocked by a timeout'


@pytest.mark.parametrize('service,target', [('sonarr', '/shows'), ('sonarr', '/downloads'),
                                           ('radarr', '/movies'), ('radarr', '/downloads'),
                                           ('sabnzbd', '/downloads'), ('qbittorrent', '/downloads'),
                                           ('jellyfin', '/config'), ('jellyfin', '/cache')])
def test_storage_write_access_as_application_user(settings, service, target):
    # Only unique probe files are created; existing media/downloads are untouched.
    script = 'set -eu; f=$(mktemp "$1/.pytest-write-XXXXXX"); trap \'rm -f "$f"\' EXIT; test -w "$f"'
    compose(['exec', '-T', '--user', settings['OWNER_UID'] + ':' + settings['OWNER_GID'],
             service, 'sh', '-c', script, 'probe', target], settings,
            capture_output=True, text=True)


def test_saved_gpu_capabilities(settings):
    profile_path = hardware_dir(settings) / 'profile.json'
    assert profile_path.exists(), 'Setup has not created a hardware profile'
    profile = json.loads(profile_path.read_text())
    if profile['selected']['mode'] == 'software':
        pytest.skip('Software transcoding selected; live playback tests exercise FFmpeg')
    actual = Probe(settings).capabilities(profile['selected'])
    expected = profile['capabilities']
    for name, supported in expected.items():
        if name == 'decoders':
            assert set(supported) <= set(actual.get(name, [])), 'Saved hardware decoders no longer available'
        elif supported:
            assert actual.get(name), f'Saved GPU capability failed: {name}'


@pytest.fixture(scope='module')
def media(live):
    # Pagination avoids silently overlooking suitable media in a large library.
    items = []
    start = 0
    while True:
        page = live.jelly.call('GET', 'Items', params={
            'recursive': True, 'includeItemTypes': 'Movie,Episode',
            'fields': 'MediaStreams,MediaSources', 'startIndex': start, 'limit': 500})
        batch = page['Items']
        items.extend(batch)
        start += len(batch)
        if not batch or start >= page.get('TotalRecordCount', start):
            return [i for i in items if i.get('MediaSources')]


def test_jellyfin_plugins(live):
    plugins = live.jelly.call('GET', 'Plugins')
    broken = [p['Name'] for p in plugins if p.get('Status') not in ('Active', 'Disabled')]
    assert not broken, f'Plugins need attention: {broken}'


def test_jellyfin_direct_stream(live, media):
    if not media:
        pytest.skip('Add media and scan a Jellyfin library to test streaming')
    item = media[0]
    response = live.jelly.session.get(live.jelly.base + '/Videos/' + item['Id'] + '/stream',
                                      params={'Static': 'true'},
                                      headers={'Range': 'bytes=0-65535'}, timeout=45, stream=True)
    try:
        assert response.status_code in (200, 206), 'Jellyfin static streaming failed'
        assert next(response.iter_content(65536), b''), 'Jellyfin returned no media bytes'
    finally:
        response.close()


@pytest.mark.parametrize('source_kind', ['any', 'HDR10', 'DOVIWithHDR10'])
def test_jellyfin_hls_transcoding(live, media, settings, source_kind):
    candidates = media if source_kind == 'any' else [
        i for i in media if any(v.get('VideoRangeType') == source_kind
                               and (source_kind != 'DOVIWithHDR10' or v.get('Width', 0) >= 3000)
                               for v in i.get('MediaStreams', []))]
    if not candidates:
        pytest.skip(f'No suitable {source_kind} media; add media and scan the library')
    item = candidates[0]
    device = 'pytest-playback-' + uuid.uuid4().hex
    api = live.jelly
    # A distinct device prevents test cleanup from stopping viewer sessions.
    headers = dict(api.session.headers)
    headers['Authorization'] = headers['Authorization'].replace('DeviceId="stack-setup"', f'DeviceId="{device}"')
    profile = {'Name': 'Pytest SDR HLS', 'MaxStreamingBitrate': 4000000, 'DirectPlayProfiles': [],
               'TranscodingProfiles': [{'Container': 'ts', 'Type': 'Video', 'VideoCodec': 'h264',
                   'AudioCodec': 'aac', 'Protocol': 'hls', 'Context': 'Streaming',
                   'MaxAudioChannels': '2', 'MinSegments': 1, 'SegmentLength': 3,
                   'BreakOnNonKeyFrames': True}],
               'CodecProfiles': [{'Type': 'Video', 'Codec': 'h264', 'Conditions': [
                   {'Condition': 'LessThanEqual', 'Property': 'Width', 'Value': '1280', 'IsRequired': True},
                   {'Condition': 'Equals', 'Property': 'VideoRangeType', 'Value': 'SDR', 'IsRequired': True}]}]}
    info = api.call('POST', 'Items/' + item['Id'] + '/PlaybackInfo', headers=headers,
                    params={'userId': live.jelly_user['Id'], 'enableDirectPlay': False,
                            'enableDirectStream': False, 'enableTranscoding': True,
                            'maxStreamingBitrate': 4000000}, json={'DeviceProfile': profile})
    assert not info.get('ErrorCode'), 'Jellyfin playback negotiation failed'
    sources = [m for m in info['MediaSources'] if m.get('TranscodingUrl')]
    assert sources, 'Jellyfin did not offer a transcode'
    url = urljoin(api.base, sources[0]['TranscodingUrl'])
    query = parse_qs(urlsplit(url).query)
    session = info.get('PlaySessionId', query.get('PlaySessionId', [''])[0])
    try:
        segment = None
        for _ in range(5):
            response = api.session.get(url, headers=headers, timeout=90)
            assert response.ok, f'HLS request failed: HTTP {response.status_code}'
            if '.m3u8' in urlsplit(url).path or 'mpegurl' in response.headers.get('Content-Type', '').lower():
                choices = [line.strip() for line in response.text.splitlines() if line.strip() and not line.startswith('#')]
                assert choices, 'HLS playlist has no entries'
                url = urljoin(url, choices[0])
            else:
                segment = response.content
                break
        assert segment and len(segment) > 10000, 'No usable transcoded segment received'
        probe = json.loads(docker(settings, 'exec', '-i', 'jellyfin',
            '/usr/lib/jellyfin-ffmpeg/ffprobe', '-v', 'error', '-show_streams',
            '-of', 'json', '-i', 'pipe:0', input=segment))
        video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        assert video['codec_name'] == 'h264'
        assert video['width'] <= 1280
        if source_kind != 'any':
            assert video.get('color_transfer') == 'bt709', 'HDR source was not converted to SDR'
    finally:
        api.call('DELETE', 'Videos/ActiveEncodings', headers=headers,
                 params={'deviceId': device, 'playSessionId': session})


@pytest.mark.transfers
def test_real_usenet_download(live):
    tag = 'pytest-usenet-' + uuid.uuid4().hex
    job = None
    try:
        result = live.sab('addurl', name='https://sabnzbd.org/tests/test_download_100MB.nzb',
                          nzbname=tag, cat='*', priority=0)
        assert result.get('status') and result.get('nzo_ids'), 'SAB rejected the official test NZB'
        job = result['nzo_ids'][0]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            history = live.sab('history', nzo_ids=job, limit=100)['history']['slots']
            row = next((r for r in history if r['nzo_id'] == job), None)
            if row:
                assert row['status'] == 'Completed', 'Official SAB test job failed'
                assert int(row.get('downloaded', 0)) >= 1000000
                return
            time.sleep(2)
        pytest.fail('Official SAB test download did not finish in 180 seconds')
    finally:
        if job:
            queued = live.sab('queue')['queue']['slots']
            section = 'queue' if any(r['nzo_id'] == job for r in queued) else 'history'
            result = live.sab(section, name='delete', value=job, del_files=1)
            assert result.get('status'), 'Could not remove the test SAB job/files'


@pytest.mark.transfers
def test_real_torrent_download(live):
    # Creative Commons film; bounded partial transfer into its own directory.
    response = requests.get('https://webtorrent.io/torrents/sintel.torrent', timeout=30)
    assert response.ok and response.content.startswith(b'd'), 'Could not fetch the public test torrent'
    tag = 'pytest-torrent-' + uuid.uuid4().hex
    hashes = []
    live.connect_qbt()
    api = live.qbt
    original_hashes = {r['hash'] for r in api.call('GET', 'torrents/info')}
    if torrent_infohash(response.content) in original_hashes:
        pytest.skip('Sintel torrent already exists; existing downloads must not be altered')
    try:
        api.call('POST', 'torrents/add', files={'torrents': ('sintel.torrent', response.content)},
                 data={'savepath': '/downloads/.' + tag, 'tags': tag, 'category': '',
                       'autoTMM': 'false', 'sequentialDownload': 'true', 'dlLimit': '2097152', 'seedRatioLimit': '0'})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            rows = api.call('GET', 'torrents/info', params={'tag': tag})
            hashes = [r['hash'] for r in rows if r['hash'] not in original_hashes]
            if sum(r.get('downloaded', 0) for r in rows) >= 8000000:
                return
            time.sleep(2)
        pytest.fail('Test torrent did not receive 8 MB from peers within 120 seconds')
    finally:
        if not hashes:
            hashes = [r['hash'] for r in api.call('GET', 'torrents/info', params={'tag': tag})
                      if r['hash'] not in original_hashes]
        if hashes:
            api.call('POST', 'torrents/delete', data={'hashes': '|'.join(hashes), 'deleteFiles': 'true'})
        api.call('POST', 'torrents/deleteTags', data={'tags': tag})
