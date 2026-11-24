"""
Webcam perception and portraits. All local: OpenCV capture, YOLOv8 detection, Ollama vision model.
Photos are saved under ~/Desktop/seguro_webcam so the user can see exactly what was captured.
"""
import base64
import os
import time

from seguro.tools.registry import registry

OUT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "seguro_webcam")
YOLO_WEIGHTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "models", "yolo", "yolov8n.pt")
os.environ.setdefault("YOLO_AUTOINSTALL", "False")
_YOLO = None


def _stamp(prefix: str, ext: str = ".png") -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{int((time.time() % 1) * 1000):03d}{ext}")


def _resolve_image(image_path: str = "") -> str:
    from seguro.tools.files import normalize_user_path
    if image_path:
        p = normalize_user_path(image_path)
        if not os.path.isfile(p):
            raise FileNotFoundError(f"Image '{p}' does not exist.")
        return p
    return webcam_capture()["path"]


def _capture_camo_studio():
    try:
        from seguro.tools.windows import ensure_default_desktop, windows_list
        import win32gui, mss, numpy as np
        ensure_default_desktop()
        camo = next((w for w in windows_list() if "camo" in w["title"].lower()), None)
        if not camo:
            return None
        hwnd = camo["hwnd"]
        try:
            import win32con
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.5)
        except Exception:
            pass
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if r <= l or b <= t:
            return None
        with mss.mss() as sct:
            shot = sct.grab({"left": l, "top": t, "width": r - l, "height": b - t})
            img = np.array(shot)[:, :, :3]
        h, w = img.shape[:2]
        cy1, cy2 = int(h * 0.30), int(h * 0.76)
        cx1, cx2 = int(w * 0.26), int(w * 0.70)
        crop = img[cy1:cy2, cx1:cx2]
        if crop.shape[0] > 100 and crop.shape[1] > 100:
            return crop
    except Exception:
        pass
    return None


@registry.register(
    name="webcam_capture",
    description="Takes a photo with the webcam and saves it as a PNG (returns the path).",
    parameters={"type": "object", "properties": {
        "camera": {"type": "integer", "description": "Camera index (default 0)"}}}
)
def webcam_capture(camera: int = 0) -> dict:
    import cv2
    from seguro.llm.client import load_config
    if not load_config().get("privacy", {}).get("webcam_enabled", True):
        return {"ok": False, "error": "Webcam use is disabled in config (privacy.webcam_enabled)."}

    # 1. Camo Studio live preview integration
    camo_frame = _capture_camo_studio()
    if camo_frame is not None and camo_frame.mean() > 5:
        path = _stamp("webcam")
        cv2.imwrite(path, camo_frame)
        h, w = camo_frame.shape[:2]
        return {"ok": True, "path": path, "width": w, "height": h}

    cap = None
    for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY):
        c = cv2.VideoCapture(int(camera), backend)
        if c.isOpened():
            cap = c
            break
        c.release()
    if cap is None or not cap.isOpened():
        return {"ok": False, "error": f"Camera {camera} could not be opened (in use or no permission)."}
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        frame = None
        for _ in range(15):  # let auto-exposure settle
            ok, f = cap.read()
            if ok:
                frame = f
        if frame is None or frame.mean() < 3:
            return {"ok": False, "error": "Camera returned no image (covered, blocked by privacy shutter, or busy)."}
        path = _stamp("webcam")
        cv2.imwrite(path, frame)
        h, w = frame.shape[:2]
        return {"ok": True, "path": path, "width": w, "height": h}
    finally:
        cap.release()


@registry.register(
    name="webcam_detect_objects",
    description=("Identifies objects (people, phone, cup, laptop, ...) in a webcam photo, or in image_path if given, "
                 "using a local YOLOv8 detector. Returns labels with confidence and an annotated image."),
    parameters={"type": "object", "properties": {
        "image_path": {"type": "string", "description": "Optional image; default = take a new webcam photo"},
        "min_confidence": {"type": "number", "description": "Default 0.35"}}}
)
def webcam_detect_objects(image_path: str = "", min_confidence: float = 0.35) -> dict:
    global _YOLO
    try:
        src = _resolve_image(image_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    res = _yolo().predict(src, conf=float(min_confidence), verbose=False)[0]
    objects = []
    for b in res.boxes:
        x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
        objects.append({"label": res.names[int(b.cls)], "confidence": round(float(b.conf), 2),
                        "box": [x1, y1, x2, y2]})
    annotated = _stamp("objects")
    import cv2
    cv2.imwrite(annotated, res.plot())
    counts = {}
    for o in objects:
        counts[o["label"]] = counts.get(o["label"], 0) + 1
    return {"ok": True, "source": src, "annotated_path": annotated, "path": annotated,
            "count": len(objects), "counts": counts, "objects": objects}


def ask_vision_model(image_path: str, question: str, max_tokens: int = 400) -> str:
    """Synchronous call to the configured local vision model (OpenAI-compatible, image as data URI)."""
    import httpx
    from seguro.llm.client import load_config
    cfg = load_config().get("llm", {}).get("vision", {})
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    body = {"model": cfg.get("model", "qwen2.5vl:7b"), "temperature": 0.1, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]}
    r = httpx.post(cfg.get("base_url", "http://127.0.0.1:11434/v1").rstrip("/") + "/chat/completions",
                   json=body, timeout=float(cfg.get("timeout_s", 180)))
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


@registry.register(
    name="vision_ask",
    description=("Asks the local vision model a question about an image: a webcam photo (source='webcam'), the "
                 "screen (source='screen'), or a file path. E.g. 'describe the person', 'what objects are on the desk?'"),
    parameters={"type": "object", "properties": {
        "question": {"type": "string"},
        "source": {"type": "string", "description": "'webcam', 'screen', or an image file path (default webcam)"}},
        "required": ["question"]}
)
def vision_ask(question: str, source: str = "webcam") -> dict:
    try:
        if source == "screen":
            from seguro.tools.vision import capture_screen_pil
            img = capture_screen_pil()
            path = _stamp("screen", ".jpg")
            img.convert("RGB").save(path, quality=85)
        elif source in ("", "webcam", "camera"):
            cap = webcam_capture()
            if not cap["ok"]:
                return cap
            path = cap["path"]
        else:
            path = _resolve_image(source)
        answer = ask_vision_model(path, question)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not answer:
        return {"ok": False, "error": "Vision model returned an empty answer."}
    return {"ok": True, "image": path, "answer": answer}


def _yolo():
    global _YOLO
    if _YOLO is None:
        os.environ["YOLO_AUTOINSTALL"] = "False"
        from ultralytics import YOLO
        os.makedirs(os.path.dirname(YOLO_WEIGHTS), exist_ok=True)
        _YOLO = YOLO(YOLO_WEIGHTS if os.path.exists(YOLO_WEIGHTS) else "yolov8n.pt")
        if not os.path.exists(YOLO_WEIGHTS) and os.path.exists("yolov8n.pt"):
            os.replace("yolov8n.pt", YOLO_WEIGHTS)
    return _YOLO


def _face_crop(img):
    """Head-and-shoulders crop from the largest detected person (OpenCV 5 dropped Haar cascades)."""
    res = _yolo().predict(img, conf=0.25, classes=[0], verbose=False)[0]  # class 0 = person
    boxes = [b.xyxy[0].tolist() for b in res.boxes]
    if not boxes:
        return None, 0
    x1, y1, x2, y2 = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    w, h = x2 - x1, y2 - y1
    H, W = img.shape[:2]
    head_h = min(h, w * 1.25)  # portrait framing: top of the body box, roughly as tall as wide
    cx1, cy1 = max(0, int(x1 - 0.05 * w)), max(0, int(y1 - 0.05 * head_h))
    cx2, cy2 = min(W, int(x2 + 0.05 * w)), min(H, int(y1 + head_h))
    return img[cy1:cy2, cx1:cx2], len(boxes)


@registry.register(
    name="portrait_create",
    description=("Makes an artistic portrait of the person in front of the webcam (or in image_path): finds the face, "
                 "crops head and shoulders, renders style 'painting', 'sketch' or 'ink'. Returns the PNG path."),
    parameters={"type": "object", "properties": {
        "style": {"type": "string", "enum": ["painting", "sketch", "ink"]},
        "image_path": {"type": "string", "description": "Optional photo; default = take a webcam photo"}}}
)
def portrait_create(style: str = "painting", image_path: str = "") -> dict:
    import cv2
    try:
        src = _resolve_image(image_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    img = cv2.imread(src)
    crop, n_faces = _face_crop(img)
    if crop is None:
        crop, n_faces = img, 1
    crop = cv2.resize(crop, (600, int(600 * crop.shape[0] / crop.shape[1])))
    if style == "sketch":
        gray_sketch, _ = cv2.pencilSketch(crop, sigma_s=25, sigma_r=0.04, shade_factor=0.02)
        gaussian = cv2.GaussianBlur(gray_sketch, (0, 0), 2.0)
        out = cv2.addWeighted(gray_sketch, 1.45, gaussian, -0.45, 0)
    elif style == "ink":
        out = 255 - _ink_edges(crop)
    else:
        smooth = cv2.bilateralFilter(crop, 9, 75, 75)
        out = cv2.stylization(smooth, sigma_s=60, sigma_r=0.45)
    path = _stamp(f"portrait_{style}")
    cv2.imwrite(path, out)
    return {"ok": True, "path": path, "style": style, "source": src, "faces": n_faces}


def _ink_edges(img):
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 50, 50)
    low_th = 25 if gray.mean() < 100 else 40
    high_th = 80 if gray.mean() < 100 else 110
    return cv2.Canny(gray, low_th, high_th)


def _stroke_paths(img, max_strokes: int = 900, min_len: int = 4):
    """Edge contours -> simplified polylines, longest first (the recognisable outline goes first)."""
    import cv2
    edges = _ink_edges(img)
    edges = cv2.dilate(edges, None, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    paths = []
    for c in sorted(contours, key=lambda c: -cv2.arcLength(c, False)):
        if cv2.arcLength(c, False) < min_len:
            break
        approx = cv2.approxPolyDP(c, 1.5, False)
        paths.append([tuple(p[0]) for p in approx])
        if len(paths) >= max_strokes:
            break
    return paths


@registry.register(
    name="portrait_paint_in_paint",
    description=("Physically draws a portrait in Microsoft Paint with the mouse (line art from the face in the webcam "
                 "photo or image_path). Opens Paint, draws stroke by stroke, saves the PNG. F12 stops it."),
    parameters={"type": "object", "properties": {
        "image_path": {"type": "string", "description": "Optional photo or portrait; default = webcam photo"},
        "max_strokes": {"type": "integer", "description": "Detail level (default 600)"}}}
)
def portrait_paint_in_paint(image_path: str = "", max_strokes: int = 600) -> dict:
    import cv2
    import subprocess
    import pyautogui
    import win32gui
    import win32con
    from seguro.safety.killswitch import killswitch
    try:
        src = _resolve_image(image_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    img = cv2.imread(src)
    crop, _ = _face_crop(img)
    crop = crop if crop is not None else img

    from seguro.tools.windows import ensure_default_desktop
    ensure_default_desktop()
    subprocess.Popen('explorer.exe "shell:AppsFolder\\Microsoft.Paint_8wekyb3d8bbwe!App"', shell=True)
    hwnd = None
    for _ in range(40):
        time.sleep(0.25)
        hwnd = next((h for h, t in _windows() if t.endswith("- Paint") or t == "Paint" or "paint" in t.lower()), None)
        if hwnd:
            break
    if not hwnd:
        return {"ok": False, "error": "Paint window did not appear."}
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    time.sleep(0.8)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        try:
            pyautogui.press("alt")
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    canvas = _paint_canvas_rect(hwnd)
    if canvas is None:
        return {"ok": False, "error": "Could not locate Paint's drawing canvas."}
    cl, ct, cr, cb = canvas
    pad = 20
    box_w, box_h = (cr - cl) - 2 * pad, (cb - ct) - 2 * pad
    scale = min(box_w / crop.shape[1], box_h / crop.shape[0])
    work = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
    ox, oy = cl + pad, ct + pad
    paths = _stroke_paths(work, max_strokes=int(max_strokes))

    old_pause = pyautogui.PAUSE
    old_failsafe = pyautogui.FAILSAFE
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False
    drawn = 0
    t0 = time.time()
    try:
        for path in paths:
            if killswitch.is_triggered():
                break
            fg = win32gui.GetForegroundWindow()
            fg_root = win32gui.GetAncestor(fg, win32con.GA_ROOT)
            if fg != hwnd and fg_root != hwnd and "Paint" not in win32gui.GetWindowText(fg):
                return {"ok": False, "error": f"Paint lost focus after {drawn} strokes; stopped drawing.",
                        "strokes": drawn}
            x0, y0 = path[0]
            pyautogui.moveTo(ox + x0, oy + y0)
            pyautogui.mouseDown()
            time.sleep(0.01)
            px, py = x0, y0
            for x, y in path[1:]:
                # Paint only draws along mouse-move events it sees while the button is held:
                # walk each segment in small steps with a tiny pause so none are coalesced away.
                steps = max(1, int(max(abs(x - px), abs(y - py)) / 6))
                for k in range(1, steps + 1):
                    _real_move(ox + px + (x - px) * k / steps, oy + py + (y - py) * k / steps)
                    time.sleep(0.002)
                px, py = x, y
            time.sleep(0.005)
            pyautogui.mouseUp()
            drawn += 1
    finally:
        pyautogui.mouseUp()
        pyautogui.PAUSE = old_pause
        pyautogui.FAILSAFE = old_failsafe
    out = _stamp("paint_portrait")
    _save_canvas(canvas, out)
    return {"ok": True, "path": out, "strokes": drawn, "of_strokes": len(paths), "seconds": round(time.time() - t0, 1),
            "stopped_by_killswitch": killswitch.is_triggered()}


def _real_move(x: float, y: float):
    """A genuine mouse-move input event (SetCursorPos, which pyautogui uses, is not seen as a drag
    by modern apps like Paint). Absolute coordinates are normalised to 0..65535 of the primary screen."""
    import win32api
    import win32con
    sw, sh = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE,
                         int(x * 65535 / (sw - 1)), int(y * 65535 / (sh - 1)), 0, 0)


def _windows():
    import win32gui
    from seguro.tools.windows import ensure_default_desktop
    ensure_default_desktop()
    out = []
    win32gui.EnumWindows(lambda h, _: out.append((h, win32gui.GetWindowText(h))) if win32gui.IsWindowVisible(h) else None, None)
    return out


def _paint_canvas_rect(hwnd):
    """Paint's canvas, found via UI Automation or screenshot thresholding."""
    try:
        import uiautomation as auto
        root = auto.GetRootControl()
        win = next((c for c in root.GetChildren() if c.NativeWindowHandle == hwnd or c.ClassName == "MSPaintApp"), None)
        if win:
            q = [win]
            while q:
                curr = q.pop(0)
                for ch in curr.GetChildren():
                    if ch.AutomationId == "image" or "canvas" in ch.Name.lower():
                        rect = ch.BoundingRectangle
                        if rect and rect.width() > 200 and rect.height() > 150:
                            m = 6
                            return (rect.left + m, rect.top + m, rect.right - m, rect.bottom - m)
                    q.append(ch)
    except Exception:
        pass

    import cv2
    import numpy as np
    import win32gui
    from seguro.tools.vision import capture_screen_pil
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    l, t = max(0, l), max(0, t)
    shot = np.array(capture_screen_pil().crop((l, t, r, b)).convert("L"))
    mask = (shot > 245).astype("uint8") * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        mask = (shot > 200).astype("uint8") * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
    x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
    if w < 300 or h < 200:
        return None
    m = 6  # stay inside the edge (resize handles)
    return (l + x + m, t + y + m, l + x + w - m, t + y + h - m)


def _save_canvas(rect, path):
    from seguro.tools.vision import capture_screen_pil
    l, t, r, b = rect
    capture_screen_pil().crop((l, t, r, b)).save(path)
