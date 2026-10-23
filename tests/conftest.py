"""Explicit opt-in for tests using live services or submitting test downloads."""
import pytest


def pytest_addoption(parser):
    parser.addoption('--run-stack', action='store_true', help='Test the configured running media stack')
    parser.addoption('--run-transfers', action='store_true', help='Also submit and clean up disposable test downloads')


def pytest_collection_modifyitems(config, items):
    live = config.getoption('--run-stack')
    transfers = config.getoption('--run-transfers')
    if transfers and not live:
        raise pytest.UsageError('--run-transfers requires --run-stack')
    for item in items:
        if 'stack' in item.keywords and not live:
            item.add_marker(pytest.mark.skip(reason='use --run-stack after completing setup'))
        if 'transfers' in item.keywords and not transfers:
            item.add_marker(pytest.mark.skip(reason='use --run-stack --run-transfers for real downloads'))
