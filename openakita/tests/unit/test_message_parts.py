"""Tests for the ordered message-parts projection and the persistence of rich
card state (plan snapshot, answered ask_user) that lets the chat UI re-display
losslessly after a reload / multi-window switch (#615)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.message_parts import (
    build_message_parts,
    normalize_chat_todo,
    serialize_plan_to_chat_todo,
)
from openakita.api.routes.chat import _backfill_ask_user_answer
from openakita.api.routes.sessions import router
from openakita.sessions import SessionManager


def _plan() -> dict:
    return {
        "id": "plan-1",
        "task_summary": "Ship the feature",
        "status": "in_progress",
        "steps": [
            {"id": "s1", "description": "design", "status": "completed", "result": "ok"},
            {"id": "s2", "description": "build", "status": "in_progress"},
        ],
    }


def test_serialize_plan_to_chat_todo_camelcases_and_keeps_steps():
    todo = serialize_plan_to_chat_todo(_plan())
    assert todo == {
        "id": "plan-1",
        "taskSummary": "Ship the feature",
        "status": "in_progress",
        "steps": [
            {"id": "s1", "description": "design", "status": "completed", "result": "ok"},
            {"id": "s2", "description": "build", "status": "in_progress"},
        ],
    }


def test_serialize_plan_handles_none_and_non_dict():
    assert serialize_plan_to_chat_todo(None) is None
    assert serialize_plan_to_chat_todo("nope") is None  # type: ignore[arg-type]


def test_serialize_plan_is_idempotent_on_event_shape():
    # The save-path captures the live ``todo_created`` SSE plan, which is
    # already camelCase. serialize_plan_to_chat_todo must accept it unchanged.
    event_plan = {
        "id": "p1",
        "taskSummary": "do it",
        "status": "completed",
        "steps": [{"id": "s1", "description": "a", "status": "completed"}],
    }
    out = serialize_plan_to_chat_todo(event_plan)
    assert out["taskSummary"] == "do it"
    assert out["status"] == "completed"
    assert out["steps"][0]["status"] == "completed"


def test_normalize_chat_todo_passthrough_frontend_shape():
    front = {"id": "x", "taskSummary": "t", "steps": [], "status": "completed"}
    assert normalize_chat_todo(front) is front
    # backend shape gets converted
    assert normalize_chat_todo(_plan())["taskSummary"] == "Ship the feature"


def test_build_message_parts_orders_blocks_and_marks_heavy_text():
    msg = {
        "role": "assistant",
        "content": "here you go",
        "chain_summary": [{"iteration": 0}],
        "todo": _plan(),
        "artifacts": [{"artifact_type": "image", "file_url": "/api/files?path=a", "path": "a"}],
        "ask_user": {"question": "ok?", "answered": True, "answer": "yes"},
    }
    parts = build_message_parts(msg)
    kinds = [p["kind"] for p in parts]
    assert kinds == ["reasoning", "plan", "text", "attachment", "ask_user"]
    # heavy text blocks are markers (no inlined payload)
    text_part = next(p for p in parts if p["kind"] == "text")
    assert set(text_part.keys()) == {"kind", "id"}
    # small blocks inline their data
    plan_part = next(p for p in parts if p["kind"] == "plan")
    assert plan_part["todo"]["taskSummary"] == "Ship the feature"
    ask_part = next(p for p in parts if p["kind"] == "ask_user")
    assert ask_part["ask"]["answered"] is True


def test_build_message_parts_empty_for_user():
    assert build_message_parts({"role": "user", "content": "hi"}) == []


def test_build_message_parts_todo_override():
    msg = {"role": "assistant", "content": "x"}
    parts = build_message_parts(msg, todo=serialize_plan_to_chat_todo(_plan()))
    assert [p["kind"] for p in parts] == ["plan", "text"]


# ── history route surfaces todo / parts / answered ask_user ──


def _history_client(tmp_path, **assistant_meta) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "do the thing")
    session.add_message("assistant", "working on it", **assistant_meta)
    app.state.session_manager = manager
    return TestClient(app)


def test_history_exposes_plan_snapshot_and_parts(tmp_path):
    client = _history_client(tmp_path, todo=serialize_plan_to_chat_todo(_plan()))
    body = client.get("/api/sessions/conv1/history").json()
    assistant = body["messages"][-1]
    assert assistant["todo"]["taskSummary"] == "Ship the feature"
    assert [p["kind"] for p in assistant["parts"]] == ["plan", "text"]
    assert "active_todo" in body


def test_history_exposes_answered_ask_user(tmp_path):
    client = _history_client(
        tmp_path,
        ask_user={"question": "pick", "answered": True, "answer": "opt_a"},
    )
    body = client.get("/api/sessions/conv1/history").json()
    assistant = body["messages"][-1]
    assert assistant["ask_user"]["answered"] is True
    assert assistant["ask_user"]["answer"] == "opt_a"
    assert any(p["kind"] == "ask_user" for p in assistant["parts"])


# ── ask_user answer backfill ──


def test_backfill_marks_last_unanswered_ask_user(tmp_path):
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "question please")
    session.add_message("assistant", "which option?", ask_user={"question": "which?"})
    # user answers -> new follow-up message + backfill
    session.add_message("user", "opt_b")
    _backfill_ask_user_answer(session, "opt_b")

    assistant = [m for m in session.context.messages if m.get("role") == "assistant"][-1]
    assert assistant["ask_user"]["answered"] is True
    assert assistant["ask_user"]["answer"] == "opt_b"


def test_backfill_noop_when_no_pending_prompt(tmp_path):
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "hi")
    session.add_message("assistant", "hello")  # no ask_user
    session.add_message("user", "thanks")
    _backfill_ask_user_answer(session, "thanks")

    assistant = [m for m in session.context.messages if m.get("role") == "assistant"][-1]
    assert "ask_user" not in assistant
