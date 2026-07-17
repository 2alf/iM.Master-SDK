from __future__ import annotations
import json
import re
from typing import Any

from . import protocol

_MOTIONS = sorted(protocol.COMMANDS.keys())


# Model-agnostic LLM control surface for the im.master robot.
################# vendor-neutral tool specs 
TOOLS: list[dict[str, Any]] = [
    {
        "name": "drive",
        "description": (
            "Set the robot's movement. It keeps doing this until you call drive "
            "again or stop. Motion is not proportional — each wheel is on or off. "
            "'forward'/'reverse' translate; 'spin_cw'/'spin_ccw' rotate in place; "
            "'veer_left'/'veer_right' arc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motion": {"type": "string", "enum": _MOTIONS,
                           "description": "Which movement to perform."},
                "duration_s": {"type": "number",
                               "description": "Optional. Run this many seconds then "
                                              "auto-stop. Omit to keep going."},
            },
            "required": ["motion"],
        },
    },
    {"name": "stop", "description": "Immediately halt all movement.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_state", "description": "Return the current movement command.",
     "input_schema": {"type": "object", "properties": {}}},
]


def openai_tools() -> list[dict[str, Any]]: # reshaped for OpenAI/Ollama/llama.cpp native tool-calling.
    return [
        {"type": "function",
         "function": {"name": t["name"], "description": t["description"],
                      "parameters": t["input_schema"]}}
        for t in TOOLS
    ]


SYSTEM_PROMPT = (
    "You are the mind of a small two-wheeled robot. Each turn reply with ONE "
    "JSON object and NOTHING else:\n"
    '  {"action": "<move>", "duration_s": <optional seconds>}\n'
    f'<move> MUST be exactly one of: {", ".join(_MOTIONS)}.\n'
    'You MUST always include "action" with a direction — never leave it out. '
    'Example: {"action": "forward", "duration_s": 2}\n'
    "Motion is on/off per wheel (no speed). spin_cw/spin_ccw rotate in place; "
    "forward/reverse translate. Use duration_s for a timed move; send "
    '{"action": "stop"} when the goal is done.'
)

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)
_ACTION_KEYS = ("action", "tool", "motion", "command")


def parse_action(text: str) -> dict[str, Any] | None: # Extract the last JSON action object from free-form model output.
    match = None
    for m in _JSON_RE.finditer(text):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and any(k in obj for k in _ACTION_KEYS):
            match = obj
    return match


def dispatch(robot, name: str, args: dict[str, Any]) -> dict[str, Any]: # Execute a tool/action against a Robot-like object.
    if name == "drive":
        motion = args.get("motion")
        if motion not in protocol.COMMANDS:
            return {"ok": False, "error": f"unknown motion {motion!r}"}
        dur = args.get("duration_s")
        if dur:
            robot.for_duration(motion, float(dur))
            return {"ok": True, "motion": motion, "ran_for_s": dur, "state": robot.state}
        robot.command(motion)
        return {"ok": True, "motion": motion, "state": robot.state}
    if name == "stop":
        robot.stop()
        return {"ok": True, "state": robot.state}
    if name == "get_state":
        return {"ok": True, "state": robot.state}
    return {"ok": False, "error": f"unknown tool {name!r}"}


def spoken_text(reply: str, action: dict[str, Any] | None = None) -> str | None:
    if action:
        for k in ("say", "message", "speech", "comment", "thought"):
            v = action.get(k)
            if v:
                return str(v).strip()
    stripped = _JSON_RE.sub("", reply).strip()
    return stripped or None


def dispatch_action(robot, action: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a single parsed-action dict (from parse_action).

    Tolerant of how small models phrase actions:
      {"tool": "drive", "motion": "forward"}   canonical
      {"motion": "forward"}                     tool omitted
      {"tool": "forward"}                       motion used as the tool name
      {"action": "forward"} / {"command": ...}  alt keys
    """
    name = action.get("tool") or action.get("action") or action.get("command")

    if name is None and "motion" in action:
        name = "drive"
    if name in protocol.COMMANDS and name != "stop":
        action = {**action, "motion": name}
        name = "drive"
    if not name:
        return {"ok": False, "error": "no tool/motion in action"}
    return dispatch(robot, name, action)
