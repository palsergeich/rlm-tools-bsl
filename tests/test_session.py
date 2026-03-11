import tempfile

import pytest
from rlm_tools_bsl.session import SessionManager


def test_create_session():
    manager = SessionManager(max_sessions=5)
    session_id = manager.create(path=tempfile.gettempdir(), query="test query")
    assert session_id is not None
    assert manager.get(session_id) is not None


def test_max_sessions_enforced():
    manager = SessionManager(max_sessions=2)
    manager.create(path=tempfile.gettempdir(), query="q1")
    manager.create(path=tempfile.gettempdir(), query="q2")
    with pytest.raises(RuntimeError, match="max sessions"):
        manager.create(path=tempfile.gettempdir(), query="q3")


def test_end_session():
    manager = SessionManager(max_sessions=5)
    session_id = manager.create(path=tempfile.gettempdir(), query="test")
    manager.end(session_id)
    assert manager.get(session_id) is None
