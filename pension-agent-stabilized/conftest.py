"""Offline tests cannot use user credentials, reach APIs or mutate serving DB."""
import os
import shutil
import socket
from pathlib import Path
import tempfile


def pytest_sessionstart(session):
    temp = tempfile.TemporaryDirectory(prefix='pension_offline_tests_')
    session._pension_temp = temp
    db = Path(temp.name) / 'legal.db'
    shutil.copy2(Path(__file__).parent / 'data/legal/pension_legal.db', db)
    os.environ.update(CLOVA_STUDIO_API_KEY='offline-test-key-not-valid', LAW_API_OC='',
                      PRODUCT_DB_BACKEND='standard_json', PENSION_AGENT_MODE='langgraph',
                      LAW_QUERY_FALLBACK_API='0', LEGAL_DB_PATH=str(db),
                      LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false',
                      PENSION_ENABLE_LLM_NORMALIZER='0')
    def deny_network(*args, **kwargs):
        raise AssertionError('Offline tests prohibit network connections')
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network


def pytest_sessionfinish(session, exitstatus):
    temp = getattr(session, '_pension_temp', None)
    if temp:
        temp.cleanup()
