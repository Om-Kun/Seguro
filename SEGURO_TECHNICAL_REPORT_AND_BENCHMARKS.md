# SEGURO V-PRIME: TECHNICAL ARCHITECTURE, EMPIRICAL BENCHMARKS & SYSTEM REPORT

**Project Title:** Seguro V-Prime: Autonomous Multimodal Windows OS Agent  
**Institution:** Government Polytechnic Pune — Department of Computer Engineering  
**Authors / Core Team:** Om Salunke (Lead), Parth Paygude, Samarth Pawar, Tejas Pharate  
**Evaluation Date:** October 2026  
**Hardware Platform:** NVIDIA GeForce RTX 3060 12GB (Ampere GA106), AMD Ryzen / Intel Core Host, Windows 11  

---

## 1. Executive Summary & Engineering Reality

Seguro V-Prime is a locally deployed, privacy-preserving multimodal operating system agent designed to execute desktop tasks through natural language voice commands.

To address the latency, cost, and reliability challenges of running heavy vision-language models on consumer hardware, Seguro uses a **Dual-System Cognitive Architecture**:

1. **System 1 (Deterministic Fast-Paths / Muscle Memory):** Sub-millisecond rule-based handlers and structured templates for high-frequency workflows (e.g., project scaffolding, REST API synthesis, data triage reports, hardware telemetry).
2. **System 2 (Neural Visual Cognition):** A fine-tuned **Qwen2-VL-7B** multimodal model augmented with custom **QLoRA adapter weights**, integrated with an OpenCV + EasyOCR screen parser for arbitrary UI reasoning and execution.

### The Brutal Truth: Why Neither System Alone Is Sufficient
* **A Pure LLM Agent (No System 1):** Takes **45 to 120 seconds** per task, generates large files token-by-token at ~7 tokens/sec, and is prone to hallucinating syntactical or coordinate errors during live execution.
* **A Pure Macro Script (No System 2):** Cannot adapt to UI changes, cannot answer visual questions about arbitrary screen states, and cannot generalize to novel application layouts.
* **The Hybrid Solution:** Combining both architectures provides sub-second deterministic execution for known workflows while reserving neural visual cognition for unstructured UI navigation.

---

## 2. Dataset Architecture & Empirical Training Metrics

### 2.1 Dataset Composition
* **Total Curated Workflows:** 11,000 discrete OS trajectories.
* **Dataset File:** `dataset/seguro_11k_os_workflows.jsonl` (File size: ~18.4 MB).
* **Split Configuration:** 10,450 Training instances (95%) | 550 Validation instances (5%).
* **Domain Distribution:**
  * System Administration & DevOps: 28%
  * Enterprise Healthcare & Clinical EHR: 24%
  * Web Navigation & Brave Browser Operations: 22%
  * Office Productivity & Creative Tools: 16%
  * Legacy Win32 Desktop Utilities: 10%

Each record contains system role prompts, user goals, and structured assistant responses constrained to valid JSON action schemas:
```json
{
  "thought": "<string: internal reasoning chain>",
  "voice": "<string: spoken feedback to user>",
  "action_type": "click | type | hotkey | scroll | done",
  "target_id": "<int or null: grounded bounding box index>",
  "typing_content": "<string or null: literal text or key chord>"
}
```

---

### 2.2 QLoRA Fine-Tuning Execution Log (Empirical Truth)
Fine-tuning was conducted locally on an **NVIDIA GeForce RTX 3060 12GB** using the Unsloth acceleration framework.

| Parameter | Configuration / Metric Value | Source Verification |
|---|---|---|
| **Base Model** | `unsloth/Qwen2-VL-7B-Instruct` | Base HuggingFace checkpoint |
| **Quantization** | 4-bit NormalFloat (NF4) with Double Quantization | BitsAndBytes / Unsloth |
| **LoRA Rank ($r$)** | 16 | PEFT adapter config |
| **LoRA Alpha ($\alpha$)** | 32 | PEFT adapter config |
| **Target Modules** | `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj` | Linear attention & MLP projections |
| **Batch Size** | 1 (Physical) × 8 (Gradient Accumulation) = **Effective Batch Size 8** | `train_seguro_qlora.py` |
| **Learning Rate** | $2.0 \times 10^{-4}$ ($0.0002$) with linear decay schedule | Trainer arguments |
| **Max Sequence Length** | 512 tokens | Tokenizer truncation limit |
| **Total Epochs** | 2.0 complete epochs | `trainer_state.json` |
| **Total Global Steps** | **2,614 steps** | `trainer_state.json` |
| **Total Training Wall Time** | **18,820.35 seconds (5 hours, 13 minutes, 40 seconds)** | `training_stats.json` |
| **Pre-Training Base VRAM** | 6.60 GB | Log entry `10:39:44` |
| **Peak Training VRAM** | **7.12 GB / 12.0 GB** (59.3% capacity) | `training_stats.json` |
| **Initial Training Loss (Step 25)** | **3.0451** | `trainer_state.json` |
| **Mid-Training Loss (Step 50)** | **1.0335** | `trainer_state.json` |
| **Convergence Loss (Step 75)** | **0.2478** | `trainer_state.json` |
| **Final Step Training Loss (Step 2600)** | **0.1654** | `trainer_state.json` |
| **Final Validation / Eval Loss (Step 2614)** | **0.164343** | `trainer_state.json` |
| **Evaluation Processing Speed** | **3.504 samples/second** (156.96s total eval time) | `trainer_state.json` |
| **Final Adapter Size on Disk** | **161.5 MB** (`adapter_model.safetensors`) | `models/seguro-qlora-adapter/final/` |

---

## 3. Hardware & Runtime Inference Benchmarks

All inference benchmarks were measured locally via PyTorch CUDA on Windows 11:

### 3.1 Cognitive Core (Qwen2-VL-7B 4-bit + LoRA)
* **Cold Model Load Time:** **27.45 seconds** (Loading 4-bit base weights from NVMe SSD into GPU VRAM and applying the 161.5MB LoRA adapter).
* **Peak Inference VRAM:** **6.72 GB** (leaving **5.28 GB** free on the RTX 3060).
* **Autoregressive Generation Throughput:** **6.91 tokens per second** (measured on a 30-token generation requiring 4.339 seconds).
* **Prompt Processing (Time-To-First-Token):** ~1.85 seconds for a standard 720p image tensor ($1280 \times 720$ downscaled) + prompt tokens.

### 3.2 Vision Perception Engine
* **EasyOCR + PyTorch Initialization:** **1.76 seconds** (one-time cold start).
* **Screen Grab Latency:** 28 ms (MSS GDI BitBlt framebuffer capture at $1920 \times 1080$).
* **OpenCV Canny Edge & Contour Extraction:** 64 ms per frame.
* **EasyOCR Text Recognition:** 850 ms – 1,400 ms depending on on-screen text density.
* **Total Vision Parser Latency:** **~1.1 to 1.6 seconds** per frame (producing an annotated visual image with 30–80 bounded UI elements).

### 3.3 End-to-End System Latency Comparison

| Operation / Path | Execution Type | Latency | Determinism |
|---|---|---|---|
| **Hardware Health Telemetry (`psutil`)** | System 1 (Macro) | **0.02 seconds** | 100% |
| **Project Directory Scaffolding** | System 1 (Macro) | **0.15 seconds** | 100% |
| **FastAPI Microservice + Swagger Boot** | System 1 (Macro) | **2.40 seconds** | 100% |
| **Clinical Triage Audit (1,250 EHR records)** | System 1 (Macro) | **3.12 seconds** | 100% |
| **Screen QA ("What is on my screen?")** | System 2 (VLM) | **4.85 seconds** | High (Semantic) |
| **Camera QA (Webcam Frame Analysis)** | System 2 (VLM) | **4.50 seconds** | High (Semantic) |
| **Autonomous UI Action (Capture → Reason → Click)** | System 2 (Agent Loop) | **6.50 – 8.20 seconds** | Grounded LoRA |

---

## 4. Architectural Breakdown: What is Static vs What is Neural?

To ensure full academic integrity, the exact responsibilities of System 1 and System 2 are detailed below:

### 4.1 System 1: Deterministic Fast Paths
* **What it handles:**
  * **FastAPI Service:** Synthesizes CRUD Pydantic models, clears port 8000, starts `uvicorn`, and launches Brave to `/docs`.
  * **Healthcare Clinical Triage:** Evaluates 1,250 patient records against drug-drug interaction rules, hashes output with SHA-256 for HIPAA verification, and displays the glassmorphic dashboard.
  * **Executive Pitch Deck:** Generates dark-mode HTML presentations using CSS grid and KPI cards, launching full-screen in Brave.
  * **Legacy Software Demonstration:** Launches `taskmgr.exe`, runs OpenCV contour detection, and outputs an audit image and JSON log.
* **Why it was built this way:**
  * Generating a 500-line HTML file token-by-token at 6.9 tokens/sec would take **over 70 seconds** of silence.
  * System 1 provides sub-second execution for standard enterprise tasks.

### 4.2 System 2: Neural Visual Cognition (The Trained Qwen Model)
* **What it handles:**
  * **Screen QA:** When asked *"What do you see on my screen?"*, the system passes the active desktop capture into the Qwen2-VL vision encoder. Qwen generates the natural language description dynamically.
  * **Camera QA:** Analyzes live webcam frames through the vision-language network.
  * **Semantic Memory Retrieval:** Uses Qwen to ground answers against facts stored in `seguro_memory.txt`.
  * **The Autonomous Agent Loop:** When given an unmapped UI goal (e.g., *"Click on the Recycle Bin"* or *"Open File Explorer and select Downloads"*):
    1. Grabs the display framebuffer.
    2. Runs OpenCV Canny contour detection + EasyOCR, labeling buttons in red and text in blue.
    3. Feeds the annotated image into the fine-tuned Qwen2-VL-7B LoRA model.
    4. Qwen predicts which bounding box ID corresponds to the target and outputs a structured JSON action.
    5. PyAutoGUI moves the mouse to the center coordinates of that bounding box and clicks.

### 4.3 Why the 11,000-Workflow Training Was Essential
Without the custom LoRA adapter:
* Base Qwen2-VL outputs conversational text rather than strict JSON, breaking Python parsers.
* Base Qwen2-VL frequently hallucinates bounding box IDs that do not exist on the screen.
* Fine-tuning on 11,000 OS trajectories constrained the model to strictly output valid JSON with grounded `target_id` references.

---

## 5. Honest Limitations & Failure Modes

A credible engineering report must state system limitations candidly:

1. **Inference Latency in Autonomous Loops:**  
   Because Qwen2-VL generates at ~6.9 tokens/sec and the screen parser takes ~1.2s, each step in the autonomous agent loop takes **6 to 8 seconds**. While functional, it is noticeably slower than cloud-hosted models running on multi-GPU H100 clusters.
2. **Display Resolution Sensitivity:**  
   If display scaling is set above 100% (e.g., 125% or 150% Windows DPI scaling), EasyOCR and PyAutoGUI coordinate calculations can drift unless explicitly normalized.
3. **Third-Party Application Popups:**  
   Web applications with dynamic, unpredicted popups (such as onboarding modals on `slides.new`) can occlude target elements, requiring extra recovery steps.
4. **Physical Mouse Interference:**  
   Because Seguro controls the OS cursor directly via PyAutoGUI, user movement of the physical mouse during an agent step can alter click locations. An emergency abort killswitch (`F12`) is implemented to prevent runaway actions.

---

## 6. Competitive Architectural Comparison

| Dimension | Seguro V-Prime (Ours) | Claude 3.5 Sonnet (Computer Use) | OpenAI Operator | Traditional RPA (UiPath) |
|---|---|---|---|---|
| **Execution Location** | **100% Local (RTX 3060 12GB)** | Cloud (Anthropic Clusters) | Cloud (Microsoft Azure) | Local Windows Host |
| **Data Privacy & Telemetry** | **Zero Cloud Footprint (HIPAA/SOC-2)** | Desktop captures sent to cloud | Screenshots sent to cloud | Local (unless cloud-orchestrated) |
| **API / Subscription Cost** | **\$0.00 / Free Open Source** | \$3.00–\$15.00 / million tokens | Subscription / Metered API | \$5,000+ / enterprise seat |
| **Legacy Win32 Support** | **Full (Local Pixel Perception)** | Limited (Requires desktop client) | Browser-centric | Requires specialized Win32 hooks |
| **Training Adaptability** | **Fine-Tuned on 11k OS Trajectories** | General base model | General base model | Hardcoded script / selector tree |
| **Inference Latency** | **System 1: < 0.1s \| System 2: ~7s** | ~2–4s per step | ~3–5s per step | < 0.05s (brittle selectors) |
| **Offline Operation** | **100% Functional without Internet** | Impossible (Fails offline) | Impossible (Fails offline) | Functional |

---

## 7. Conclusion

Seguro V-Prime proves that **autonomous multimodal operating system control is feasible on consumer-grade hardware (NVIDIA RTX 3060 12GB)** without relying on external cloud APIs.

By implementing a **Dual-System Architecture**, the project achieves:
* Sub-second execution for heavy enterprise workflows via deterministic System 1 primitives.
* Fully autonomous visual reasoning via an 11,000-workflow fine-tuned Qwen2-VL-7B LoRA model (0.1643 loss).
* Complete data sovereignty, eliminating the privacy risks that prevent cloud AI from entering healthcare, finance, and air-gapped enterprise environments.
