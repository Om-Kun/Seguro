import sys
import time
from typing import Optional

try:
    from PySide6.QtCore import Qt, QTimer, Signal, QObject
    from PySide6.QtGui import QColor, QFont, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QFrame, QScrollArea
    )
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

class HUDController(QObject if PYSIDE_AVAILABLE else object):
    if PYSIDE_AVAILABLE:
        mode_changed = Signal(str)
        rung_changed = Signal(str)
        command_changed = Signal(str)
        step_added = Signal(int, str, str, str)  # step_num, tool, status, evidence
        status_changed = Signal(str, str)        # status, evidence

    def __init__(self):
        if PYSIDE_AVAILABLE:
            super().__init__()

hud_controller = HUDController()

if PYSIDE_AVAILABLE:
    class SeguroHUD(QWidget):
        """
        Frameless, always-on-top, semi-transparent HUD overlay.
        Renders real-time telemetry: transcribed command, active rung, step verification, and timer.
        """
        def __init__(self):
            super().__init__()
            self.start_time = time.time()
            self._init_ui()
            self._connect_signals()

        def _init_ui(self):
            # Window flags: frameless, always-on-top, tool window
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

            # Dimensions & positioning (top-right)
            screen = QApplication.primaryScreen().geometry()
            hud_w, hud_h = 400, 360
            self.setGeometry(screen.width() - hud_w - 20, 30, hud_w, hud_h)

            # Main container with sleek glass styling
            self.container = QFrame(self)
            self.container.setGeometry(0, 0, hud_w, hud_h)
            self.container.setStyleSheet("""
                QFrame {
                    background-color: rgba(15, 23, 42, 0.92);
                    border: 1px solid rgba(56, 189, 248, 0.4);
                    border-radius: 12px;
                }
            """)

            layout = QVBoxLayout(self.container)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(8)

            # Header row: Brand + Badges + Timer
            header_layout = QHBoxLayout()
            self.lbl_title = QLabel("🛡️ SEGURO V2")
            self.lbl_title.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 14px; border: none;")

            self.badge_mode = QLabel("LOCAL-ONLY")
            self.badge_mode.setStyleSheet("""
                background-color: #065f46; color: #34d399; font-size: 10px;
                font-weight: bold; padding: 2px 6px; border-radius: 4px; border: 1px solid #10b981;
            """)

            self.badge_rung = QLabel("RUNG: L0 SKILL")
            self.badge_rung.setStyleSheet("""
                background-color: #1e1b4b; color: #818cf8; font-size: 10px;
                font-weight: bold; padding: 2px 6px; border-radius: 4px; border: 1px solid #6366f1;
            """)

            self.lbl_timer = QLabel("0.0s")
            self.lbl_timer.setStyleSheet("color: #94a3b8; font-size: 12px; border: none;")

            header_layout.addWidget(self.lbl_title)
            header_layout.addStretch()
            header_layout.addWidget(self.badge_mode)
            header_layout.addWidget(self.badge_rung)
            header_layout.addWidget(self.lbl_timer)
            layout.addLayout(header_layout)

            # Transcribed user command
            self.lbl_command = QLabel("Awaiting input (Push F9 to speak)...")
            self.lbl_command.setWordWrap(True)
            self.lbl_command.setStyleSheet("""
                color: #f1f5f9; font-size: 12px; font-weight: bold;
                background-color: rgba(30, 41, 59, 0.6); padding: 6px; border-radius: 6px; border: none;
            """)
            layout.addWidget(self.lbl_command)

            # Steps list
            self.lbl_steps_title = QLabel("EXECUTION LADDER TELEMETRY")
            self.lbl_steps_title.setStyleSheet("color: #64748b; font-size: 10px; font-weight: bold; border: none;")
            layout.addWidget(self.lbl_steps_title)

            self.scroll_area = QScrollArea()
            self.scroll_area.setStyleSheet("background: transparent; border: none;")
            self.scroll_area.setWidgetResizable(True)

            self.steps_widget = QWidget()
            self.steps_widget.setStyleSheet("background: transparent;")
            self.steps_layout = QVBoxLayout(self.steps_widget)
            self.steps_layout.setContentsMargins(0, 0, 0, 0)
            self.steps_layout.setSpacing(4)
            self.steps_layout.addStretch()
            self.scroll_area.setWidget(self.steps_widget)
            layout.addWidget(self.scroll_area)

            # Real-time timer
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._update_timer)
            self.timer.start(100)

        def _connect_signals(self):
            hud_controller.mode_changed.connect(self.set_mode)
            hud_controller.rung_changed.connect(self.set_rung)
            hud_controller.command_changed.connect(self.set_command)
            hud_controller.step_added.connect(self.add_step)
            hud_controller.status_changed.connect(self.set_status)

        def _update_timer(self):
            elapsed = time.time() - self.start_time
            self.lbl_timer.setText(f"{elapsed:.1f}s")

        def reset_timer(self):
            self.start_time = time.time()

        def set_mode(self, mode: str):
            if "hybrid" in mode.lower():
                self.badge_mode.setText("HYBRID")
                self.badge_mode.setStyleSheet("""
                    background-color: #78350f; color: #fbbf24; font-size: 10px;
                    font-weight: bold; padding: 2px 6px; border-radius: 4px; border: 1px solid #f59e0b;
                """)
            else:
                self.badge_mode.setText("LOCAL-ONLY")
                self.badge_mode.setStyleSheet("""
                    background-color: #065f46; color: #34d399; font-size: 10px;
                    font-weight: bold; padding: 2px 6px; border-radius: 4px; border: 1px solid #10b981;
                """)

        def set_rung(self, rung: str):
            self.badge_rung.setText(f"RUNG: {rung.upper()}")

        def set_command(self, cmd: str):
            self.lbl_command.setText(f"🎯 \"{cmd}\"")
            self.reset_timer()

        def add_step(self, step_num: int, tool: str, status: str, evidence: str):
            lbl = QLabel(f"[{step_num}] {tool} → {evidence[:45]}")
            if "fail" in status.lower() or "error" in status.lower():
                color = "#f87171"
                prefix = "✗"
            else:
                color = "#4ade80"
                prefix = "✓"
            lbl.setText(f"{prefix} [{step_num}] {tool}: {evidence[:40]}")
            lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent; border: none;")
            self.steps_layout.insertWidget(self.steps_layout.count() - 1, lbl)

        def set_status(self, status: str, evidence: str):
            color = "#34d399" if status == "success" else "#f87171"
            lbl = QLabel(f"🏁 {status.upper()}: {evidence[:50]}")
            lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 12px; border: none;")
            self.steps_layout.insertWidget(self.steps_layout.count() - 1, lbl)

def get_hud_app():
    if not PYSIDE_AVAILABLE:
        return None, None
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    hud = SeguroHUD()
    return app, hud
