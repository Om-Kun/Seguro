# SEGURO: On-Premise Autonomous Desktop Operating System Agent

SEGURO is a production-grade, privacy-first desktop agent designed to automate complex, multi-application workflows natively on Windows. It combines local Small Language Models (SLMs) with deterministic skill execution, OS-level safety gates, and post-action verification.

---

## Key Highlights

- **Local Neural Execution**: Runs locally via Ollama (Qwen 2.5-Coder-7B) with zero cloud screen streaming.
- **6-Rung Execution Ladder**: Prioritizes deterministic Python skills, shell scripts, and direct Office APIs over visual mouse macros.
- **Action Verification**: Programmatic verification of file generation, data rows, and application states before reporting task completion.
- **Hardware Safety**: Global F12 emergency killswitch (<50ms response) and path-sandboxed execution.
- **Desktop Control Center**: PySide6 dark-mode graphical interface with push-to-talk voice input (Faster-Whisper).

---

## Architecture Overview

1. **FastRouter**: Handles common, unambiguous commands in under 300ms using regex pattern matching.
2. **ReAct Orchestrator**: Handles novel multi-step tasks through dynamic reasoning and structured tool calls.
3. **Safety Gate**: Restricts file modifications to user-approved directories and blocks destructive commands.
4. **Action Verifier**: Confirms on-disk file integrity (ZIP/OOXML validation, file sizes, process states).

---

## Repository Structure

```text
seguro-github/
├── docs/ui/            # Architecture diagrams and UI screenshots
├── models/yolo/        # Local YOLOv8 weights for computer vision
├── ollama/             # Modelfiles for local SLM runtime
├── seguro/             # Core agent source code
│   ├── core/           # Orchestrator, router, verifier, workflow planner
│   ├── safety/         # F12 killswitch and path sandbox
│   ├── skills/         # Verified deterministic skill libraries
│   ├── tools/          # OS, Office, browser, and Win32 automation tools
│   ├── ui/             # PySide6 Control Center and telemetry HUD
│   └── voice/          # Local speech-to-text (STT) and text-to-speech (TTS)
├── tests/              # Automated test suite
├── requirements.txt    # Project dependencies
└── run_seguro.py       # Application entry point
```

---

## Quickstart

### 1. Prerequisites
- Windows 10/11 (64-bit)
- Python 3.10 or 3.11
- Ollama installed and running (`ollama serve`)

### 2. Setup Ollama Model
```bash
ollama create seguro-brain-qwen3 -f ollama/seguro-brain-qwen3.Modelfile
```

### 3. Install Dependencies
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 4. Run Seguro
```bash
# Launch PySide6 Desktop GUI
python run_seguro.py

# Or run directly via CLI
python run_seguro.py --task "Open Google Sheets"
```

---

## Capstone Project Credits

- **Institute**: Government Polytechnic, Pune
- **Department**: Computer Engineering
- **Project Team**:
  - Samarth Pawar (2406151)
  - Parth Paygude (2406155)
  - Tejas Pharate (2406158)
  - Om Salunke (2406176)
- **Mentor**: Smt. V. S. Pawar
- **Head of Department**: Smt. J. R. Hange
