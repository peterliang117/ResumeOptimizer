# Local LLM Setup

This workflow can use a local OpenAI-compatible LLM endpoint for resume/JD
analysis and application-answer drafting. The recommended default is Ollama with
Qwen3 models.

## 1. Install Ollama

Windows:

```powershell
winget install -e --id Ollama.Ollama
```

Or install it from:

```text
https://ollama.com/download
```

After install, confirm the CLI is available:

```powershell
ollama --version
```

## 2. Pull the recommended models

```powershell
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama list
```

The pipeline routes lightweight screening and tracker summaries to `qwen3:8b`
and resume/application work to `qwen3:14b`.

## 3. Enable local LLM routing for this shell

From the repo root:

```powershell
Set-Location C:\path\to\ResumeOptimizer
. .\scripts\use_local_llm.ps1
```

If PowerShell blocks local scripts, enable bypass for the current PowerShell
process only and then dot-source the script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
. .\scripts\use_local_llm.ps1
```

That sets:

```text
LOCAL_LLM_ENABLED=1
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
LOCAL_LLM_SCREENING_MODEL=qwen3:8b
LOCAL_LLM_RESUME_MODEL=qwen3:14b
LOCAL_LLM_APPLICATION_MODEL=qwen3:14b
RESUME_OPTIMIZER_LLM_PROVIDER=local
```

These values apply only to the current PowerShell session. To make them
persistent, use Windows user environment variables instead of storing secrets or
private config in tracked files.

The script also adds `%LOCALAPPDATA%\Programs\Ollama` to the current shell PATH
when `ollama.exe` is installed there. If `ollama` is still not recognized, open
a fresh PowerShell window or call the executable directly from that directory.

## 4. Verify local model availability

```powershell
python scripts\local_llm_status.py --check-server
python scripts\doctor.py --check-local-llm
```

If the server check fails, start Ollama from the Start Menu or run:

```powershell
ollama serve
```

## 5. Run the application packet pipeline with local LLM

```powershell
python scripts\run_application_pipeline.py `
  --company "Example Co" `
  --role "Senior Data Engineer" `
  --job-url "https://example.com/job" `
  --resume resumes\master.docx `
  --llm-provider local
```

The pipeline still stops at the existing review gate after writing
`proposed_edits.json`. It does not submit applications.

## Route Table

| Task | Model env var | Default model | Mode | Temperature |
|---|---|---|---|---|
| `batch_screening` | `LOCAL_LLM_SCREENING_MODEL` | `qwen3:8b` | `/no_think` | `0.2` |
| `resume_tailoring` | `LOCAL_LLM_RESUME_MODEL` | `qwen3:14b` | `/think` | `0.2` |
| `application_answer` | `LOCAL_LLM_APPLICATION_MODEL` | `qwen3:14b` | `/no_think` | `0.3` |
| `tracker_update` | `LOCAL_LLM_SCREENING_MODEL` | `qwen3:8b` | `/no_think` | `0.0` |

Use `--model` for one-off overrides:

```powershell
python scripts\tailor.py `
  --resume resumes\master.docx `
  --job jobs\job.txt `
  --profile profile\facts.md `
  --out outputs\tailored.docx `
  --dry-run `
  --llm-provider local `
  --model qwen3:14b
```

## OpenClaw integration boundary

For OpenClaw or another browser agent, set the model endpoint to:

```text
Base URL: http://127.0.0.1:11434/v1
API key: ollama
Default model: qwen3:14b
```

Keep final submission disabled. Let the repo scripts create application packets,
draft answers, and update local tracker state. Use OpenClaw only for supervised
browser navigation and form filling after required answers are covered by:

```text
profile/facts.md
profile/application_answers.json
```

Legal attestations, sensitive self-ID fields, sponsorship wording, privacy
acknowledgements, and final submit remain manual unless explicitly covered by
those private local files.
