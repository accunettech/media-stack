"""Offline regressions for the API changes encountered during upgrades."""
from unittest.mock import Mock

import pytest
import requests

import config_stack
from config_stack import API, SetupError, Stack


@pytest.fixture
def stack(tmp_path):
    return Stack({'UI_USER': 'test-user', 'UI_PASS': 'test-password',
                  'STACK_HARDWARE_DIR': str(tmp_path)})


@pytest.fixture
def login_api(monkeypatch):
    api = Mock()
    monkeypatch.setattr(config_stack, 'API', Mock(return_value=api))
    monkeypatch.setattr(config_stack, 'wait_for', Mock(return_value=True))
    monkeypatch.setattr(config_stack, 'compose', Mock(side_effect=AssertionError('Unexpected Docker access')))
    return api


@pytest.mark.parametrize('status,body,expected', [(200, b'Ok.', 'Ok.'), (204, b'', None)])
def test_login_response_formats(status, body, expected):
    response = requests.Response()
    response.status_code = status
    response._content = body
    session = Mock()
    session.request.return_value = response
    assert API('http://test/api/v2', session=session).call('POST', 'auth/login') == expected


@pytest.mark.parametrize('success', ['Ok.', None])
def test_existing_login_accepts_both_versions(stack, login_api, success):
    login_api.call.return_value = success
    stack.connect_qbt()
    login_api.call.assert_called_once_with('POST', 'auth/login', data={
        'username': 'test-user', 'password': 'test-password'})


@pytest.mark.parametrize('failure', ['Fails.', SetupError('POST auth/login: HTTP 401')])
def test_read_only_check_never_initializes_credentials(stack, login_api, failure):
    login_api.call.side_effect = [failure]
    with pytest.raises(SetupError, match='qBittorrent login failed'):
        stack.connect_qbt(allow_initialize=False)
    config_stack.compose.assert_not_called()


def test_fresh_login_uses_temporary_password_and_verifies_new_credentials(stack, login_api, monkeypatch):
    login_api.call.side_effect = [SetupError('POST auth/login: HTTP 401'), None, None, None]
    docker = Mock(return_value=Mock(stdout='temporary password: initial-password'))
    monkeypatch.setattr(config_stack, 'compose', docker)
    stack.connect_qbt(allow_initialize=True)
    calls = login_api.call.call_args_list
    assert calls[1].kwargs['data'] == {'username': 'admin', 'password': 'initial-password'}
    assert calls[2].args == ('POST', 'app/setPreferences')
    assert calls[3].kwargs['data']['password'] == 'test-password'


def test_login_transport_errors_do_not_trigger_password_reset(stack, login_api):
    login_api.call.side_effect = SetupError('POST auth/login: connection failed')
    with pytest.raises(SetupError, match='connection failed'):
        stack.connect_qbt(allow_initialize=True)
    config_stack.compose.assert_not_called()


@pytest.mark.parametrize('expired', [False, True])
def test_logout_always_closes_and_clears_qbt_session(stack, expired):
    api = Mock()
    stack.qbt = api
    if expired:
        api.call.side_effect = SetupError('POST auth/logout: HTTP 403')
    stack.logout_qbt()
    api.call.assert_called_once_with('POST', 'auth/logout')
    api.session.close.assert_called_once()
    assert stack.qbt is None


def test_reconnect_releases_previous_session(stack, login_api):
    previous = Mock()
    stack.qbt = previous
    login_api.call.return_value = None
    stack.connect_qbt()
    previous.call.assert_called_once_with('POST', 'auth/logout')
    previous.session.close.assert_called_once()
    assert stack.qbt is login_api


def test_prowlarr_usenet_creation_supplies_required_redirect(stack, monkeypatch):
    # Exercise reconciliation using the real declared NZBgeek template.
    desired = stack.defaults['prowlarr']
    desired['indexer'] = [next(i for i in desired['indexer'] if i['protocol'] == 'usenet')]
    desired['indexerproxy'] = []
    desired['applications'] = []
    stack.env.update(INDEXERS='NZBgeek')
    stack.variables.update(USENET_DEFAULT_BASEURL='https://indexer.invalid', USENET_DEFAULT_APIKEY='test-key')
    api = Mock()
    api.call.side_effect = lambda method, path, **kw: {'id': 1} if method == 'POST' else []
    stack.arr['prowlarr'] = api
    monkeypatch.setattr(stack, 'wait_command', Mock())
    stack.configure_prowlarr()
    payload = next(c.kwargs['json'] for c in api.call.call_args_list if c.args == ('POST', 'indexer'))
    assert payload['redirect'] is True
    assert next(f['value'] for f in payload['fields'] if f['name'] == 'apiKey') == 'test-key'


def test_torrent_identity_uses_original_info_bytes():
    import hashlib
    from stack_test_helpers import torrent_infohash
    info = b'd6:lengthi123e4:name6:sintele'
    payload = b'd8:announce11:https://url4:info' + info + b'e'
    assert torrent_infohash(payload) == hashlib.sha1(info).hexdigest()


@pytest.mark.parametrize('payload', [b'not a torrent', b'd4:name6:sintele'])
def test_torrent_without_identity_is_rejected(payload):
    from stack_test_helpers import torrent_infohash
    with pytest.raises(ValueError):
        torrent_infohash(payload)


def test_transfer_skips_existing_torrent_without_adding_or_deleting(monkeypatch):
    import test_stack_e2e
    from stack_test_helpers import torrent_infohash
    payload = b'd4:infod6:lengthi123e4:name6:sintelee'
    response = Mock(ok=True, content=payload)
    monkeypatch.setattr(test_stack_e2e.requests, 'get', Mock(return_value=response))
    live = Mock()
    live.qbt.call.return_value = [{'hash': torrent_infohash(payload)}]
    with pytest.raises(pytest.skip.Exception, match='already exists'):
        test_stack_e2e.test_real_torrent_download(live)
    live.qbt.call.assert_called_once_with('GET', 'torrents/info')
