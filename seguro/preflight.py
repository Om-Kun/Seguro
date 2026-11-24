import os
import sys
import time
import urllib.request
import urllib.error
import json
import ctypes
import pyautogui

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add root folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seguro.tools.apps import load_apps_index
from seguro.voice.tts import tts

def run_preflight():
    print("\n========================================================")
    print("      🛡️  SEGURO V2: LIVE DEMO PREFLIGHT CHECKLIST  🛡️    ")
    print("========================================================\n")

    passed = 0
    total = 6

    # 1. Display Resolution & DPI Scaling
    sw, sh = pyautogui.size()
    print(f"[1/6] Display Geometry: {sw}x{sh}", end=" -> ")
    if sw >= 1920 and sh >= 1080:
        print("✅ PASS")
        passed += 1
    else:
        print(f"⚠️ WARN: Display is {sw}x{sh} (recommended 1920x1080)")

    # 2. Brain model actually answers (sends one real request; first call also loads it into VRAM)
    import yaml
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"), encoding="utf-8"))
    brain = cfg["llm"]["brain"]
    print(f"[2/6] Brain model warm ({brain['model']} @ {brain['base_url']})", end=" -> ")
    try:
        body = json.dumps({"model": brain["model"], "max_tokens": 4,
                           "messages": [{"role": "user", "content": "Reply with: ok"}]}).encode("utf-8")
        req = urllib.request.Request(brain["base_url"].rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        reply = json.loads(urllib.request.urlopen(req, timeout=120.0).read().decode("utf-8"))
        text = reply["choices"][0]["message"].get("content", "")
        print(f"✅ PASS (replied {text.strip()[:20]!r} in {time.time() - t0:.2f}s)")
        passed += 1
    except Exception as e:
        print(f"❌ FAIL: model did not answer ({e})")

    # 3. Internet connectivity: informational only; it decides which mode makes sense
    print("[3/6] Internet connectivity", end=" -> ")
    try:
        try:
            urllib.request.urlopen("https://1.1.1.1", timeout=3.0)
        except urllib.error.HTTPError:
            pass  # an HTTP error status still means the server answered
        print("✅ ONLINE (hybrid mode possible)")
        passed += 1
    except Exception as e:
        print(f"⚠️ OFFLINE ({type(e).__name__}): use --mode local_only")

    # 4. Applications Index Freshness
    print("[4/6] Universal Windows StartApps Index", end=" -> ")
    apps = load_apps_index()
    if len(apps) > 10:
        print(f"✅ PASS ({len(apps)} applications indexed)")
        passed += 1
    else:
        print(f"⚠️ WARN: Only {len(apps)} apps in index")

    # 5. Non-blocking Voice TTS Engine
    print("[5/6] Speech Audio Engine (SAPI Voice)", end=" -> ")
    try:
        tts.speak("Preflight test", non_blocking=True)
        print("✅ PASS")
        passed += 1
    except Exception as e:
        print(f"❌ FAIL: {e}")

    # 6. Safety Gate & Allowed Roots
    print("[6/6] Safety Barrier Allowlist Verification", end=" -> ")
    from seguro.safety.gate import safety_gate
    ok, _ = safety_gate.check_path("~/Desktop/test.txt")
    denied, _ = safety_gate.check_path("C:/Windows/System32/cmd.exe")
    if ok and not denied:
        print("✅ PASS (User Profile Allowed | System Dirs Blocked)")
        passed += 1
    else:
        print("❌ FAIL: Safety gate barrier violation")

    print("\n--------------------------------------------------------")
    print(f"Preflight Score: {passed}/{total} Checks Passing")
    if passed == total:
        print("🎉 WORKSTATION READY FOR EXHIBITION DEMO! 🚀")
    print("--------------------------------------------------------\n")

if __name__ == "__main__":
    run_preflight()
