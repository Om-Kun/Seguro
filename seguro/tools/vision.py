import io
import base64
import ctypes
import concurrent.futures
import mss
from PIL import Image
import pyautogui
from seguro.tools.registry import registry

# Dedicated background thread pool for GDI BitBlt capture
# Isolates GDI BitBlt from COM apartment threading and UIA hooks
_CAPTURE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="seguro_gdi_capture")

def _worker_capture_frame() -> Image.Image:
    """Runs on a clean worker thread isolated from UIA / COM hooks."""
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass

    with mss.MSS() as sct:
        monitor = sct.monitors[1]  # Primary monitor
        sct_img = sct.grab(monitor)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

def capture_screen_pil() -> Image.Image:
    """Fast Windows GDI BitBlt framebuffer capture (~28ms)."""
    future = _CAPTURE_EXECUTOR.submit(_worker_capture_frame)
    return future.result(timeout=5.0)

def image_to_base64(img: Image.Image, max_dim: int = 1280) -> str:
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def scale_coords_vlm_to_screen(x_vlm: float, y_vlm: float, model_dim: tuple, screen_dim: tuple) -> tuple:
    """
    Maps VLM coordinate space to physical desktop coordinates.
    model_dim: (w, h) of image fed to model
    screen_dim: (w, h) of physical monitor
    """
    mw, mh = model_dim
    sw, sh = screen_dim
    if mw <= 0 or mh <= 0:
        return 0, 0
    x_phys = int(round((x_vlm / mw) * sw))
    y_phys = int(round((y_vlm / mh) * sh))
    # Clamp to monitor boundaries
    x_phys = max(0, min(sw - 1, x_phys))
    y_phys = max(0, min(sh - 1, y_phys))
    return x_phys, y_phys

def scale_normalized_to_screen(x_norm: float, y_norm: float, screen_dim: tuple, norm_base: float = 1000.0) -> tuple:
    """
    Maps normalized coordinates (e.g. 0-1000 from Qwen2-VL or Claude CUA) to physical screen coordinates.
    """
    sw, sh = screen_dim
    if norm_base <= 0:
        return 0, 0
    x_phys = int(round((x_norm / norm_base) * sw))
    y_phys = int(round((y_norm / norm_base) * sh))
    x_phys = max(0, min(sw - 1, x_phys))
    y_phys = max(0, min(sh - 1, y_phys))
    return x_phys, y_phys

@registry.register(
    name="look_at_screen",
    description="Captures the active screen and provides visual observation to answer a question.",
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Specific question about what is on screen"}
        },
        "required": ["question"]
    }
)
def look_at_screen(question: str) -> dict:
    # Answered by the local vision model (llm.vision). If it is unavailable, say so honestly.
    from seguro.tools.webcam import vision_ask
    res = vision_ask(question, source="screen")
    if not res.get("ok"):
        res["error"] = f"Screen question could not be answered: {res.get('error')}"
    return res

@registry.register(
    name="ground_and_click",
    description="Finds a visual UI element on screen using VLM pixel grounding and clicks it.",
    parameters={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Visual description of the target element to click"},
            "double": {"type": "boolean", "description": "Set true to double click"},
            "right": {"type": "boolean", "description": "Set true to right click"}
        },
        "required": ["description"]
    }
)
def ground_and_click(description: str, double: bool = False, right: bool = False) -> dict:
    # Visual grounding needs a VLM that returns coordinates; none is wired up yet.
    # Do not click anything rather than guess.
    return {
        "ok": False,
        "error": "Visual grounding is not available: no vision-language grounder is configured. "
                 "Use uia_snapshot + uia_click to target controls by accessibility name.",
        "target": description
    }
