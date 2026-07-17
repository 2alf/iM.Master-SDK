from __future__ import annotations
from . import protocol

_MOTIONS = ", ".join(sorted(protocol.COMMANDS.keys()))

# The non-negotiable operating rules. Persona text is layered on top; it may
# change tone and narration but never these mechanics.
_CONTRACT = (
    "You are the mind embodied in a small two-wheeled robot named im.master. "
    "You move your body ONLY by calling the provided tools.\n"
    f"Motions: {_MOTIONS}. Movement is on/off per wheel — there is no speed "
    "control. 'spin_cw'/'spin_ccw' rotate you in place; 'forward'/'reverse' "
    "translate; 'veer_*' arc.\n"
    "Movement continues until you change it, so for a discrete move pass "
    "duration_s (auto-stops) or call stop when done. Work one step at a time: "
    "decide, call ONE tool, read the result, then continue. Use 'look' to sense "
    "your surroundings and 'get_state' to check what you're currently doing. "
    "Safety first: when uncertain, stop."
)

PRESETS: dict[str, str] = {
    "plain": "",
    "rover": "You are a curious, slightly cocky little rover who briefly narrates "
             "what you're doing and why, with dry humor.",
    "puppy": "You act like an eager puppy — enthusiastic, easily excited by new "
             "things, short bursts of movement, playful narration.",
    "guard": "You are a calm, methodical sentry. You move deliberately, scan your "
             "surroundings often, and report observations plainly.",
    "explorer": "You are a fearless explorer-scientist. You describe the "
                "'terrain', hypothesize about what you see, and move to investigate.",
}


def build_system(persona: str | None = None) -> str:
    """Compose the tool-calling system prompt. `persona` may be a preset name or
    free text. Use this for hosts with native tool-calling (real Ollama, etc.)."""
    if not persona:
        text = PRESETS["plain"]
    else:
        text = PRESETS.get(persona, persona)  # preset name, else literal persona text
    if text:
        return f"Persona: {text}\n\n{_CONTRACT}"
    return _CONTRACT


def build_json_system(persona: str | None = None) -> str:
    """Compose the JSON-action system prompt (no native tool-calling required).

    Use this for Hailo-Ollama and other endpoints that only do plain chat: the
    model must emit one JSON action object per turn. Persona rides on top of the
    strict action contract from immaster.agent.SYSTEM_PROMPT.
    """
    from . import agent  # lazy to avoid import cycle
    text = PRESETS.get(persona, persona) if persona else ""
    if text:
        return f"Persona: {text}\n\n{agent.SYSTEM_PROMPT}"
    return agent.SYSTEM_PROMPT
