import os
import sys
import time
import asyncio
import threading
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add root folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seguro.safety.killswitch import killswitch
from seguro.safety.gate import safety_gate
from seguro.voice.tts import tts
from seguro.voice.stt import transcriber
from seguro.skills.recorder import skill_recorder
from seguro.core.orchestrator import orchestrator
from seguro.ui.hud import hud_controller, get_hud_app, PYSIDE_AVAILABLE

# Ensure skills are registered
import seguro.skills.library.fastapi_generator
import seguro.skills.library.youtube_player
import seguro.skills.library.report_generator
import seguro.skills.library.settings_theme

from seguro.core.rungs import determine_rung
from seguro.core.workflow.planner import is_complex


async def run_workflow_with_telemetry(prompt: str, mode: str, resume_id: str = None):
    from seguro.core.workflow.runner import run_workflow, resume_workflow
    killswitch.reset()
    t0 = time.time()
    run = await (resume_workflow(resume_id, mode) if resume_id else run_workflow(prompt, mode=mode))
    elapsed = time.time() - t0
    icon = "✅" if run.status == "done" else ("⏸️" if run.status == "interrupted" else "❌")
    print(f"\n{icon} [WORKFLOW {run.status.upper()} in {elapsed:.1f}s] id={run.id}\n   {run.report}")
    tts.speak(run.report[:200])
    from_template = str(run.plan_info.get("source", "")).startswith("template:")
    if run.status == "done" and sys.stdin and sys.stdin.isatty() and not from_template:
        await offer_template(run)
    return run


async def offer_template(run):
    from seguro.core.workflow import templates

    def confirm(prop):
        print(f"\n💾 Save this workflow as template '{prop.template_name}'? {prop.description}")
        for v in prop.variables:
            print(f"   {v.name} = {v.value_in_this_run!r}  ({v.description})")
        try:
            return input("   Save? [y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, OSError):
            return False

    path = await templates.propose_and_save(run, confirm)
    if path:
        print(f"   Saved template: {path}")


async def execute_task_with_telemetry(prompt: str, mode: str = "hybrid", force_workflow: bool = False):
    clean_p = prompt.strip()
    if not clean_p:
        return

    # A new command from the user re-arms the agent after an earlier F12 abort.
    killswitch.reset()

    print(f"\n========================================================")
    print(f"🎯 [SEGURO TASK INITIATED]: \"{clean_p}\" (Mode: {mode.upper()})")
    print(f"========================================================")

    # 1. Update HUD
    if PYSIDE_AVAILABLE:
        hud_controller.command_changed.emit(clean_p)
        hud_controller.mode_changed.emit(mode)

    # 2. Speak status non-blocking
    tts.speak(f"Working on {clean_p[:40]}")

    from seguro.skills.library.demo_skills import match_skill
    if not match_skill(clean_p) and (force_workflow or is_complex(clean_p)):
        print("[Router] Multi-step request: planning a workflow.")
        await run_workflow_with_telemetry(clean_p, mode)
        return

    t0 = time.time()
    # 3. Dispatch to orchestrator
    state = await orchestrator.run_task(clean_p, mode=mode)
    elapsed = time.time() - t0

    # 4. Stream steps to HUD
    for s in state.history:
        rung = determine_rung(s.tool)
        v_evidence = getattr(s.verifier_check, "get", lambda k: "")("evidence") if s.verifier_check else ""
        if PYSIDE_AVAILABLE:
            hud_controller.rung_changed.emit(rung)
            hud_controller.step_added.emit(s.step_num, s.tool, "success" if s.result.get("ok", True) else "failed", v_evidence or str(s.result.get("data", "")))

    # 5. Handle completion
    if state.status == "success":
        print(f"\n✅ [TASK COMPLETED IN {elapsed:.2f}s] Evidence: {state.final_evidence}")
        tts.speak(f"Done. {state.final_evidence[:50]}")
        if PYSIDE_AVAILABLE:
            hud_controller.status_changed.emit("success", state.final_evidence)
        # Record successful trajectory as muscle memory candidate
        skill_recorder.record_successful_task(clean_p, state.history, state.final_evidence)
    else:
        print(f"\n❌ [TASK FAILED IN {elapsed:.2f}s] Reason: {state.error_reason}")
        tts.speak(f"Task could not be completed: {state.error_reason[:50]}")
        if PYSIDE_AVAILABLE:
            hud_controller.status_changed.emit("failed", state.error_reason)

def start_voice_listener(loop: asyncio.AbstractEventLoop, mode: str):
    """Hooks F9 push-to-talk in a background thread."""
    try:
        import keyboard
        print("🎤 [Voice Listener Armed] Press and HOLD F9 to speak. Release F9 to execute.")
        
        def on_f9_press(e):
            if e.event_type == keyboard.KEY_DOWN:
                transcriber.start_recording()
            elif e.event_type == keyboard.KEY_UP:
                text = transcriber.stop_and_transcribe()
                if text:
                    asyncio.run_coroutine_threadsafe(
                        execute_task_with_telemetry(text, mode=mode),
                        loop
                    )

        keyboard.hook_key("f9", on_f9_press)
    except Exception as e:
        print(f"[Voice] Hotkey hook warning: {e}")

def main():
    parser = argparse.ArgumentParser(description="Seguro V2 Autonomous Windows Agent")
    parser.add_argument("--mode", choices=["local_only", "hybrid"], default="hybrid", help="Agent execution mode")
    parser.add_argument("--no-hud", action="store_true", help="Run without PySide6 graphical HUD")
    parser.add_argument("--task", type=str, help="Direct one-shot task to execute")
    parser.add_argument("--workflow", action="store_true", help="Force planning --task as a multi-step workflow")
    parser.add_argument("--resume", type=str, metavar="WORKFLOW_ID", help="Resume a checkpointed workflow")
    parser.add_argument("--list-workflows", action="store_true", help="List checkpointed workflows")
    args = parser.parse_args()

    if args.list_workflows:
        from seguro.core.workflow.runner import _settings
        from seguro.core.workflow import checkpoint
        for w in checkpoint.list_workflows(_settings()[2]):
            print(f"{w['id']}  {w['status']:<12} {w['goal']}")
        return

    print("\n========================================================")
    print("      🛡️  SEGURO V2: PRODUCTION DESKTOP AGENT  🛡️      ")
    print(f"      Mode: {args.mode.upper()} | F12: Killswitch | F9: Push-To-Talk")
    print("========================================================\n")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Arm F9 voice listener
    start_voice_listener(loop, args.mode)

    if args.resume:
        loop.run_until_complete(run_workflow_with_telemetry("", args.mode, resume_id=args.resume))
        return

    if args.task:
        # Run one-shot task
        loop.run_until_complete(execute_task_with_telemetry(args.task, mode=args.mode, force_workflow=args.workflow))
        return

    # If HUD enabled and PySide available, run Qt event loop
    if not args.no_hud and PYSIDE_AVAILABLE:
        app, hud = get_hud_app()
        if hud:
            hud.show()
            hud.set_mode(args.mode)
            print("🚀 [HUD Activated] Displaying always-on-top telemetry overlay.")

            # Run asyncio loop in background thread
            t = threading.Thread(target=loop.run_forever, daemon=True)
            t.start()

            sys.exit(app.exec())

    # Fallback to interactive console loop
    print("Entering interactive console mode. Type your task (or press Ctrl+C to exit):")
    try:
        while True:
            cmd = input("\nSeguro > ").strip()
            if cmd:
                loop.run_until_complete(execute_task_with_telemetry(cmd, mode=args.mode))
    except KeyboardInterrupt:
        print("\nExiting Seguro V2.")

if __name__ == "__main__":
    main()
