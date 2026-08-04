"""Cover every EventBroadcaster convenience method."""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.server.websocket.broadcaster import EventBroadcaster
from backend.server.websocket.events import WSEventType


def _broadcaster() -> tuple[EventBroadcaster, MagicMock]:
    manager = MagicMock()
    return EventBroadcaster(manager), manager


def test_emit_constructs_event_and_broadcasts() -> None:
    b, manager = _broadcaster()
    b.emit(WSEventType.AGENT_STATUS_CHANGE, {"x": 1})
    manager.broadcast.assert_called_once()
    event = manager.broadcast.call_args.args[0]
    assert event.event_type == WSEventType.AGENT_STATUS_CHANGE
    assert event.payload == {"x": 1}


def test_agent_status_change_merges_extras() -> None:
    b, manager = _broadcaster()
    b.agent_status_change("A1", "busy", phase=3)
    payload = manager.broadcast.call_args.args[0].payload
    assert payload == {"agent_id": "A1", "status": "busy", "phase": 3}


def test_task_start() -> None:
    b, manager = _broadcaster()
    b.task_start("t1", "A1", "desc")
    payload = manager.broadcast.call_args.args[0].payload
    assert payload == {"task_id": "t1", "agent_id": "A1", "description": "desc"}


def test_task_complete_defaults_success_true() -> None:
    b, manager = _broadcaster()
    b.task_complete("t1", "A1")
    payload = manager.broadcast.call_args.args[0].payload
    assert payload["success"] is True


def test_phase_transition() -> None:
    b, manager = _broadcaster()
    b.phase_transition(2, 3, "running")
    ev = manager.broadcast.call_args.args[0]
    assert ev.event_type == WSEventType.PHASE_TRANSITION
    assert ev.payload == {"from_phase": 2, "to_phase": 3, "status": "running"}


def test_audit_entry() -> None:
    b, manager = _broadcaster()
    b.audit_entry({"id": 1})
    assert manager.broadcast.call_args.args[0].payload == {"entry": {"id": 1}}


def test_session_snapshot_passes_dict_whole() -> None:
    b, manager = _broadcaster()
    snap = {"phase": 2, "status": "ok"}
    b.session_snapshot(snap)
    assert manager.broadcast.call_args.args[0].payload == snap


def test_blocker_raised_and_resolved() -> None:
    b, manager = _broadcaster()
    b.blocker_raised("blk-1", "stuck", 3)
    b.blocker_resolved("blk-1")
    assert manager.broadcast.call_count == 2


def test_contract_state_change() -> None:
    b, manager = _broadcaster()
    b.contract_state_change("C1", "locked")
    assert manager.broadcast.call_args.args[0].payload == {
        "contract_id": "C1",
        "state": "locked",
    }


def test_architecture_update_passes_payload_whole() -> None:
    b, manager = _broadcaster()
    payload = {"arch": "v2"}
    b.architecture_update(payload)
    assert manager.broadcast.call_args.args[0].payload == payload


def test_test_result_stream_passes_payload_whole() -> None:
    b, manager = _broadcaster()
    b.test_result_stream({"case": "C1", "pass": True})
    assert manager.broadcast.call_args.args[0].payload["pass"] is True


def test_steering_update_passes_payload_whole() -> None:
    b, manager = _broadcaster()
    b.steering_update({"rule": "prefer-ears"})
    assert manager.broadcast.call_args.args[0].payload["rule"] == "prefer-ears"


def test_package_invalidated() -> None:
    b, manager = _broadcaster()
    b.package_invalidated("pkg-1")
    assert manager.broadcast.call_args.args[0].payload == {"package_id": "pkg-1"}


def test_gap_list_update_serialises_gaps() -> None:
    b, manager = _broadcaster()
    g1 = MagicMock()
    g1.model_dump.return_value = {"id": 1}
    g2 = MagicMock()
    g2.model_dump.return_value = {"id": 2}
    b.gap_list_update([g1, g2])
    payload = manager.broadcast.call_args.args[0].payload
    assert payload["total"] == 2
    assert payload["gaps"] == [{"id": 1}, {"id": 2}]
