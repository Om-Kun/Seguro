"""
Seguro Control Center: a clean single-window GUI.
    python seguro/ui/app.py

Qt runs on the main thread; the agent runs on its own asyncio thread. They only talk through the
event bus (agent -> GUI) and a command call (GUI -> agent), so the GUI can never block the agent.
Approvals (purchases, settings, overwrites...) appear as an in-window dialog with a countdown.
"""
import asyncio
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout, QWidget)

# ---- design tokens (single source of truth) ---------------------------------------------------
T = {
    "bg": "#0f1117", "panel": "#171a23", "panel2": "#1e2230", "border": "#2a2f40", "text": "#e6e8ef",
    "muted": "#8a90a6", "accent": "#7c6cff", "accent2": "#22d3ee", "ok": "#22c55e", "warn": "#f59e0b",
    "err": "#ef4444", "radius": 12, "pad": 16, "font": "Segoe UI Variable", "mono": "Cascadia Mono",
}
RUNG_COLORS = {"SKILL": T["accent"], "SHELL": "#38bdf8", "API": T["accent2"], "UIA": "#a3e635",
               "VISION": "#f472b6", "DELEGATE": T["warn"], "CLOUD": T["warn"], "CORE": T["muted"]}

def rgba(hex_color: str, alpha: float) -> str:
    """Qt reads 8-digit hex as #AARRGGBB, so tints are written as rgba() instead."""
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


STYLE = f"""
QWidget {{ background: {T['bg']}; color: {T['text']}; font-family: '{T['font']}'; font-size: 14px; }}
QLabel {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {T['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {T['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {T['panel2']}; color: {T['text']}; border: 1px solid {T['border']}; padding: 6px; }}
QFrame#panel {{ background: {T['panel']}; border: 1px solid {T['border']}; border-radius: {T['radius']}px; }}
QLabel#title {{ font-size: 20px; font-weight: 600; }}
QLabel#muted {{ color: {T['muted']}; font-size: 12px; }}
QLineEdit {{ background: {T['panel2']}; border: 1px solid {T['border']}; border-radius: 10px; padding: 12px 14px; font-size: 14px; }}
QLineEdit:focus {{ border: 1px solid {T['accent']}; }}
QPushButton {{ background: {T['accent']}; color: white; border: none; border-radius: 10px; padding: 11px 20px; font-weight: 600; }}
QPushButton:hover {{ background: #8d7fff; }}
QPushButton:disabled {{ background: {T['border']}; color: {T['muted']}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {T['border']}; color: {T['text']}; }}
QPushButton#mic {{ background: {T['panel2']}; border: 1px solid {T['border']}; color: {T['text']}; padding: 11px 14px; }}
QPushButton#mic:hover {{ border: 1px solid {T['accent']}; }}
QPushButton#micOn {{ background: {T['err']}; color: white; padding: 11px 14px; }}
QProgressBar {{ background: {T['panel2']}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {T['accent2']}; border-radius: 3px; }}
QPushButton#deny {{ background: {T['panel2']}; border: 1px solid {T['border']}; color: {T['text']}; }}
QComboBox {{ background: {T['panel2']}; border: 1px solid {T['border']}; border-radius: 10px; padding: 10px 12px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 7px 10px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {T['panel2']}; }}
QListWidget::item:selected {{ background: {T['panel2']}; color: {T['text']}; }}
"""


# ---- agent thread -----------------------------------------------------------------------------
class VoiceInput(QObject):
    """Mic -> faster-whisper (local). Recording and transcription run off the Qt thread."""
    transcribed = Signal(str, str)   # text, error

    def __init__(self):
        super().__init__()
        from seguro.voice.stt import transcriber
        self.stt = transcriber

    def start(self) -> str:
        self.stt.start_recording()
        if not self.stt._recording:
            return "Microphone could not be opened (check Windows privacy settings or the default input device)."
        return ""

    def level(self) -> float:
        frames = self.stt._audio_frames
        if not frames:
            return 0.0
        import numpy as np
        return float(min(1.0, np.sqrt(np.mean(frames[-1] ** 2)) * 12))

    def stop(self):
        def work():
            seconds = sum(len(f) for f in self.stt._audio_frames) / 16000
            try:
                text = self.stt.stop_and_transcribe()
            except Exception as e:
                self.transcribed.emit("", f"{type(e).__name__}: {e}")
                return
            if text:
                self.transcribed.emit(text, "")
            else:
                self.transcribed.emit("", f"No speech recognised in {seconds:.1f}s of audio.")
        threading.Thread(target=work, daemon=True, name="seguro-stt").start()


class AgentBridge(QObject):
    event = Signal(dict)                  # bus events + task start/finish, delivered on the Qt thread
    approval_needed = Signal(str, object)  # reason, concurrent.futures.Future

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="seguro-agent").start()
        from seguro.core.events import bus
        bus.subscribe(lambda ev: self.event.emit({"type": ev.type, "data": ev.data, "node": ev.node_id}))
        from seguro.core.workflow.gates import broker
        broker.configure(responder=self._ask_gui, timeout_s=90)

    async def _ask_gui(self, reason, details):
        import concurrent.futures
        fut = concurrent.futures.Future()
        self.approval_needed.emit(reason, fut)
        return await asyncio.wrap_future(fut)

    def run(self, prompt: str, mode: str):
        asyncio.run_coroutine_threadsafe(self._run(prompt, mode), self.loop)

    async def _run(self, prompt, mode):
        from seguro.core.orchestrator import orchestrator
        from seguro.core.workflow.planner import is_complex
        from seguro.skills.library.demo_skills import match_skill
        from seguro.safety.killswitch import killswitch
        killswitch.reset()
        t0 = time.time()
        self.event.emit({"type": "task_started", "data": {"prompt": prompt, "mode": mode}})
        try:
            if match_skill(prompt) or not is_complex(prompt):
                st = await orchestrator.run_task(prompt, mode=mode)
                ok, text = st.status == "success", (st.final_evidence or st.error_reason)
            else:
                from seguro.core.workflow.runner import run_workflow
                run = await run_workflow(prompt, mode=mode)
                ok, text = run.status == "done", run.report
        except Exception as e:
            ok, text = False, f"{type(e).__name__}: {e}"
        self.event.emit({"type": "task_finished", "data": {"ok": ok, "text": text, "seconds": round(time.time() - t0, 1)}})


# ---- widgets ----------------------------------------------------------------------------------
def panel():
    f = QFrame()
    f.setObjectName("panel")
    return f


def badge(text, color):
    b = QLabel(text)
    b.setStyleSheet(f"background: {rgba(color, 0.14)}; color: {color}; border: 1px solid {rgba(color, 0.45)}; border-radius: 6px;"
                    f"padding: 2px 8px; font-size: 11px; font-weight: 600;")
    return b


class ApprovalDialog(QDialog):
    def __init__(self, parent, reason: str, timeout_s: int = 90):
        super().__init__(parent)
        self.setWindowTitle("Seguro - approval needed")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.remaining = timeout_s
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(14)
        head = QLabel("Approve this action?")
        head.setObjectName("title")
        body = QLabel(reason)
        body.setWordWrap(True)
        body.setStyleSheet(f"background: {T['panel2']}; border: 1px solid {rgba(T['warn'], 0.55)}; border-radius: 10px; padding: 14px;")
        self.timer_lbl = QLabel()
        self.timer_lbl.setObjectName("muted")
        row = QHBoxLayout()
        deny, ok = QPushButton("Deny"), QPushButton("Approve")
        deny.setObjectName("deny")
        deny.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(self.timer_lbl)
        row.addStretch()
        row.addWidget(deny)
        row.addWidget(ok)
        for w in (head, body):
            lay.addWidget(w)
        lay.addLayout(row)
        deny.setFocus()  # Enter never approves by accident
        self._tick()
        self.t = QTimer(self, interval=1000, timeout=self._tick)
        self.t.start()

    def _tick(self):
        self.timer_lbl.setText(f"Auto-deny in {self.remaining}s")
        if self.remaining <= 0:
            self.reject()
        self.remaining -= 1


class ControlCenter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Seguro")
        self.resize(1180, 740)
        self.bridge = AgentBridge()
        self.bridge.event.connect(self.on_event)
        self.bridge.approval_needed.connect(self.on_approval)
        self.busy = False
        self.t0 = 0.0

        root = QHBoxLayout(self)
        root.setContentsMargins(T["pad"], T["pad"], T["pad"], T["pad"])
        root.setSpacing(T["pad"])

        # left: demo prompts
        from seguro.demo.setup_demo import PROMPTS
        side = panel()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 16, 14, 14)
        logo = QLabel("Seguro")
        logo.setObjectName("title")
        logo.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {T['text']};")
        sub = QLabel("Verified desktop agent")
        sub.setObjectName("muted")
        hint = QLabel(f"{len(PROMPTS)} THINGS SEGURO CAN DO")
        hint.setObjectName("muted")
        hint.setStyleSheet(f"color: {T['muted']}; font-size: 11px; letter-spacing: 1px; margin-top: 12px;")
        self.prompts = QListWidget()
        self.prompts.setWordWrap(True)
        self.prompts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from seguro.demo.setup_demo import PROMPTS
        last_cat = None
        for category, title, prompt, needs in PROMPTS:
            if category != last_cat:
                head = QListWidgetItem(category.upper())
                head.setFlags(Qt.NoItemFlags)
                head.setForeground(QColor(T["accent2"]))
                f = head.font(); f.setBold(True); f.setPointSize(8); head.setFont(f)
                self.prompts.addItem(head)
                last_cat = category
            it = QListWidgetItem("   " + title)
            it.setData(Qt.UserRole, prompt)
            it.setToolTip(prompt + (f"\n\nNeeds: {needs}" if needs else ""))
            self.prompts.addItem(it)
        self.prompts.itemClicked.connect(lambda it: it.data(Qt.UserRole) and self.input.setText(it.data(Qt.UserRole)))
        self.prompts.itemDoubleClicked.connect(
            lambda it: it.data(Qt.UserRole) and (self.input.setText(it.data(Qt.UserRole)), self.submit()))
        for w in (logo, sub, hint, self.prompts):
            sl.addWidget(w)
        side.setFixedWidth(320)
        root.addWidget(side)

        # right: command bar, status, activity
        main = QVBoxLayout()
        main.setSpacing(T["pad"])
        bar = panel()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 14, 14, 14)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Tell Seguro what to do…  (click a demo prompt on the left)")
        self.input.returnPressed.connect(self.submit)
        self.mode = QComboBox()
        self.mode.addItems(["Local only", "Hybrid"])
        self.mode.setToolTip("Hybrid lets failed workflow steps use a cloud model: prompts and tool results leave this PC.")
        self.mode.currentIndexChanged.connect(self.on_mode)
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self.submit)
        self.voice = VoiceInput()
        self.voice.transcribed.connect(self.on_transcribed)
        self.mic_btn = QPushButton("🎤")
        self.mic_btn.setObjectName("mic")
        self.mic_btn.setToolTip("Click to speak, click again to stop. The text goes into the box; press Run to execute.")
        self.mic_btn.clicked.connect(self.toggle_mic)
        self.listening = False
        bl.addWidget(self.mic_btn)
        bl.addWidget(self.input, 1)
        bl.addWidget(self.mode)
        bl.addWidget(self.run_btn)
        main.addWidget(bar)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.hide()
        main.addWidget(self.level)

        self.mode_warn = QLabel("Hybrid: failed steps may be sent to a cloud model (data leaves this PC).")
        self.mode_warn.setStyleSheet(f"color: {T['warn']}; font-size: 12px;")
        self.mode_warn.hide()
        main.addWidget(self.mode_warn)

        stat = panel()
        stl = QVBoxLayout(stat)
        stl.setContentsMargins(18, 14, 18, 14)
        top = QHBoxLayout()
        self.state_badge = badge("IDLE", T["muted"])
        self.timer = QLabel("")
        self.timer.setObjectName("muted")
        top.addWidget(self.state_badge)
        top.addStretch()
        top.addWidget(self.timer)
        self.result = QLabel("Ready. Every result is checked by code before Seguro calls it done.")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stl.addLayout(top)
        stl.addWidget(self.result)
        main.addWidget(stat)

        act = panel()
        al = QVBoxLayout(act)
        al.setContentsMargins(14, 12, 14, 12)
        ah = QLabel("ACTIVITY")
        ah.setStyleSheet(f"color: {T['muted']}; font-size: 11px; letter-spacing: 1px;")
        self.activity = QListWidget()
        self.activity.setWordWrap(True)
        self.activity.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.activity.setStyleSheet(f"font-family: '{T['mono']}'; font-size: 12px;")
        al.addWidget(ah)
        al.addWidget(self.activity, 1)
        main.addWidget(act, 1)
        root.addLayout(main, 1)

        self.clock = QTimer(self, interval=200, timeout=self._tick)
        self.clock.start()

    # ---- behaviour --------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        exclude_from_capture(int(self.winId()))

    def on_mode(self, i):
        self.mode_warn.setVisible(i == 1)

    def submit(self):
        text = self.input.text().strip()
        if not text or self.busy:
            return
        # Busy from the click, not from the agent's first event: the first run imports the agent
        # (several seconds) and a second click meanwhile would start a parallel task.
        self.busy, self.t0 = True, time.time()
        self.run_btn.setEnabled(False)
        self._set_state("STARTING", T["accent"])
        self.bridge.run(text, "hybrid" if self.mode.currentIndex() == 1 else "local_only")

    def _tick(self):
        if self.busy:
            self.timer.setText(f"{time.time() - self.t0:.1f}s")
        if self.listening:
            self.level.setValue(int(self.voice.level() * 100))

    def _mic_style(self, name, text):
        self.mic_btn.setObjectName(name)
        self.mic_btn.setText(text)
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)

    def toggle_mic(self):
        if not self.listening:
            err = self.voice.start()
            if err:
                self._line("🎤 " + err, T["err"])
                return
            self.listening = True
            self._mic_style("micOn", "■ Stop")
            self.level.show()
            self.input.setPlaceholderText("Listening… click Stop when you're done")
            self._line("🎤 listening…", T["accent2"])
        else:
            self.listening = False
            self.mic_btn.setEnabled(False)
            self._mic_style("mic", "…")
            self.level.hide()
            self._line("🎤 transcribing (local Whisper)…", T["muted"])
            self.voice.stop()

    def on_transcribed(self, text, error):
        self.mic_btn.setEnabled(True)
        self._mic_style("mic", "🎤")
        self.input.setPlaceholderText("Tell Seguro what to do…  (click a demo prompt on the left)")
        if error:
            self._line("🎤 " + error, T["warn"])
            return
        self.input.setText(text)
        self.input.setFocus()
        self._line(f"🎤 heard: \"{text}\"  (review, then press Run)", T["text"])

    def _set_state(self, text, color):
        self.state_badge.setText(text)
        self.state_badge.setStyleSheet(badge(text, color).styleSheet())

    def _line(self, text, color=None, rung=None):
        it = QListWidgetItem((f"[{rung}] " if rung else "") + text)
        if color:
            it.setForeground(QColor(color))
        self.activity.addItem(it)
        self.activity.scrollToBottom()

    def on_event(self, ev):
        t, d = ev["type"], ev.get("data", {})
        if t == "task_started":
            self.busy, self.t0 = True, time.time()
            self.run_btn.setEnabled(False)
            self._set_state("RUNNING", T["accent"])
            self.result.setText(d["prompt"])
            self._line(f"▶ {d['prompt']}  ({d['mode']})", T["text"])
        elif t == "task_finished":
            self.busy = False
            self.run_btn.setEnabled(True)
            self._set_state("VERIFIED" if d["ok"] else "NOT DONE", T["ok"] if d["ok"] else T["err"])
            self.timer.setText(f"{d['seconds']}s")
            self.result.setText(d["text"])
            self._line(("✔ verified" if d["ok"] else "✖ not done") + f" in {d['seconds']}s", T["ok"] if d["ok"] else T["err"])
        elif t == "tool_call":
            from seguro.core.rungs import determine_rung
            rung = "SKILL" if d.get("tool", "").startswith("skill_") else determine_rung(d.get("tool", ""))
            mark = "✔" if d.get("verified") else ("·" if d.get("ok") else "✖")
            col = T["ok"] if d.get("verified") else (T["muted"] if d.get("ok") else T["err"])
            ms = f" {d['elapsed_ms']:.0f}ms" if isinstance(d.get("elapsed_ms"), (int, float)) else ""
            self._line(f"{mark} {d.get('tool')}{ms}  {str(d.get('evidence') or '')[:140]}", col, rung)
        elif t == "plan_ready":
            nodes = d.get("graph", {}).get("nodes", [])
            self._line(f"plan: {len(nodes)} steps → " + " → ".join(n["id"] for n in nodes), T["accent2"])
        elif t in ("node_started", "node_verified", "node_failed"):
            col = {"node_started": T["muted"], "node_verified": T["ok"], "node_failed": T["err"]}[t]
            self._line(f"{t.replace('node_', 'step ')}: {ev.get('node')}", col)
        elif t == "needs_confirmation":
            self._line("⚠ waiting for your approval", T["warn"])
        elif t == "confirmation_resolved":
            self._line(f"approval: {'approved' if d.get('approved') else 'denied'} ({d.get('how')})",
                       T["ok"] if d.get("approved") else T["warn"])

    def on_approval(self, reason, fut):
        dlg = ApprovalDialog(self, reason)
        exclude_from_capture(int(dlg.winId()))
        approved = dlg.exec() == QDialog.Accepted
        if not fut.done():
            fut.set_result(approved)


def exclude_from_capture(hwnd: int):
    """Seguro must never see its own windows: hide them from screen capture (mss/BitBlt) on Win10 2004+."""
    try:
        WDA_EXCLUDEFROMCAPTURE = 0x11
        from seguro.llm.client import load_config
        if load_config().get("ui", {}).get("exclude_from_capture", True):
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass  # already set by another import
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont(T["font"], 10))
    w = ControlCenter()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
