"""Tests for OrgRuntime workbench (plugin) asset bridging.

When a workbench (plugin) tool returns a structured JSON payload, the
runtime should:

1. Recognise it as a plugin tool via ``_is_plugin_tool``.
2. Materialise any produced artifacts (``local_paths`` for local files,
   ``image_urls``/``video_url`` for remote ones, ``asset_ids`` via Asset
   Bus lookup) into ``<workspace>/plugin_assets/<plugin_id>/<task_id>/``.
3. Funnel each through ``_register_file_output`` so they land on the
   blackboard, the ProjectTask, and (when ``org_submit_deliverable`` is
   called without ``file_attachments``) on the TASK_DELIVERED payload.
4. Append a ``registered_attachments`` field to the JSON result so the
   LLM sees what was actually attached on its next ReAct turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.runtime import OrgRuntime


@pytest.fixture()
def runtime(org_manager: OrgManager) -> OrgRuntime:
    return OrgRuntime(org_manager)


def _make_agent_with_plugin_tool(tool_name: str = "tongyi_image_create") -> SimpleNamespace:
    """Minimal stand-in agent: a plugin manager with one loaded plugin
    that registered ``tool_name``."""
    plugin = SimpleNamespace(
        manifest=SimpleNamespace(id="tongyi-image", display_name_zh="通义生图"),
        api=SimpleNamespace(
            _registered_tools=[
                {"name": tool_name, "description": "...", "input_schema": {}}
            ]
        ),
    )
    pm = SimpleNamespace(
        loaded_plugins={"tongyi-image": plugin},
        host_refs={},
        _external_host_refs={},
    )
    pm.get_loaded = lambda pid: plugin if pid == "tongyi-image" else None
    agent = SimpleNamespace(_plugin_manager=pm)
    return agent


def test_is_plugin_tool_recognises_registered_name(runtime):
    agent = _make_agent_with_plugin_tool("tongyi_image_create")
    assert runtime._is_plugin_tool(agent, "tongyi_image_create") is True
    # subsequent lookups hit the per-agent cache
    assert runtime._is_plugin_tool(agent, "tongyi_image_create") is True
    assert runtime._is_plugin_tool(agent, "definitely_not_a_plugin_tool") is False


def test_is_plugin_tool_skips_org_prefix(runtime):
    agent = _make_agent_with_plugin_tool("org_delegate_task")
    # org_* tools must never be classified as plugin tools — they have
    # their own handling path in _patched_with_policy.
    assert runtime._is_plugin_tool(agent, "org_delegate_task") is False


def _make_agent_with_real_pluginapi_shape(
    *tool_names: str,
    plugin_id: str = "happyhorse-video",
) -> SimpleNamespace:
    """Stand-in agent whose plugin reports its tools as ``list[str]`` —
    matching the actual shape of ``PluginAPI._registered_tools``
    (``src/openakita/plugins/api.py:264`` does ``extend(tool_names)``
    where ``tool_names`` is ``list[str]``).

    Without this regression coverage the dict-shape ``_make_agent_with_plugin_tool``
    hides the bug where production lookups always miss.
    """
    plugin = SimpleNamespace(
        manifest=SimpleNamespace(id=plugin_id, display_name_zh="HappyHorse"),
        api=SimpleNamespace(_registered_tools=list(tool_names)),
    )
    pm = SimpleNamespace(
        loaded_plugins={plugin_id: plugin},
        host_refs={},
        _external_host_refs={},
    )
    pm.get_loaded = lambda pid: plugin if pid == plugin_id else None
    return SimpleNamespace(_plugin_manager=pm)


def test_is_plugin_tool_handles_list_of_strings_pluginapi_shape(runtime):
    """Regression for the silent disable: PluginAPI stores tool names as
    ``list[str]``, but ``_is_plugin_tool`` historically only looked for
    ``dict[name=...]``. That made the asset registration hook a no-op for
    every real plugin (e.g. happyhorse-video) and starved the blackboard
    of mp4/png RESOURCE entries."""
    agent = _make_agent_with_real_pluginapi_shape("hh_i2v", "hh_image_create")
    assert runtime._is_plugin_tool(agent, "hh_i2v") is True
    assert runtime._is_plugin_tool(agent, "hh_image_create") is True
    assert runtime._is_plugin_tool(agent, "not_a_plugin_tool") is False


def test_plugin_id_for_tool_handles_list_of_strings(runtime):
    agent = _make_agent_with_real_pluginapi_shape(
        "hh_i2v",
        plugin_id="happyhorse-video",
    )
    assert OrgRuntime._plugin_id_for_tool(agent, "hh_i2v") == "happyhorse-video"
    assert OrgRuntime._plugin_id_for_tool(agent, "no_such_tool") == ""


def test_is_plugin_tool_ignores_non_string_non_dict_entries(runtime):
    """Defensive: a plugin that registered nothing useful (e.g. None /
    int sneaked into the list) must not crash the enumerator."""
    plugin = SimpleNamespace(
        manifest=SimpleNamespace(id="weird-plugin"),
        api=SimpleNamespace(_registered_tools=[None, 42, "", "ok_tool"]),
    )
    pm = SimpleNamespace(
        loaded_plugins={"weird-plugin": plugin},
        host_refs={},
        _external_host_refs={},
    )
    agent = SimpleNamespace(_plugin_manager=pm)
    assert runtime._is_plugin_tool(agent, "ok_tool") is True
    assert runtime._is_plugin_tool(agent, "no") is False


async def test_record_plugin_asset_local_path_registers_attachment(runtime, tmp_path):
    """Local files already on disk should be hardlinked / copied into the
    workspace and registered as task attachments."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = tmp_path / "external" / "img.png"
    src.parent.mkdir()
    src.write_bytes(b"fake-png-bytes")

    agent = _make_agent_with_plugin_tool()
    registered: list[dict] = []

    def _fake_register(org_id, node_id, *, chain_id, filename, file_path, workspace):
        att = {"filename": filename, "file_path": file_path, "file_size": 11}
        registered.append(att)
        return att

    runtime._register_file_output = _fake_register  # type: ignore[assignment]
    runtime.get_current_chain_id = MagicMock(return_value=None)

    payload = {
        "ok": True,
        "task_id": "tk123",
        "status": "succeeded",
        "image_urls": [],
        "local_paths": [str(src)],
        "asset_ids": [],
    }

    enhanced = await runtime._record_plugin_asset_output(
        agent, "org_test", "node_wb",
        "tongyi_image_create", {}, json.dumps(payload),
        workspace=workspace,
    )

    assert enhanced is not None
    # one attachment was registered, and the new file lives inside the
    # workspace under plugin_assets/<plugin>/<task>/
    assert len(registered) == 1
    out_path = Path(registered[0]["file_path"]).resolve()
    workspace_resolved = workspace.resolve()
    assert str(out_path).startswith(str(workspace_resolved))
    assert "plugin_assets" in out_path.parts
    assert "tongyi-image" in out_path.parts
    assert "tk123" in out_path.parts

    enhanced_payload = json.loads(enhanced)
    assert "registered_attachments" in enhanced_payload
    assert enhanced_payload["registered_attachments"][0]["file_path"] == registered[0]["file_path"]
    # plugin attachments buffer is populated for org_submit_deliverable
    buf = runtime._node_plugin_attachments_in_task.get("org_test:node_wb") or []
    assert buf and buf[0]["file_path"] == registered[0]["file_path"]


async def test_record_plugin_asset_returns_none_when_no_artifacts(runtime, tmp_path):
    agent = _make_agent_with_plugin_tool()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = {"ok": True, "task_id": "tk", "status": "succeeded"}
    enhanced = await runtime._record_plugin_asset_output(
        agent, "org_test", "node_wb",
        "tongyi_image_create", {}, json.dumps(payload),
        workspace=workspace,
    )
    assert enhanced is None


async def test_record_plugin_asset_skips_non_json(runtime, tmp_path):
    agent = _make_agent_with_plugin_tool()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    out = await runtime._record_plugin_asset_output(
        agent, "org", "node", "tongyi_image_create", {},
        "this is not JSON", workspace=workspace,
    )
    assert out is None


async def test_record_plugin_asset_skips_when_ok_false(runtime, tmp_path):
    agent = _make_agent_with_plugin_tool()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = {
        "ok": False,
        "error": "no API key",
        "image_urls": ["https://example.com/should-be-ignored.png"],
    }
    out = await runtime._record_plugin_asset_output(
        agent, "org", "node", "tongyi_image_create", {},
        json.dumps(payload), workspace=workspace,
    )
    # failed plugin calls must not trigger downloads / registration
    assert out is None


def test_workbench_prompt_section_lists_tools_and_protocol(runtime):
    agent = _make_agent_with_plugin_tool("tongyi_image_create")
    agent._tools = [
        {"name": "tongyi_image_create", "description": "Create image"},
        {"name": "tongyi_image_status", "description": "Status"},
    ]
    prompt = runtime._build_workbench_prompt_section(
        agent, {"plugin_id": "tongyi-image", "template_id": "workbench:tongyi-image"},
    )
    assert "通义生图" in prompt
    assert "tongyi_image_create" in prompt
    # the contract lines must be present so LLM doesn't double-declare attachments
    assert "registered_attachments" in prompt
    assert "org_submit_deliverable" in prompt


def test_workbench_prompt_section_falls_back_to_plugin_id(runtime):
    agent = SimpleNamespace(_plugin_manager=None, _tools=[])
    prompt = runtime._build_workbench_prompt_section(
        agent, {"plugin_id": "some-workbench", "template_id": "workbench:some-workbench"},
    )
    # gracefully degrades when manager is unavailable; still emits a section
    assert "some-workbench" in prompt
