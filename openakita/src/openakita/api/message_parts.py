"""Ordered message-parts projection for the chat UI.

This mirrors the frontend ``MessagePart`` discriminated union
(``apps/setup-center/src/types.ts``) and the client-side projection in
``apps/setup-center/src/views/chat/utils/messageParts.ts``. It is the
server-side half of the "single rendering model" that lets rich cards
(reasoning / plan / answered ask_user / attachments / …) re-display
losslessly after a reload or window switch — closing the gap where the
live SSE stream and the persisted flat history used to diverge.

Design:
  - Heavy text blocks (``reasoning`` / ``text``) are *markers*: the
    renderer reads their payload from the corresponding flat field that the
    history endpoint already returns, so this projection never re-inlines
    (and thus never doubles) the answer text or thinking chain on the wire.
  - Small blocks (``plan`` / ``attachment`` / ``ask_user`` / ``error``)
    inline their data so a part is self-describing.

The projection is derived from the stored message dict, so it is NOT
persisted itself — it cannot bloat ``sessions.json`` and is never trimmed.
"""

from __future__ import annotations

from typing import Any


def serialize_plan_to_chat_todo(plan: dict | None) -> dict | None:
    """Convert a backend plan dict (snake_case) into the frontend ChatTodo
    shape (camelCase ``taskSummary``). Mirrors the SSE ``todo_created`` payload
    in ``reasoning_engine.py`` so the persisted snapshot and the live event
    look identical to the UI.
    """
    if not isinstance(plan, dict):
        return None
    steps_src = plan.get("steps") or []
    steps: list[dict] = []
    for s in steps_src:
        if not isinstance(s, dict):
            continue
        steps.append(
            {
                "id": s.get("id", ""),
                "description": s.get("description", ""),
                "status": s.get("status", "pending"),
                **({"result": s.get("result")} if s.get("result") else {}),
            }
        )
    return {
        "id": plan.get("id", ""),
        "taskSummary": plan.get("task_summary", plan.get("taskSummary", "")),
        "steps": steps,
        "status": plan.get("status", "in_progress"),
    }


def build_message_parts(msg: dict, *, todo: dict | None = None) -> list[dict]:
    """Build the ordered parts projection for one stored assistant message.

    ``todo`` overrides ``msg['todo']`` (used to attach a live in-flight plan
    snapshot during hydration). Returns ``[]`` for non-assistant messages —
    user / system messages render directly, not via parts.
    """
    if msg.get("role") != "assistant":
        return []

    parts: list[dict] = []

    if msg.get("chain_summary") or msg.get("chain_timeline"):
        parts.append({"kind": "reasoning", "id": "reasoning"})
    if msg.get("org_timeline"):
        parts.append({"kind": "org_timeline", "id": "org_timeline"})

    plan = todo if todo is not None else msg.get("todo")
    plan_todo = serialize_plan_to_chat_todo(plan) if isinstance(plan, dict) else plan
    if isinstance(plan_todo, dict) and plan_todo.get("steps"):
        parts.append({"kind": "plan", "id": f"plan:{plan_todo.get('id', '')}", "todo": plan_todo})

    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        parts.append({"kind": "text", "id": "text"})

    artifacts = msg.get("artifacts")
    if isinstance(artifacts, list):
        for i, art in enumerate(artifacts):
            parts.append({"kind": "attachment", "id": f"attachment:{i}", "artifact": art})

    ask_user = msg.get("ask_user")
    if isinstance(ask_user, dict):
        parts.append({"kind": "ask_user", "id": "ask_user", "ask": ask_user})

    if msg.get("is_truncated") or msg.get("stream_error"):
        parts.append({"kind": "error", "id": "error"})

    return parts


def normalize_chat_todo(todo: Any) -> dict | None:
    """Accept either a frontend ChatTodo dict or a backend plan dict and return
    the ChatTodo shape, or ``None``."""
    if not isinstance(todo, dict):
        return None
    if "taskSummary" in todo and "steps" in todo:
        return todo
    return serialize_plan_to_chat_todo(todo)
