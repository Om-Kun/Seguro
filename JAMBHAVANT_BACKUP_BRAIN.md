# JAMBHAVANT / SEGURO - AGENT KNOWLEDGE BACKUP

This is an offline backup of the Antigravity AI context for the Jambhavant Capstone Project. 

## 1. Core Architecture
You have built a 3-tier Generative OS Agent system:
1. **The Fast Router:** Intercepts commands and routes them instantly for zero-latency execution.
2. **The V1 Memory Agent:** Hardcoded macro-execution for high-reliability, static tasks.
3. **The V2 Vision Agent (Qwen2-VL + OmniParser):** A zero-shot pipeline that visually parses the screen using Set-of-Mark to control the computer without APIs.

## 2. The Data Engine
To solve the slow speed of the Zero-Shot V2 agent, we built a proprietary data engine:
* **scripts/record_dataset.py:** Uses native Windows ctypes (GetForegroundWindow) to automatically capture screenshots and record physical mouse clicks.
* **scripts/format_dataset.py:** Converts raw pixels into a normalized [0, 1000] relative coordinate grid. CRITICAL MOAT: This solves the Windows 150% DPI display scaling bug.

## 3. Local Fine-Tuning (The LoRA Pipeline)
We trained a 7-Billion parameter Vision Model completely locally on a 12GB RTX 3060 using Unsloth.
* **Script:** scripts/train_lora.py
* **Parameters to prevent OOM:** load_in_4bit=True, batch_size=1, gradient_accumulation=4.
* **The Fix for Windows:** We explicitly disabled PyTorch Triton (torch._dynamo.config.disable = True) to prevent the Windows compiler from crashing.
* **Output:** The successful weights are saved in models/custom_os_lora.

## 4. Testing & Deployment
* **To test the model:** Run python scripts/test_lora.py with a screenshot named test.png in the root folder.

## 5. The Pitch Defenses
* **Why not use OpenAI/Claude?** Privacy, Latency, and Cost. Hospitals/Finance cannot send HIPAA screens to the cloud.
* **Why not just stack Llama on UiPath?** Legacy RPA uses APIs and DOM-scraping, which breaks on legacy or Citrix apps. Ours is pure vision.
* **How did you fit this on a laptop?** We heavily optimized using 4-bit quantization and LoRA adapters, freezing the massive vision encoder to fit everything inside 12GB of consumer VRAM.
