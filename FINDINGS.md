# Gemini Flash Agent Loop – Investigation Findings

> **Status**: OUTDATED — This document described the old 3-template whitelist system.
> The codebase now uses **free-rein CadQuery codegen** where Gemini generates arbitrary
> Python scripts for any shape the user requests.

---

## Current Architecture (Post-Refactor)

| Aspect | Details |
|--------|---------|
| **File** | `backend/ai/intent.py` |
| **Mode** | Free-rein CadQuery Python codegen |
| **Model** | Configurable via `GEMINI_MODEL` env var; default `gemini-2.0-flash` |
| **Fallback** | Gemini → OpenAI → clarify |

### How It Works Now

1. User speaks any shape request ("build me a star", "make a gear", "create a vase")
2. Gemini receives the utterance and `CODEGEN_SYSTEM_PROMPT` with CadQuery examples
3. LLM generates Python code: `import cadquery as cq; result = ...`
4. Backend executes the script in a sandbox and exports GLB

### Key Changes from Old System

- **No template whitelist** — LLM can generate code for any shape
- **No template Literal** — `Intent.script` holds raw Python, not a template name
- **STT fixes simplified** — removed shape-biased corrections (bring→ring, etc.)
- **CAD_KEYTERMS neutralized** — no longer biases STT toward ring/box/cylinder

### Remaining Fast Paths

- **Color changes**: Named colors (`yellow`, `navy`, `gold`, …) map to hex and
  `action="set_material"`. Saying "color"/"paint" with no name returns `clarify`.
- **Scale / geometry follow-ups**: Gemini edits `current_script` using `last_summary`.
  There is no regex that multiplies every number in the script.

---

## Historical Context

The original system hard-coded three templates (ring, box, cylinder) at four layers:
prompt, Pydantic Literal, builder.DEFAULTS, and CAD logic branches. This was replaced
with free-rein codegen to enable arbitrary shapes.

---

*Document retained for historical reference. See `backend/ai/intent.py` for current implementation.*
