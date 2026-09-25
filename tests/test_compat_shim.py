"""The ghost-response counter in ``hive._compat`` (ADR-007 Amendment #2).

The mcp 1.x respond-after-cancel patch that used to live beside it is gone
(#434); ``tests/test_cancel_race.py`` guards the behaviour it provided.
Counter state is reset around every test by the autouse
``_reset_ghost_response_counter`` fixture in ``conftest.py``.
"""

from __future__ import annotations

from hive import _compat as _hc


def test_record_counts_by_source_and_keeps_the_last_tool() -> None:
    _hc.GHOST_RESPONSES.record(tool="vault_write", source="deadline")
    _hc.GHOST_RESPONSES.record(source="cancellation")

    snap = _hc.GHOST_RESPONSES.snapshot()
    assert snap["total"] == 2
    assert snap["last_tool"] == "vault_write"
    assert isinstance(snap["last_seen"], str)
    assert snap["by_source"] == {"deadline": 1, "cancellation": 1}


def test_ghost_response_snapshot_defaults_empty() -> None:
    """Fresh counter snapshot has total=0, null last_* fields, empty by_source.

    ``by_source`` was added by HIVE-115 PR-3 to discriminate the
    ``cancellation`` vs ``deadline`` (bounded_call-driven) triggers. An empty
    dict on a fresh counter preserves the read contract for clients that only
    look at ``total``.
    """
    snap = _hc.GHOST_RESPONSES.snapshot()
    assert snap == {
        "total": 0,
        "last_seen": None,
        "last_tool": None,
        "by_source": {},
    }
