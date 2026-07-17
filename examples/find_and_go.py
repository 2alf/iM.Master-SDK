#!/usr/bin/env python3
"""
The AI addon in action: see-think-act with REAL object detection.

This is the loop the project actually ran end to end:

    camera (immaster.camera)  ──►  object detector (immaster.detect)
                                    │   "cup (right, near)"
                                    ▼
                    local LLM (immaster.llm)  ──►  {"say":.., "action":..}
                                    │
                                    ▼
                    immaster.robot  ──►  BLE  ──►  wheels

Every moving part is pluggable and picked at the top of run():
    * camera   — IPCamera (phone webcam) by default; swap for your own Camera.
    * detector — Hailo YOLO by default; any ObjectDetector works.
    * llm      — pick the backend with --backend (or LLM_BACKEND):
                   ollama   real Ollama on the Pi CPU/GPU        (default)
                   hailo    a model running on the Hailo NPU (Hailo-Ollama)
                   openai   llama.cpp / LM Studio / vLLM (/v1 API)

Run on the Pi (root for BLE):
    export LLM_MODEL=qwen2.5:1.5b
    export IPCAM_URL="http://user:pass@<phone-ip>:8080/shot.jpg"
    sudo -E python3 -m examples.find_and_go --backend ollama --voice --monitor \
        "find a cup and go to it"

Test off-robot (no BLE, no movement; camera + LLM must be reachable):
    IMMASTER_DRY_RUN=1 python3 -m examples.find_and_go --backend hailo "find a cup"
"""

from __future__ import annotations
import argparse
import os
from collections import deque

from immaster import agent, protocol
from immaster.llm import make_llm, BACKENDS
from immaster.camera import IPCamera
from immaster.detect import Detector, describe_detections
from immaster.personas import PRESETS
from immaster.voice import VoiceServer
from immaster.monitor import MonitorServer
from immaster import vision  # frame_change() "am I stuck?" cue

DRY_RUN = os.environ.get("IMMASTER_DRY_RUN") == "1"
STEPS = int(os.environ.get("EXPLORE_STEPS", "40"))
MIN_D, MAX_D, DEF_D = 0.5, 2.5, 1.2
_MOVES = ", ".join(sorted(protocol.COMMANDS))

SYSTEM = (
    "You are the mind of a small two-wheeled robot with a forward camera and "
    "object detection. Each turn you are told your GOAL and WHAT YOU SEE (a list "
    "of detected objects, each with a side: left/center/right and distance: "
    "near/far). Reply with ONE JSON object and NOTHING else: "
    '{"say": "<short spoken remark>", "action": "<move>", "duration_s": <seconds>}. '
    f"<move> is exactly one of: {_MOVES}. "
    "To approach a target object: if it is on your left use veer_left or "
    "spin_ccw, if on your right use veer_right or spin_cw, if centered drive "
    "forward. If you don't see the target, spin_cw or spin_ccw to search. If a "
    "move didn't change the view you are blocked — turn. You have ARRIVED when "
    'the target is center and near: then say so and {"action":"stop"}. '
    "Keep moves short."
)


def make_robot():
    if DRY_RUN:
        from immaster.testing import DryRobot
        return DryRobot()
    from immaster.robot import Robot
    return Robot()


def clamp_duration(action: dict) -> dict:
    d = action.get("duration_s")
    try:
        d = float(d)
    except (TypeError, ValueError):
        d = DEF_D
    action["duration_s"] = max(MIN_D, min(MAX_D, d))
    return action


def run(goal: str, persona: str | None, steps: int, backend: str | None,
        voice: VoiceServer | None, monitor: MonitorServer | None = None):
    llm = make_llm(backend)
    camera = IPCamera()
    print(f"brain: {type(llm).__name__} ({llm.model})")
    print("loading YOLO detector on the Hailo NPU...")
    detector = Detector()

    robot = make_robot()
    persona_txt = PRESETS.get(persona, persona) if persona else PRESETS["explorer"]
    system = f"Persona: {persona_txt}\n\n{SYSTEM}" if persona_txt else SYSTEM
    recent: deque[str] = deque(maxlen=5)
    prev_frame = None
    last_action = None

    try:
        for step in range(1, steps + 1):
            frame = camera.capture()
            if frame is None:
                print(f"[{step}] camera unreachable — stopping")
                robot.stop()
                continue

            dets = detector.detect(frame)
            seen = describe_detections(dets)
            change = vision.frame_change(prev_frame, frame) if prev_frame is not None else None
            stuck = ""
            if change is not None and last_action not in (None, "stop") and change < 0.04:
                stuck = f" (after '{last_action}' the view barely changed — you may be blocked)"
            print(f"[{step}] 👁  {seen}{stuck}")
            if monitor:
                monitor.update(frame, {"step": step, "seen": seen}, detections=dets)

            prompt = (f"GOAL: {goal}\nYou see: {seen}{stuck}\n"
                      f"Recent moves: {list(recent)}\nChoose ONE JSON action.")
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": prompt}]
            reply = llm.chat(messages)
            action = agent.parse_action(reply)
            if not action:
                print(f"[{step}] (no action) {reply[:70]!r}")
                prev_frame = frame
                continue

            spoken = agent.spoken_text(reply, action)
            if spoken:
                print(f"      🗣 {spoken}")
                if voice:
                    voice.say(spoken)

            name = action.get("action") or action.get("tool") or action.get("motion")
            if monitor:
                monitor.update(frame, {"step": step, "seen": seen,
                                       "action": name, "say": spoken or ""},
                               detections=dets)
            if name == "stop":
                print(f"[{step}] arrived / stopping")
                robot.stop()
                prev_frame = frame
                break

            action = clamp_duration(action)
            result = agent.dispatch_action(robot, action)
            motion = result.get("motion", name)
            if result.get("ok"):
                recent.append(motion)
                last_action = motion
                print(f"      → {motion} for {action['duration_s']}s  ✓")
            else:
                print(f"      → {result}")
            prev_frame = frame
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        robot.stop()
        if hasattr(robot, "close"):
            robot.close()
        detector.close()


def main():
    ap = argparse.ArgumentParser(description="Object-seeking see-think-act loop.")
    ap.add_argument("goal", nargs="*")
    ap.add_argument("--backend", default=None,
                    help=f"LLM backend: {', '.join(sorted(BACKENDS))} (default: env "
                         "LLM_BACKEND or 'ollama')")
    ap.add_argument("--persona", default="explorer")
    ap.add_argument("--steps", type=int, default=STEPS)
    ap.add_argument("--voice", action="store_true")
    ap.add_argument("--voice-port", type=int, default=8090)
    ap.add_argument("--monitor", action="store_true",
                    help="live robot's-eye view in the browser")
    ap.add_argument("--monitor-port", type=int, default=8092)
    args = ap.parse_args()
    goal = " ".join(args.goal) or "explore and tell me what objects you see"

    voice = None
    if args.voice:
        voice = VoiceServer(port=args.voice_port)
        voice.start()
        print(f"::: open http://<pi-ip>:{args.voice_port} on the phone, tap Enable voice")

    monitor = None
    if args.monitor:
        monitor = MonitorServer(port=args.monitor_port)
        monitor.start()
        print(f"::: live view: open http://<pi-ip>:{args.monitor_port} in any browser")
    run(goal, args.persona, args.steps, args.backend, voice, monitor)


if __name__ == "__main__":
    main()
