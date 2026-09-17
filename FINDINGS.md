# Gemini Flash Agent Loop – Investigation Findings

> **Scope**: Gemini Flash voice/CAD agent loop, prompts, tooling/schemas, generation quality.  
> **Non-goals**: CadQuery pipeline internals, WebXR/passthrough, wake-word UX.  
> **Investigator**: Cloud Agent | **Date**: 2026-09-17

---

## 1. Where Gemini Flash Is Called

| Aspect | Details |
|--------|---------|
| **File** | `backend/ai/intent.py` |
| **Function** | `_intent_from_gemini()` (lines 279–338) |
| **API surface** | REST `POST` to `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` |
| **Model ID** | Configurable via `GEMINI_MODEL` env var; default `gemini-2.0-flash` (`backend/app/config.py:28`) |
| **Streaming** | **One-shot** (no streaming); 45 s timeout |
| **Auth** | API key passed as `?key=` query param |
| **Fallback chain** | Gemini → OpenAI (`gpt-4o-mini`) → pure rule-based heuristics |

### Request shape (simplified)
```json
{
  "system_instruction": { "parts": [{ "text": SYSTEM_PROMPT }] },
  "contents": [{ "role": "user", "parts": [{ "text": "<JSON with utterance, normalized, current_template, current_params>" }] }],
  "generationConfig": { "temperature": 0, "responseMimeType": "application/json" }
}
```

---

## 2. System / User Prompts

### 2.1 Full System Prompt (lines 17–43)

```text
You convert spoken CAD commands into JSON for a parametric CAD system.
Templates: ring, box, cylinder.
Synonyms: cube/block/square → box; tube/pipe/rod/can → cylinder; donut/band/torus → ring.

Actions:
- create: make a new model (set template + params). If the user names a color, include "color" in params.
- modify: change numeric params of the current model
- set_material: change color only (hex like #FFD700 or name mapped to hex)
- noop: nothing to do
- clarify: need more info

Color map: yellow/gold=#FFD700, red=#E53935, blue=#1E88E5, green=#43A047,
silver/grey/gray=#C0C0C0, black=#212121, white=#FAFAFA, orange=#FB8C00, purple=#8E24AA, pink=#EC407A.

Params (mm):
ring: inner_diameter_mm, outer_diameter_mm, height_mm, color
  - "thicker band" → increase height_mm and outer-inner gap
  - "thinner" → decrease those
  - "small" → scale down sizes ~0.75x from defaults; "large" ~1.4x
box: width_mm, depth_mm, height_mm, color
cylinder: diameter_mm, height_mm, color
"bigger"/"larger" → scale all *_mm by 1.25; "smaller" → 0.8

Fix obvious speech-to-text errors (bring→ring, rink→ring, boxes→box).

Return ONLY JSON:
{"action":"...","template":"ring|box|cylinder|null","params":{},"reply":"short spoken confirmation"}
```

### 2.2 User Message (constructed in code)
```json
{
  "utterance": "<raw STT>",
  "normalized": "<after _STT_FIXES regex>",
  "current_template": "ring" | "box" | "cylinder" | null,
  "current_params": { ... }
}
```

### 2.3 Key Limitations in the Prompt

| Issue | Evidence | Impact |
|-------|----------|--------|
| **Hard-coded 3-template allowlist** | `Templates: ring, box, cylinder.` | LLM cannot suggest/create anything else — even "keychain" is impossible |
| **No few-shot examples** | Prompt has inline hints but zero worked examples | LLM may misinterpret edge cases; no calibration for complex requests |
| **No multi-step / plan** | Actions are atomic; no `"steps": [...]` | Cannot build "ring with a hole and a bail" without user issuing two commands |
| **No boolean ops in prompt** | Only params exposed; no union/cut/extrude vocabulary | LLM can't express "add a loop to the ring" |
| **Color inline, no material system** | `#hex` only; no roughness/metalness | "Make it shiny gold" unrepresentable |

---

## 3. Tooling / Structured Output / Function-Calling

### 3.1 Current Setup
* **No Gemini function-calling** — the code uses plain JSON-mode (`responseMimeType: application/json`) rather than `tools` / `function_declarations`.
* **Pydantic validation only** — `Intent.model_validate(parsed)` after response (no schema sent to Gemini).

### 3.2 Intent Pydantic Model (`backend/app/models.py`)

```python
TemplateName = Literal["ring", "box", "cylinder"]
ActionName   = Literal["create", "modify", "set_material", "noop", "clarify"]

class Intent(BaseModel):
    action: ActionName = "noop"
    template: TemplateName | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    reply: str = "Okay."
```

### 3.3 Validation & Retries
* **Validation**: Pydantic raises if action/template literals are wrong → unhandled exception bubbles up.
* **Retries**: None in the Gemini path. OpenAI path also has no retry. On error, fall back to rules.
* **Clarify fallback**: If LLM returns `action=clarify`, code re-runs `_rule_based_intent()`; if rules succeed, use that instead.

### 3.4 Gaps

| Gap | Why it matters |
|-----|----------------|
| No schema in request | Gemini doesn't know the exact structure; relies on prompt fidelity |
| params is `dict[str, Any]` | Allows hallucinated keys; no enforcement of per-template schema |
| No retry on malformed JSON | Single parse attempt; crash on invalid JSON |
| No function-calling | Could use Gemini's native tool mode for guaranteed schema compliance |

---

## 4. Natural Language → CadQuery Mapping

### 4.1 Flow

```
STT transcript
  ↓ _normalize_transcript() (regex STT fixes)
  ↓ Gemini (or OpenAI/rules) → Intent { action, template, params }
  ↓ apply_intent() → calls cad.builder.build_model(template, params, settings)
  ↓ CadQuery (or trimesh fallback) generates GLB
```

### 4.2 Where the cube/cylinder/ring ceiling is enforced

| Layer | Enforcement |
|-------|-------------|
| **Prompt** | `Templates: ring, box, cylinder.` — LLM won't produce anything else |
| **Pydantic Literal** | `TemplateName = Literal["ring", "box", "cylinder"]` — raises on unknown |
| **builder.DEFAULTS** | Keys = `{"ring", "box", "cylinder"}` — any other template → `ValueError` in `build_model()` |
| **_build_with_cadquery / _build_with_trimesh** | Hard-coded if/elif for three templates |

**Conclusion**: To add a new shape (e.g., "keychain_base"), changes are required in **all four layers**.

### 4.3 No Intermediate Representation (IR)
The system has no CSG tree, no operation log, no "recipe". Each model is regenerated from params; there's no undo, no combine, no subtract.

---

## 5. Edit-by-Voice vs Create Path

| Aspect | Create | Edit (modify / set_material) |
|--------|--------|------------------------------|
| Trigger | `action = "create"` | `action = "modify"` or `"set_material"` |
| Shared LLM? | Yes — same `SYSTEM_PROMPT`, same `parse_intent()` | Yes |
| Params handling | Start from `DEFAULTS[template]` | Merge `session.params` + new params |
| GLB rebuild | Always | modify → always; set_material → only if `rebuild=True` in params (never set currently) |

**Key observation**: Both paths use a single intent loop. No special "edit mode" LLM. Color changes skip CAD rebuild for speed.

---

## 6. Failure Modes

### 6.1 Observed / Potential Failures

| Failure | Current handling | Risk |
|---------|------------------|------|
| **Empty LLM output** | `json.loads("{}")` may succeed → noop intent | Silent no-op |
| **Markdown-wrapped JSON** | Stripped via regex (lines 328-331) | Usually okay |
| **Hallucinated template** | Pydantic rejects → unhandled exception → falls back to rules | UX glitch, recovered |
| **Hallucinated param keys** | Ignored by `merge_params()` — base DEFAULTS used | Param silently lost |
| **Invalid numeric params** | Passed to CadQuery; may cause geometry error | 500 or fallback to trimesh |
| **LLM suggests unsupported op** | e.g., "add a hole" → no action exists | Returns clarify |
| **Rate limit / network** | Exception logged, fallback to rules | Degraded but functional |

### 6.2 No structured error feedback to user
When LLM fails, user sees generic "Try: build me a ring…" — no indication that their creative request was understood but unsupported.

---

## 7. Gaps vs a Stronger "Percy" CAD Assistant

| Capability | Current | Needed for keychains / richer parts |
|------------|---------|-------------------------------------|
| **Template library** | 3 hard-coded | 10-20 parametric primitives + composites |
| **Boolean ops** | None | Union, cut, fillet, chamfer |
| **Multi-step planning** | None | LLM emits a plan; backend executes steps |
| **Parametric constraints** | Implicit (defaults) | Explicit relations ("hole centered on face") |
| **Material / finish** | Hex color only | Roughness, metalness, texture |
| **Dimensions from voice** | Partial ("inner diameter 18mm") | More robust NLU ("2 inch by 3 inch base") |
| **Undo / history** | None | Op log for "undo last step" |
| **Few-shot / RAG** | None | Example library; retrieval of similar past builds |
| **Function-calling** | Unused | Guarantees schema; enables multi-tool calls |

---

## 8. Concrete Limitations — File/Path Pointers

| Limitation | File | Lines | Notes |
|------------|------|-------|-------|
| 3-template hard-code | `backend/ai/intent.py` | 17-18 | Prompt |
| 3-template Literal | `backend/app/models.py` | 10 | Pydantic type |
| 3-template DEFAULTS | `backend/cad/builder.py` | 15-32 | Param defaults |
| 3-template CAD logic | `backend/cad/builder.py` | 63-92, 110-136 | if/elif branches |
| No function-calling | `backend/ai/intent.py` | 299-318 | Plain JSON mode |
| No retry on LLM error | `backend/ai/intent.py` | 387-394 | Falls straight to rules |
| No multi-step action | `backend/app/models.py` | 29-32 | Single action enum |
| params untyped | `backend/app/models.py` | 31 | `dict[str, Any]` |

---

## 9. Fix Options — Ranked by Leverage

### Tier 1: Prompt-only (no code changes)

| Change | Effort | Impact |
|--------|--------|--------|
| Add 3-5 few-shot examples to SYSTEM_PROMPT | Low | Better intent accuracy; fewer clarify loops |
| Expand synonym list in prompt | Low | Handles more STT variants |
| Add "unsupported" action for graceful rejection | Low | Better UX when user asks for unsupported shapes |

### Tier 2: Schema / Tools (small code changes)

| Change | Effort | Impact |
|--------|--------|--------|
| Use Gemini function-calling with typed tool schema | Medium | Guaranteed structure; enables multi-tool calls |
| Strongly-type params per template (discriminated union) | Medium | Rejects hallucinated/invalid params |
| Add retry loop (1-2 retries) on JSON parse failure | Low | Resilience |

### Tier 3: Architecture (larger changes, likely separate PRs)

| Change | Effort | Impact |
|--------|--------|--------|
| Introduce IR (op list) and multi-step planning | High | Unlocks composites, undo, richer parts |
| Expand template library (keychain_base, hook, text, etc.) | Medium | Direct path to keychains |
| Add material system (roughness, metalness) | Medium | "Shiny gold" support |

---

## 10. Recommended Next Slice (Smallest High-Leverage Change)

### **Add few-shot examples + unsupported-action handling**

**Why**:
1. Zero-code prompt change improves intent accuracy immediately.
2. "unsupported" action lets LLM say "I can't make a keychain yet" instead of hallucinating or returning clarify.
3. Validates whether prompt improvements alone move the needle before investing in function-calling or IR.

**Deliverable**:
* Update `SYSTEM_PROMPT` in `backend/ai/intent.py`:
  * Add 4-6 example user→JSON pairs.
  * Add `unsupported` action with guidance: "If user asks for a shape not in {ring, box, cylinder}, return unsupported."
* Add `"unsupported"` to `ActionName` Literal in `backend/app/models.py`.
* Handle `action == "unsupported"` in `apply_intent()` — return a friendly reply without building.

**Estimated scope**: ~50 lines changed; no new files; no CadQuery or WebXR changes.

---

## 11. Explicit Non-Goals / Handoffs

| Area | Owner | Handoff note |
|------|-------|--------------|
| CadQuery geometry bugs / new primitives | CadQuery pipeline team | If we add templates, they implement the CAD logic |
| WebXR passthrough, AR placement | WebXR/wake-word team | No changes needed for prompt/LLM work |
| STT quality (ElevenLabs/Deepgram) | Voice team | Prompt can mitigate via synonyms, but root fix is STT config |
| TTS latency / voice selection | Voice team | Out of scope |

---

## 12. Summary for Umad + PM

**Current state**: Gemini Flash (or OpenAI fallback) converts voice to a single-action Intent with 3 possible templates. The ceiling is enforced redundantly in prompt, Pydantic types, and builder code. No function-calling, no multi-step planning, no boolean ops.

**Quickest win**: Prompt improvements (few-shot, unsupported action) — validates LLM quality hypothesis with minimal risk.

**Next step after prompt**: Switch to Gemini function-calling for schema guarantees, then expand template library in concert with CadQuery team.

**Richer "Percy" vision** (keychains, parametric features) requires an IR/plan architecture — scope that as a separate design spike once prompt + schema improvements land.

---

*End of findings.*
