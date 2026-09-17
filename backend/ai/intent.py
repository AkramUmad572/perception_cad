"""LLM intent parsing: natural language → CadQuery Python codegen (free-rein)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.config import Settings
from app.models import Intent

logger = logging.getLogger(__name__)

CODEGEN_SYSTEM_PROMPT = """You are a CadQuery Python code generator for a voice-controlled CAD system.

When the user describes ANY 3D shape or object, you generate working CadQuery Python code.
You can make ANYTHING the user asks for — there are no restrictions on shape types.

## Output Format
Return ONLY valid JSON:
{
  "action": "generate",
  "script": "import cadquery as cq\\nresult = ...",
  "reply": "Building a [description]."
}

Or for non-CAD requests:
{
  "action": "set_material",
  "params": {"color": "#HEXCODE"},
  "reply": "Changed the color."
}

Or if you need clarification:
{
  "action": "clarify",
  "reply": "Could you describe what shape you'd like?"
}

## Script Requirements
1. Script MUST start with `import cadquery as cq`
2. Script MUST define `result` variable with the final CadQuery workplane/solid
3. Use millimeters for all dimensions
4. Default to reasonable sizes (10-50mm) unless user specifies
5. Code must be syntactically valid Python

## SANDBOX SECURITY RULES — CRITICAL
Scripts run in a restricted sandbox. ONLY use the patterns below or the script will fail.

### ALLOWED operations:
- Import: `import cadquery as cq` and `import math` ONLY
- Entry point: `cq.Workplane("XY")` / `cq.Workplane("XZ")` / `cq.Workplane("YZ")`
- 2D sketching: .circle(), .rect(), .polygon(), .polyline(), .close(), .text()
- 3D operations: .box(), .cylinder(), .sphere(), .extrude(), .loft(), .revolve()
- Subtractive: .cut(), .hole()
- Edge/face ops: .fillet(), .chamfer(), .edges(), .faces(), .workplane()
- Boolean: .union(), .cut(), .intersect() via Workplane methods
- Selectors: .faces(">Z"), .edges("|Z"), etc. for selecting geometry
- Variables: plain Python variables for dimensions, lists for polyline points
- Math: math.pi, math.sin, math.cos, math.sqrt, etc.
- Loops: for/while for generating point lists

### FORBIDDEN — will trigger SECURITY error:
- getattr, setattr, delattr, hasattr
- type(), object, vars(), dir(), globals(), locals()
- __import__, importlib, exec, eval, compile
- open(), file operations, pathlib, os, sys
- Network: socket, urllib, requests, http
- Any module not in [math, cadquery]
- Accessing cq internals: cq.occ_impl, cq.selectors internals, __class__, __bases__
- Dynamic attribute access or introspection tricks

## CadQuery Examples (sandbox-safe patterns only)

Simple box:
```python
import cadquery as cq
result = cq.Workplane("XY").box(30, 20, 10)
```

Cylinder:
```python
import cadquery as cq
result = cq.Workplane("XY").circle(15).extrude(40)
```

Sphere:
```python
import cadquery as cq
result = cq.Workplane("XY").sphere(20)
```

Ring (hollow cylinder):
```python
import cadquery as cq
outer_r, inner_r, height = 11, 9, 4
result = cq.Workplane("XY").circle(outer_r).extrude(height).faces(">Z").workplane().hole(inner_r * 2)
```

Cone (via loft):
```python
import cadquery as cq
result = cq.Workplane("XY").circle(20).workplane(offset=30).circle(0.1).loft()
```

Pyramid (via loft):
```python
import cadquery as cq
result = cq.Workplane("XY").rect(30, 30).workplane(offset=25).rect(1, 1).loft()
```

Hexagonal prism:
```python
import cadquery as cq
result = cq.Workplane("XY").polygon(6, 20).extrude(15)
```

Box with hole:
```python
import cadquery as cq
result = cq.Workplane("XY").box(30, 30, 20).faces(">Z").workplane().hole(10)
```

Rounded box (fillet all edges):
```python
import cadquery as cq
result = cq.Workplane("XY").box(30, 20, 15).edges().fillet(3)
```

Chamfered box:
```python
import cadquery as cq
result = cq.Workplane("XY").box(25, 25, 12).edges().chamfer(2)
```

Keychain (plate + hole + fillet + text):
```python
import cadquery as cq
plate = cq.Workplane("XY").box(50, 25, 4).edges("|Z").fillet(3)
with_hole = plate.faces(">Z").workplane().center(20, 0).hole(5)
result = with_hole.faces(">Z").workplane().center(-5, 0).text("KEY", 8, 1)
```

Star shape (using polyline with math):
```python
import cadquery as cq
import math
pts = []
for i in range(10):
    angle = i * math.pi / 5
    r = 20 if i % 2 == 0 else 10
    pts.append((r * math.cos(angle), r * math.sin(angle)))
result = cq.Workplane("XY").polyline(pts).close().extrude(5)
```

Gear-like shape:
```python
import cadquery as cq
import math
n_teeth = 12
outer_r, inner_r = 25, 20
pts = []
for i in range(n_teeth * 2):
    angle = i * math.pi / n_teeth
    r = outer_r if i % 2 == 0 else inner_r
    pts.append((r * math.cos(angle), r * math.sin(angle)))
result = cq.Workplane("XY").polyline(pts).close().extrude(8).faces(">Z").workplane().hole(10)
```

Vase (revolved profile):
```python
import cadquery as cq
pts = [(0, 0), (20, 0), (15, 30), (18, 50), (10, 60), (10, 65), (18, 65), (20, 50), (17, 30), (22, 0)]
result = cq.Workplane("XZ").polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0))
```

Heart shape:
```python
import cadquery as cq
import math
pts = []
for t_int in range(100):
    t = t_int * 2 * math.pi / 100
    x = 16 * (math.sin(t) ** 3)
    y = 13 * math.cos(t) - 5 * math.cos(2*t) - 2 * math.cos(3*t) - math.cos(4*t)
    pts.append((x, y))
result = cq.Workplane("XY").polyline(pts).close().extrude(5)
```

Text extrusion:
```python
import cadquery as cq
result = cq.Workplane("XY").text("Hi", 10, 3)
```

## Color Handling
For "make it yellow", "change color to blue", etc:
- action: "set_material"
- params: {"color": "#HEXCODE"}

Color map: yellow=#FFD700, red=#E53935, blue=#1E88E5, green=#43A047,
silver/grey=#C0C0C0, black=#212121, white=#FAFAFA, orange=#FB8C00, purple=#8E24AA, pink=#EC407A

## Modification Handling
For "make it bigger", "make it taller", etc. when there's existing code:
- Modify the dimensions in the current script proportionally
- action: "generate" with updated script

## Speech-to-text corrections
Common STT errors: bring/rink→ring, cubes→box, yello→yellow, bill me→build me
"""

REPAIR_PROMPT = """The previous CadQuery script failed with this error:
{error}

Original script:
```python
{script}
```

Fix the script to resolve the error.

## If error is SECURITY-related (blocked import, blocked builtin, access denied):
The sandbox only allows these patterns:
- Imports: ONLY `import cadquery as cq` and `import math`
- Entry: `cq.Workplane("XY")`, `cq.Workplane("XZ")`, `cq.Workplane("YZ")`
- 2D: .circle(), .rect(), .polygon(), .polyline(), .close(), .text()
- 3D: .box(), .cylinder(), .sphere(), .extrude(), .loft(), .revolve()
- Subtractive: .cut(), .hole()
- Edges/faces: .fillet(), .chamfer(), .edges(), .faces(), .workplane()
- Boolean: .union(), .cut(), .intersect() via Workplane methods
- FORBIDDEN: getattr, setattr, type, object, __import__, exec, eval, open, os, sys, importlib, pathlib, any introspection

Rewrite using ONLY the allowed patterns above. Do NOT try workarounds.

## Other common issues:
- Syntax errors: check parentheses, quotes, indentation
- Invalid operations: some CadQuery methods don't work on all shapes
- Division issues: ensure no division by zero
- Import errors: only 'cadquery' and 'math' are available

Return ONLY the corrected JSON:
{{"action": "generate", "script": "...", "reply": "Fixed: [brief description]"}}
"""

COLOR_MAP = {
    "yellow": "#FFD700", "gold": "#FFD700",
    "red": "#E53935", "crimson": "#DC143C",
    "blue": "#1E88E5", "navy": "#000080",
    "green": "#43A047", "lime": "#32CD32",
    "silver": "#C0C0C0", "grey": "#C0C0C0", "gray": "#C0C0C0",
    "black": "#212121",
    "white": "#FAFAFA",
    "orange": "#FB8C00",
    "purple": "#8E24AA", "violet": "#8E24AA",
    "pink": "#EC407A",
    "brown": "#795548",
    "cyan": "#00BCD4", "teal": "#009688",
}

_STT_FIXES = [
    # General STT corrections (not shape-biased)
    (r"\bbill me\b", "build me"), (r"\bbuilt me\b", "build me"),
    (r"\byello\b", "yellow"), (r"\bmellow\b", "yellow"),
]


def _normalize_transcript(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    lower = t.lower()
    for pattern, repl in _STT_FIXES:
        lower = re.sub(pattern, repl, lower, flags=re.IGNORECASE)
    return lower.strip()


def _check_color_only(text: str) -> Intent | None:
    """Fast path: detect pure color-change requests."""
    t = text.lower()
    for name, hex_color in COLOR_MAP.items():
        if re.search(rf"\b{name}\b", t):
            if any(kw in t for kw in ["color", "make it", "make this", "change", "paint", f"to {name}"]):
                return Intent(
                    action="set_material",
                    params={"color": hex_color},
                    reply=f"Changed to {name}.",
                )
            if t.strip() == name:
                return Intent(
                    action="set_material",
                    params={"color": hex_color},
                    reply=f"Changed to {name}.",
                )
    return None


def _check_scale_modify(text: str, current_script: str | None) -> Intent | None:
    """Fast path: detect scale/size modification requests."""
    if not current_script:
        return None
    t = text.lower()
    
    scale = None
    direction = None
    if any(w in t for w in ["bigger", "larger", "scale up"]):
        scale, direction = 1.25, "bigger"
    elif any(w in t for w in ["smaller", "scale down", "shrink"]):
        scale, direction = 0.8, "smaller"
    elif any(w in t for w in ["taller", "higher"]):
        scale, direction = 1.35, "taller"
    elif any(w in t for w in ["shorter", "lower"]):
        scale, direction = 0.7, "shorter"
    elif any(w in t for w in ["thicker", "wider"]):
        scale, direction = 1.3, "thicker"
    elif any(w in t for w in ["thinner", "narrower"]):
        scale, direction = 0.75, "thinner"
    
    if scale is None:
        return None
    
    modified = _scale_dimensions_in_script(current_script, scale, direction)
    if modified != current_script:
        return Intent(
            action="generate",
            script=modified,
            reply=f"Made it {direction}.",
        )
    return None


def _scale_dimensions_in_script(script: str, scale: float, direction: str) -> str:
    """Scale numeric dimensions in a CadQuery script."""
    def scale_number(match):
        num = float(match.group(0))
        if num > 0.5:
            return str(round(num * scale, 2))
        return match.group(0)
    
    lines = script.split('\n')
    modified_lines = []
    for line in lines:
        if 'import' in line or line.strip().startswith('#'):
            modified_lines.append(line)
        else:
            modified_lines.append(re.sub(r'\b\d+\.?\d*\b', scale_number, line))
    
    return '\n'.join(modified_lines)


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


async def _gemini_codegen(
    text: str,
    settings: Settings,
    current_script: str | None = None,
    last_error: str | None = None,
) -> Intent:
    """Generate CadQuery code via Gemini API."""
    import httpx

    model = settings.gemini_model
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    if last_error and current_script:
        user_content = REPAIR_PROMPT.format(error=last_error, script=current_script)
    else:
        context = {"utterance": text}
        if current_script:
            context["current_script"] = current_script
        user_content = json.dumps(context)

    payload = {
        "system_instruction": {"parts": [{"text": CODEGEN_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            params={"key": settings.gemini_api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    raw = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "{}")
    )
    parsed = _parse_json_response(raw)
    return Intent.model_validate(parsed)


async def _openai_codegen(
    text: str,
    settings: Settings,
    current_script: str | None = None,
    last_error: str | None = None,
) -> Intent:
    """Generate CadQuery code via OpenAI API."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    if last_error and current_script:
        user_content = REPAIR_PROMPT.format(error=last_error, script=current_script)
    else:
        context = {"utterance": text}
        if current_script:
            context["current_script"] = current_script
        user_content = json.dumps(context)

    resp = await client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CODEGEN_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    parsed = json.loads(raw)
    return Intent.model_validate(parsed)


async def generate_code(
    text: str,
    settings: Settings,
    current_script: str | None = None,
    last_error: str | None = None,
) -> Intent:
    """Generate CadQuery code from natural language using LLM."""
    if settings.gemini_api_key:
        try:
            return await _gemini_codegen(text, settings, current_script, last_error)
        except Exception as exc:
            logger.warning("Gemini codegen failed (%s); trying OpenAI", exc)

    if settings.openai_api_key:
        try:
            return await _openai_codegen(text, settings, current_script, last_error)
        except Exception as exc:
            logger.warning("OpenAI codegen failed (%s)", exc)

    return Intent(
        action="clarify",
        reply="Code generation unavailable. Please configure GEMINI_API_KEY or OPENAI_API_KEY.",
    )


async def parse_intent(
    text: str,
    settings: Settings,
    current_template: str | None,
    current_params: dict[str, Any],
    current_script: str | None = None,
) -> tuple[Intent, float]:
    """
    Parse user utterance into Intent with CadQuery script.
    
    Flow:
    1. Normalize STT errors
    2. Fast path: pure color changes
    3. Fast path: scale/size modifications (if script exists)
    4. LLM codegen: generate CadQuery Python for any shape
    
    Returns (Intent, latency_ms).
    """
    t0 = time.perf_counter()
    cleaned = _normalize_transcript(text)
    logger.info("Intent parsing: %r", cleaned)

    color_intent = _check_color_only(cleaned)
    if color_intent:
        logger.info("Fast path: color change → %s", color_intent.params.get("color"))
        return color_intent, (time.perf_counter() - t0) * 1000

    scale_intent = _check_scale_modify(cleaned, current_script)
    if scale_intent:
        logger.info("Fast path: scale modification")
        return scale_intent, (time.perf_counter() - t0) * 1000

    intent = await generate_code(cleaned, settings, current_script)
    logger.info("LLM codegen → action=%s", intent.action)
    return intent, (time.perf_counter() - t0) * 1000


async def repair_and_retry(
    original_text: str,
    failed_script: str,
    error: str,
    settings: Settings,
    max_retries: int = 2,
) -> Intent:
    """
    Attempt to repair a failed CadQuery script.
    
    Feeds the error back to the LLM and asks for a fix.
    """
    for attempt in range(max_retries):
        logger.info("Repair attempt %d/%d for error: %s", attempt + 1, max_retries, error[:100])
        intent = await generate_code(
            original_text,
            settings,
            current_script=failed_script,
            last_error=error,
        )
        if intent.action == "generate" and intent.script:
            return intent
        failed_script = intent.script or failed_script

    return Intent(
        action="clarify",
        reply=f"I couldn't generate working code after {max_retries} attempts. Error: {error}",
    )
