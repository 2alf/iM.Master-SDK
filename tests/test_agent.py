"""Unit tests for the model-agnostic LLM control layer — no hardware.

Uses DryRobot (the same stand-in the MCP server uses under IMMASTER_DRY_RUN=1)
so the whole dispatch path is exercised without BLE.
"""

from immaster import agent
from immaster.testing import DryRobot


def test_openai_tools_shape():
    tools = agent.openai_tools()
    assert all(t["type"] == "function" for t in tools)
    names = {t["function"]["name"] for t in tools}
    assert {"drive", "stop", "get_state"} <= names


def test_parse_action_extracts_json():
    text = 'Sure, moving now. {"action": "forward", "duration_s": 2}'
    action = agent.parse_action(text)
    assert action == {"action": "forward", "duration_s": 2}


def test_parse_action_returns_last_action():
    text = '{"action": "spin_cw"} then {"action": "forward"}'
    assert agent.parse_action(text)["action"] == "forward"


def test_parse_action_none_when_no_action():
    assert agent.parse_action("just chatting, no json here") is None


def test_dispatch_drive():
    bot = DryRobot()
    result = agent.dispatch(bot, "drive", {"motion": "forward"})
    assert result["ok"] is True
    assert bot.state == "forward"


def test_dispatch_rejects_unknown_motion():
    bot = DryRobot()
    result = agent.dispatch(bot, "drive", {"motion": "moonwalk"})
    assert result["ok"] is False


def test_dispatch_stop():
    bot = DryRobot()
    bot.command("forward")
    result = agent.dispatch(bot, "stop", {})
    assert result["ok"] is True
    assert bot.state == "stop"


def test_dispatch_action_tolerates_missing_tool_field():
    bot = DryRobot()
    # small models often drop the "tool" field and give the motion directly
    result = agent.dispatch_action(bot, {"motion": "spin_ccw"})
    assert result["ok"] is True
    assert bot.state == "spin_ccw"


def test_dispatch_action_motion_as_tool_name():
    bot = DryRobot()
    result = agent.dispatch_action(bot, {"tool": "forward"})
    assert result["ok"] is True
    assert bot.state == "forward"


def test_spoken_text_prefers_say_field():
    action = {"action": "forward", "say": "here we go!"}
    assert agent.spoken_text("{...}", action) == "here we go!"


def test_spoken_text_strips_json_from_reply():
    reply = 'Rolling forward. {"action": "forward"}'
    assert agent.spoken_text(reply) == "Rolling forward."
