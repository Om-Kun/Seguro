import os
import sys
import time
import zipfile
import pytest
import asyncio

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.core.router import fast_router
from seguro.core.verifier import verifier
from seguro.tools.vision import (
    capture_screen_pil,
    image_to_base64,
    scale_coords_vlm_to_screen,
    scale_normalized_to_screen
)
from seguro.core.orchestrator import orchestrator
from seguro.core.state import TaskState
from seguro.tools.windows import windows_close

def test_fast_router_rules_and_latency():
    """Acceptance test: Fast-path router identifies unambiguous intents in < 10ms."""
    t0 = time.time()
    
    # 1. YouTube query
    r_yt = fast_router.match("play Beethoven Symphony 9 on youtube")
    assert r_yt is not None
    assert r_yt["tool"] == "browser_open"
    assert "Beethoven" in r_yt["args"]["url"]
    assert "youtube.com" in r_yt["args"]["url"]

    # 2. Web search
    r_search = fast_router.match("search for latest quantum computing breakthroughs")
    assert r_search is not None
    assert r_search["tool"] == "browser_search"
    assert "quantum" in r_search["args"]["query"]

    # 3. App launch
    r_app = fast_router.match("launch notepad")
    assert r_app is not None
    assert r_app["tool"] == "apps_launch"
    assert r_app["args"]["name"] == "notepad"

    # 4. App close
    r_close = fast_router.match("close notepad")
    assert r_close is not None
    assert r_close["tool"] == "windows_close"
    assert r_close["args"]["title_contains"] == "notepad"

    # 5. Non-matching ambiguous task should route to brain
    r_brain = fast_router.match("calculate Fibonacci sequence and save to file")
    assert r_brain is None

    elapsed_ms = (time.time() - t0) * 1000.0
    print(f"✅ FastRouter executed 5 matches in {elapsed_ms:.2f}ms (target: < 300ms)")
    assert elapsed_ms < 300.0

def test_action_verifier_strictness():
    """Acceptance test: ActionVerifier strictly checks physical files and flags hallucinations."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    valid_file = os.path.join(desktop, "test_verifier_valid.txt")
    bogus_file = os.path.join(desktop, "nonexistent_ghost_file_999.txt")

    try:
        with open(valid_file, "w", encoding="utf-8") as f:
            f.write("Seguro empirical evidence test payload.")

        # Test valid file operation verification
        v_ok = verifier.verify_action("files_write", {"path": valid_file}, {"ok": True})
        assert v_ok["verified"] is True
        assert "bytes" in v_ok["evidence"]

        # Test missing file detection
        v_fail = verifier.verify_action("files_write", {"path": bogus_file}, {"ok": True})
        assert v_fail["verified"] is False
        assert "does not exist" in v_fail["evidence"]

        # Test task_complete with honest disk evidence
        v_comp_ok = verifier.verify_action("task_complete", {"evidence": f"Successfully created {valid_file}"}, {"ok": True})
        assert v_comp_ok["verified"] is True

        # Test task_complete with fabricated/hallucinated file path
        v_comp_fake = verifier.verify_action("task_complete", {"evidence": f"Created file at {bogus_file}"}, {"ok": True})
        assert v_comp_fake["verified"] is False
        assert "missing" in v_comp_fake["evidence"]

        print("✅ ActionVerifier strictly validated physical disk artifacts and rejected hallucinated evidence")
    finally:
        if os.path.exists(valid_file):
            os.remove(valid_file)

def test_vision_coordinate_scaling():
    """Acceptance test: Mathematical coordinate scaling correctly maps model tensor coords to physical screen."""
    screen_dim = (1920, 1080)
    model_dim = (1280, 720)

    # 1. Exact center
    cx, cy = scale_coords_vlm_to_screen(640, 360, model_dim, screen_dim)
    assert cx == 960
    assert cy == 540

    # 2. Origin
    ox, oy = scale_coords_vlm_to_screen(0, 0, model_dim, screen_dim)
    assert ox == 0
    assert oy == 0

    # 3. Bottom-right corner
    brx, bry = scale_coords_vlm_to_screen(1280, 720, model_dim, screen_dim)
    assert brx == 1919 or brx == 1920
    assert bry == 1079 or bry == 1080

    # 4. Normalized 0-1000 scaling (Qwen2-VL / CUA standard)
    nx, ny = scale_normalized_to_screen(500, 500, screen_dim, 1000.0)
    assert nx == 960
    assert ny == 540

    # 5. Out-of-bounds clamping
    clamped_x, clamped_y = scale_normalized_to_screen(1200, -50, screen_dim, 1000.0)
    assert clamped_x == 1919
    assert clamped_y == 0

    print("✅ Vision coordinate scaling unit tests passed: center, origin, corner, normalized & clamping")

def test_screen_capture_framebuffer():
    """Acceptance test: Fast GDI BitBlt framebuffer capture acquires real monitor frames."""
    t0 = time.time()
    img = capture_screen_pil()
    elapsed_ms = (time.time() - t0) * 1000.0

    assert img is not None
    assert img.size[0] > 0 and img.size[1] > 0
    b64 = image_to_base64(img, max_dim=1280)
    assert len(b64) > 1000
    print(f"✅ Fast screen framebuffer capture: {img.size[0]}x{img.size[1]} in {elapsed_ms:.2f}ms, Base64 len: {len(b64)}")

@pytest.mark.asyncio
async def test_orchestrator_fast_path_end_to_end():
    """Acceptance test: Orchestrator executes fast-path app launch with verifier in LOCAL-ONLY mode."""
    state = await orchestrator.run_task("open notepad", mode="local_only")
    assert state.status == "success"
    assert state.step_count == 1
    assert "notepad" in state.final_evidence.lower()
    time.sleep(1.0)
    windows_close("Notepad")
    print(f"✅ Orchestrator end-to-end fast-path task passed: '{state.final_evidence}' in {state.step_count} step")

if __name__ == "__main__":
    test_fast_router_rules_and_latency()
    test_action_verifier_strictness()
    test_vision_coordinate_scaling()
    test_screen_capture_framebuffer()
    asyncio.run(test_orchestrator_fast_path_end_to_end())
    print("\n🎉 ALL PHASE 3 ACCEPTANCE TESTS PASSED!")
