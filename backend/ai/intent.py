"""LLM intent parsing: natural language → CadQuery Python codegen (free-rein)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.config import Settings
from app.models import Intent
from photos.search import is_photo_search, photo_query

logger = logging.getLogger(__name__)

CODEGEN_SYSTEM_PROMPT = """You are a CadQuery Python generator for a voice-driven CAD app.

Free-rein: ANY object. No shape whitelist. Prefer TOO MANY named parts over one lump.

## Output (JSON only)
Always include "backend":"cad" or "backend":"mesh".

CAD geometry (list parts FIRST, then the script):
{"action":"generate","backend":"cad","parts":[{"name":"head","color":"#FFD700"}],"size_mm":60,"script":"import cadquery as cq\\n...\\nresult = assy","reply":"Short spoken confirmation."}

Mesh / sculpted model (NO CadQuery script):
{"action":"generate","backend":"mesh","mesh_prompt":"short visual English for a 3D model","size_mm":200,"script":null,"reply":"Sculpting that."}

Whole-object color only (no part named):
{"action":"set_material","backend":"cad","params":{"color":"#HEXCODE"},"reply":"Changed the color."}

Need a missing fact:
{"action":"clarify","reply":"What color?"}

## Backend (pick first; do not guess against these)
CAD if ANY is true: hole, mm, fillet, gear, mug, vase, stand, plate, hinge, printable, 3D print, keychain/keyring, axle, follow-up on a CAD script, or the user asked for a printable/dimensional part — even on a character ("Pikachu keychain", "car with a 4mm axle").
MESH if: creature, character, animal, person, "looks like X", "a model of X", or a toy car / a car with no hole/print. "make me a Pikachu" = mesh. "make me a toy car" = mesh.
Existing CAD + "now a dragon" = mesh (new organic object). Mesh session tweaks ("cuter", "more yellow") stay mesh. Do not emit a CadQuery script for mesh.

## Richness (required)
- Invent the canonical part breakdown. Not a phrasebook.
  Character: head, body, ear bases, ear tips, eyes, cheeks, limbs/tail as relevant.
  Vehicle: body, cabin, windows/greenhouse, bumper, 4 wheels, hubs, headlights.
  Keychain: charm parts + lug + 3-4mm hole.
- 8-15 parts for characters/vehicles/keychains. 4+ for a simple mug.
- Characteristic details are mandatory (keychain hole, four distinct wheels, ear tips + cheeks on a Pikachu-class creature).
- Default overall size ~50-90mm. Keychains may be flatter (~6-8mm thick).

## Assembly
More than one part → `result` MUST be `cq.Assembly()`, never a single .union() of everything.
assy.add(solid, name="wheels", color=cq.Color("#212121"))
Use matching names in the parts[] array.

## CadQuery 2 API
- import cadquery as cq (and math if needed). Valid Python only. Millimeters.
- .extrude(height) ONLY — never extrude(..., centered=...). .box(l,w,h) may use centered=.
- NO .cone() (does not exist). Tapered solids: loft two circles at an offset.
- .transformed(offset=(x,y,z), rotate=(rx,ry,rz)) — the kwarg is `rotate`, NOT `rotation`.
- OK: Workplane XY/XZ/YZ, Sketch, circle/rect/polygon/polyline/close/text, box/cylinder/sphere, extrude/loft/revolve/sweep, cut/hole/union/intersect, fillet/chamfer, transformed/offset, edges/faces/workplane/center, shell, Assembly, Color, Location, Vector.
- FORBIDDEN: getattr/setattr/type/object/exec/eval/open/os/sys/network/importlib/pathlib/cq.occ_impl.

## Follow-ups
User JSON may include current_script, last_summary, current_color.
- Same object: EDIT the Assembly. Keep parts. Change dimensions/colors. NEVER scale loop counts, n_teeth, or range().
- Different object: new script + new parts list.
- "make the ears black" / "wheels black": EDIT those parts' cq.Color, action=generate.
- "make it yellow" with no part name: set_material.

## Examples
Character (lofted ears, colored tips) — pattern for any creature:
```python
import cadquery as cq
def loft_ear(x, y, z, r0, r1, h):
    return (cq.Workplane("XY").transformed(offset=(x, y, z))
            .circle(r0).workplane(offset=h).circle(r1).loft())
head = cq.Workplane("XY").sphere(16)
body = cq.Workplane("XY").transformed(offset=(0, -18, 0)).sphere(14)
ear_l = loft_ear(-10, 12, 6, 5.5, 1.6, 16)
ear_r = loft_ear(10, 12, 6, 5.5, 1.6, 16)
tip_l = loft_ear(-10, 12, 20, 2.0, 0.7, 7)
tip_r = loft_ear(10, 12, 20, 2.0, 0.7, 7)
eye_l = cq.Workplane("XY").transformed(offset=(-5, 2, 13)).sphere(2.2)
eye_r = cq.Workplane("XY").transformed(offset=(5, 2, 13)).sphere(2.2)
cheek_l = cq.Workplane("XY").transformed(offset=(-10, -3, 11)).sphere(3.8)
cheek_r = cq.Workplane("XY").transformed(offset=(10, -3, 11)).sphere(3.8)
lug = (cq.Workplane("XY").transformed(offset=(0, 22, 4)).circle(4.5).extrude(3)
       .faces(">Z").workplane().hole(3.2))
assy = cq.Assembly()
assy.add(head, name="head", color=cq.Color("#FFD700"))
assy.add(body, name="body", color=cq.Color("#FFD700"))
assy.add(ear_l, name="ear_l", color=cq.Color("#FFD700"))
assy.add(ear_r, name="ear_r", color=cq.Color("#FFD700"))
assy.add(tip_l, name="tip_l", color=cq.Color("#212121"))
assy.add(tip_r, name="tip_r", color=cq.Color("#212121"))
assy.add(eye_l, name="eye_l", color=cq.Color("#212121"))
assy.add(eye_r, name="eye_r", color=cq.Color("#212121"))
assy.add(cheek_l, name="cheek_l", color=cq.Color("#E53935"))
assy.add(cheek_r, name="cheek_r", color=cq.Color("#E53935"))
assy.add(lug, name="lug", color=cq.Color("#FFD700"))
result = assy
```

Keychain lug + hole:
```python
import cadquery as cq
plate = cq.Workplane("XY").box(46, 26, 6).edges("|Z").fillet(5)
charm = plate.faces(">Z").workplane().center(-8, 0).text("Hi", 8, 1.2)
lug = (cq.Workplane("XY").transformed(offset=(18, 0, 0)).circle(5).extrude(6)
       .faces(">Z").workplane().hole(3.5))
assy = cq.Assembly()
assy.add(plate, name="plate", color=cq.Color("#FFD700"))
assy.add(charm, name="text", color=cq.Color("#212121"))
assy.add(lug, name="lug", color=cq.Color("#FFD700"))
result = assy
```

Toy car (cabin, 4 wheels, headlights):
```python
import cadquery as cq
body = cq.Workplane("XY").box(72, 30, 16)
cabin = cq.Workplane("XY").transformed(offset=(-8, 0, 14)).box(30, 24, 14)
bumper = cq.Workplane("XY").transformed(offset=(34, 0, -2)).box(8, 28, 8)
def wheel(x, y):
    return cq.Workplane("YZ").transformed(offset=(x, y, -8)).circle(7).extrude(6)
def hub(x, y):
    return cq.Workplane("YZ").transformed(offset=(x, y, -8)).circle(2.8).extrude(6.5)
def lamp(x, y, z):
    return cq.Workplane("XY").transformed(offset=(x, y, z)).sphere(3)
assy = cq.Assembly()
assy.add(body, name="body", color=cq.Color("#E53935"))
assy.add(cabin, name="cabin", color=cq.Color("#90CAF9"))
assy.add(bumper, name="bumper", color=cq.Color("#C0C0C0"))
assy.add(wheel(22, 16), name="wheel_fl", color=cq.Color("#212121"))
assy.add(wheel(22, -16), name="wheel_fr", color=cq.Color("#212121"))
assy.add(wheel(-22, 16), name="wheel_rl", color=cq.Color("#212121"))
assy.add(wheel(-22, -16), name="wheel_rr", color=cq.Color("#212121"))
assy.add(hub(22, 16), name="hub_fl", color=cq.Color("#C0C0C0"))
assy.add(lamp(34, 8, 4), name="lamp_l", color=cq.Color("#FFF3E0"))
assy.add(lamp(34, -8, 4), name="lamp_r", color=cq.Color("#FFF3E0"))
result = assy
```

## Color hex
yellow/gold=#FFD700 red=#E53935 blue=#1E88E5 green=#43A047 silver/grey=#C0C0C0
black=#212121 white=#FAFAFA orange=#FB8C00 purple=#8E24AA pink=#EC407A
navy=#0D47A1 mint=#66BB6A cream=#FFF3E0 bronze=#CD7F32

STT: yello→yellow, bill me→build me.
"""

REPAIR_PROMPT = """The previous CadQuery script failed with this error:
{error}

Original script:
```python
{script}
```

Fix THIS script. Keep the Assembly and all named parts. Do NOT replace a detailed assembly with a box/cylinder. Fix only the failing part.

## If error is SECURITY-related (blocked import, blocked builtin, access denied):
- Imports: ONLY `import cadquery as cq` and `import math`
- cq.Workplane, Sketch, Assembly, Color, Location, Vector
- .circle/.rect/.polygon/.polyline/.close/.text/.box/.cylinder/.sphere
- .extrude(height) — NEVER centered= on extrude
- .loft/.revolve/.sweep — NEVER .cone()
- .cut/.hole/.union/.fillet/.chamfer/.shell/.transformed
- FORBIDDEN: getattr, setattr, type, object, exec, eval, open, os, sys, importlib, pathlib, cq.occ_impl

## Other common issues:
- Syntax, parentheses, indentation
- .extrude(..., centered=...) is INVALID
- Missing Assembly.add names/colors
- Division by zero

Return ONLY JSON:
{{"action":"generate","parts":[...],"script":"...","reply":"Fixed: [brief]"}}
"""

COLOR_MAP = {
    "yellow": "#FFD700", "gold": "#FFD700", "golden": "#FFD700", "yellowish": "#FFD700",
    "red": "#E53935", "crimson": "#DC143C",
    "blue": "#1E88E5", "navy": "#0D47A1",
    "green": "#43A047", "lime": "#32CD32", "mint": "#66BB6A",
    "silver": "#C0C0C0", "grey": "#C0C0C0", "gray": "#C0C0C0",
    "black": "#212121",
    "white": "#FAFAFA", "cream": "#FFF3E0",
    "orange": "#FB8C00",
    "purple": "#8E24AA", "violet": "#8E24AA",
    "pink": "#EC407A",
    "brown": "#795548", "bronze": "#CD7F32",
    "cyan": "#00BCD4", "teal": "#009688",
}

_STT_FIXES = [
    # General STT corrections (not shape-biased)
    (r"\bbill me\b", "build me"), (r"\bbuilt me\b", "build me"),
    (r"\byello\b", "yellow"), (r"\bmellow\b", "yellow"),
]

# "Hey Percy" wake phrase aliases (canonical)
# Maps STT mishears: hey mercy, hey see, hey merce, hey pursey, a mercy → hey percy
_HEY_PERCY_ALIASES = re.compile(
    r"^(?:hey\s+(?:percy|mercy|merce|merci|pursey|pursee|purse|persey|piercy|see|perce|pc)|"
    r"a\s+(?:mercy|percy|merce|pursey))[,.\s]*",
    re.IGNORECASE,
)

# STT leftover wake names if the user still says "Percy".
# Do not treat bare "see" / "I see" as wake words (PTT transcripts are the command).
_WAKE_WORD_ALIASES = [
    r"^mercy\b",
    r"^percy[,.]?\s+percy\b",
]


def _normalize_wake_word(text: str) -> str:
    """
    Normalize STT garbage that appears before wake words.
    
    "Hey Percy" (canonical wake phrase) mishearings:
    - "hey mercy, build me a box" → "hey percy, build me a box"
    - "hey see, make it yellow" → "hey percy, make it yellow"
    - "a mercy build me a ring" → "hey percy, build me a ring"
    
    Legacy leftover wake names:
    - "Mercy, can you build me a box" → "Percy, can you build me a box"
    
    Bare "see" / "I see" are not stripped (PTT commands can start that way).
    
    Strategy:
    1. If text starts with "hey percy" alias, normalize to "hey percy"
    2. If text starts with "Mercy", replace with "Percy"
    3. If "Percy Percy", dedupe to single "Percy"
    """
    t = text.strip()
    lower = t.lower()
    
    # "hey percy" aliases: hey mercy, hey see, hey merce, a mercy, etc.
    match = _HEY_PERCY_ALIASES.match(t)
    if match:
        rest = t[match.end():].strip()
        if rest:
            return f"hey percy, {rest}"
        return "hey percy"
    
    # "Mercy" → "Percy" (preserve case style if original was capitalized)
    if lower.startswith("mercy"):
        # Replace "Mercy" with "Percy" preserving the rest
        if t[0].isupper():
            t = "Percy" + t[5:]
        else:
            t = "percy" + t[5:]
        return t.strip()
    
    # "Percy Percy" → "Percy"
    match = re.match(r"^percy[,.]?\s+percy\b", lower)
    if match:
        t = "Percy" + t[match.end():]
        return t.strip()
    
    return t


def _normalize_transcript(text: str) -> str:
    """
    Normalize STT transcript before intent parsing.
    
    Steps:
    1. Basic cleanup (whitespace normalization)
    2. Optional leftover "hey percy" / "mercy" prefixes
    3. Common STT word fixes (bill me → build me, yello → yellow)
    """
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    
    # First: normalize wake-word garbage
    t = _normalize_wake_word(t)
    
    # Then: apply word-level STT fixes
    lower = t.lower()
    for pattern, repl in _STT_FIXES:
        lower = re.sub(pattern, repl, lower, flags=re.IGNORECASE)
    
    return lower.strip()


_NEW_OBJECT_RE = re.compile(
    r"\b(build|create|design|generate)\b|"
    r"\bmake\s+(me\s+)?a\b|"
    r"\bi want a\b|"
    r"\bnow\s+(a|make)\b|"
    r"\binstead\b",
    re.IGNORECASE,
)
_COLOR_WORD_RE = re.compile(r"\b(colou?rs?|paint|painted)\b", re.IGNORECASE)
_PART_RE = re.compile(
    r"\b(ears?|tips?|wheels?|tires?|tyres?|body|bodies|heads?|cheeks?|"
    r"eyes?|windows?|cabin|bumper|handle|handles|arms?|legs?|tail|"
    r"roof|hood|doors?|hubs?|lights?|headlights?|lug|plate|rims?|"
    r"holder|keyring|lanyard|"
    r"cabin|greenhouse|limbs?)\b",
    re.IGNORECASE,
)


def extract_named_color(text: str) -> str | None:
    """Return hex for the longest matching color name, if any."""
    t = text.lower()
    hits: list[tuple[int, str]] = []
    for name, hex_color in COLOR_MAP.items():
        if re.search(rf"\b{re.escape(name)}\b", t):
            hits.append((len(name), hex_color))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def _is_new_object_request(text: str) -> bool:
    return bool(_NEW_OBJECT_RE.search(text))


# Dimensional / printable cues force CadQuery (even on a character).
_CAD_FORCE_RE = re.compile(
    r"\b("
    r"mm|millimet(?:er|re)s?|fillet|chamfer|gear|mug|vase|stand|plate|hinge|"
    r"printable|3d\s*print(?:ed|able|ing)?|keychain|keyring|axle|"
    r"n_teeth|\d+\s*-?\s*tooth|teeth|hole|holes"
    r")\b",
    re.IGNORECASE,
)
# Organic / look-like cues — backup when Gemini omits backend.
_MESH_CUE_RE = re.compile(
    r"looks\s+like|a\s+model\s+of|"
    r"\b(?:character|creature|animal|person|human|statue|dragon|corgi|cats?|dogs?|"
    r"pokemon|pikachu|toy\s+car|cars?|trucks?)\b",
    re.IGNORECASE,
)
_MESH_UNAVAILABLE_REPLY = (
    "Mesh generation isn't configured. three.ws should work with no key; "
    "or set NVIDIA_API_KEY / MESHY_API_KEY."
)
_MESH_CAD_CLARIFY_REPLY = (
    "I can't drill a hole in a sculpted mesh like CAD. "
    "Ask me to build a new CAD part, or keep sculpting this one."
)


_CAD_OBJECT_RE = re.compile(
    r"\b(box|cube|ring|cylinder|bracket|organizer|nameplate)\b",
    re.IGNORECASE,
)


def _has_cad_force(text: str) -> bool:
    return bool(_CAD_FORCE_RE.search(text))


def _has_mesh_cue(text: str) -> bool:
    return bool(_MESH_CUE_RE.search(text))


def choose_backend(
    text: str,
    session_backend: str | None = None,
    intent_backend: str | None = None,
    is_new_object: bool = False,
) -> str:
    """
    Pick cad | mesh | clarify_mesh.

    CAD cues (mm/hole/keychain/print) always win. Mesh-session tweaks stay on
    mesh unless the user asked for a new CAD object. Gemini's backend field is
    honored when local cues do not decide.
    """
    hinted = (intent_backend or "").strip().lower()
    if hinted not in ("cad", "mesh"):
        hinted = None
    session = (session_backend or "").strip().lower() or None
    cad_force = _has_cad_force(text)
    mesh_cue = _has_mesh_cue(text)

    if cad_force:
        if session == "mesh" and not is_new_object:
            return "clarify_mesh"
        return "cad"

    if session == "mesh" and not is_new_object:
        return "mesh"

    if is_new_object and mesh_cue:
        return "mesh"

    if _CAD_OBJECT_RE.search(text) and not mesh_cue:
        return "cad"

    if hinted:
        return hinted

    if session == "cad" and not is_new_object:
        return "cad"

    return "cad"


def _compose_mesh_prompt(text: str, previous: str | None = None) -> str:
    """Clean spoken English into a Meshy prompt; append follow-ups."""
    visual = re.sub(
        r"^(?:hey\s+percy[,.\s]*)?(?:please\s+)?"
        r"(?:build|make|create|generate|design)(?:\s+me)?(?:\s+a)?\s+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip(" .,")
    visual = visual or text.strip()
    if previous:
        return f"{previous}. Variation: {visual}"
    return visual


def _mesh_generate_intent(text: str, previous_prompt: str | None) -> Intent:
    prompt = _compose_mesh_prompt(text, previous_prompt)
    return Intent(
        action="generate",
        backend="mesh",
        mesh_prompt=prompt,
        script=None,
        reply="Here's that sculpt.",
    )


def _check_color_only(text: str, has_model: bool = True) -> Intent | None:
    """
    Color vocabulary only — not a command phrasebook.
    New-object requests skip this so Gemini can build the shape.
    """
    t = text.lower().strip()
    if _is_new_object_request(t):
        return None

    named = extract_named_color(t)
    if named and _PART_RE.search(t):
        return None
    if named:
        return Intent(
            action="set_material",
            params={"color": named},
            reply="Changed the color.",
        )

    if has_model and _COLOR_WORD_RE.search(t) and not named:
        return Intent(action="clarify", reply="What color?")

    return None


# Size vocabulary, in the same spirit as the colour map: words for a magnitude,
# not a phrasebook of commands. A sculpt has no editable script, so resizing it
# is a display change; CAD keeps going through codegen so the millimetres stay real.
_SCALE_FACTORS: tuple[tuple[str, float], ...] = (
    (r"\btwice as (?:big|large)\b|\bdouble\b|\b2x\b", 2.0),
    (r"\bhalf (?:the )?(?:size|as big)\b|\bhalf\b", 0.5),
    (r"\b(?:way|much|a lot) (?:bigger|larger)\b|\bhuge\b|\bmassive\b", 2.0),
    (r"\b(?:way|much|a lot) smaller\b|\btiny\b|\bminiature\b", 0.5),
    (r"\b(?:a )?(?:bit|little|touch|slightly) (?:bigger|larger)\b", 1.2),
    (r"\b(?:a )?(?:bit|little|touch|slightly) smaller\b", 0.83),
    (r"\bbigger\b|\blarger\b|\bscale (?:it )?up\b|\bgrow\b", 1.5),
    (r"\bsmaller\b|\bshrink\b|\bscale (?:it )?down\b", 0.67),
)
_SCALE_RE = re.compile("|".join(p for p, _f in _SCALE_FACTORS), re.I)


def _check_scale_only(text: str) -> Intent | None:
    """Pure resize of an existing sculpt — no need to re-sculpt it."""
    t = text.lower().strip()
    if _is_new_object_request(t) or not _SCALE_RE.search(t):
        return None
    # "make the ears bigger" is a shape edit, not a resize of the whole model.
    if _PART_RE.search(t) or extract_named_color(t):
        return None

    for pattern, factor in _SCALE_FACTORS:
        if re.search(pattern, t, re.I):
            word = "bigger" if factor > 1 else "smaller"
            return Intent(
                action="set_scale",
                params={"factor": factor},
                reply=f"Made it {word}.",
            )
    return None


def _build_user_payload(
    text: str,
    current_script: str | None = None,
    last_summary: str | None = None,
    current_color: str | None = None,
) -> str:
    payload: dict[str, Any] = {"utterance": text}
    if current_script:
        payload["has_existing_model"] = True
        payload["current_script"] = current_script
        payload["instruction"] = (
            "Edit current_script unless the user asked for a different object. "
            "Keep the Assembly and named parts; change dimensions or cq.Color. "
            "Do not scale loop counts or range(). "
            "A part name plus a color means edit those parts, not set_material."
        )
    else:
        payload["has_existing_model"] = False
    if last_summary:
        payload["last_summary"] = last_summary
    if current_color:
        payload["current_color"] = current_color
    return json.dumps(payload)


# The script field carries Python, and roughly one generation in eight forgets
# to escape the quotes in it — cq.Workplane("XZ") closes the JSON string early.
_SCRIPT_OPEN_RE = re.compile(r'"script"\s*:\s*"')
_FIELD_END_RE = re.compile(r'"\s*,\s*"[A-Za-z_][A-Za-z0-9_]*"\s*:|"\s*\}\s*$')


def _repair_script_field(raw: str) -> dict | None:
    """Re-escape a Python script that broke out of its JSON string."""
    opening = _SCRIPT_OPEN_RE.search(raw)
    if not opening:
        return None
    body_start = opening.end()
    fallback = None
    for end in _FIELD_END_RE.finditer(raw, body_start):
        body = re.sub(r'(?<!\\)"', lambda _m: '\\"', raw[body_start : end.start()])
        try:
            parsed = json.loads(raw[:body_start] + body + raw[end.start() :])
        except json.JSONDecodeError:
            continue
        # Several cut points can yield valid JSON; the one that leaves behind
        # runnable Python is the real end of the field.
        try:
            compile(parsed.get("script") or "", "<codegen>", "exec")
            return parsed
        except SyntaxError:
            fallback = fallback or parsed
    return fallback


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences and trailing junk."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = _repair_script_field(raw)
        if repaired is not None:
            logger.info("Recovered codegen JSON by re-escaping the script field")
            return repaired
        start = raw.find("{")
        if start < 0:
            raise
        obj, _idx = json.JSONDecoder().raw_decode(raw[start:])
        return obj


async def _gemini_codegen(
    text: str,
    settings: Settings,
    current_script: str | None = None,
    last_error: str | None = None,
    last_summary: str | None = None,
    current_color: str | None = None,
) -> Intent:
    """Generate CadQuery code via Gemini API."""
    import httpx

    model = settings.gemini_model
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    if last_error and current_script:
        user_content = REPAIR_PROMPT.format(error=last_error, script=current_script)
        temperature = 0.15
    else:
        user_content = _build_user_payload(
            text, current_script, last_summary, current_color
        )
        temperature = 0.15 if current_script else 0.4

    payload = {
        "system_instruction": {"parts": [{"text": CODEGEN_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": temperature,
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
    last_summary: str | None = None,
    current_color: str | None = None,
) -> Intent:
    """Generate CadQuery code via OpenAI API."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    if last_error and current_script:
        user_content = REPAIR_PROMPT.format(error=last_error, script=current_script)
        temperature = 0.15
    else:
        user_content = _build_user_payload(
            text, current_script, last_summary, current_color
        )
        temperature = 0.15 if current_script else 0.4

    resp = await client.chat.completions.create(
        model=settings.openai_model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CODEGEN_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    parsed = json.loads(raw)
    return Intent.model_validate(parsed)


PHOTO_CODEGEN_PROMPT = """Build a 3D model of the object in this photo.

Study its shape, proportions and colours, then write CadQuery that reproduces
it as a solid object. Use an Assembly with one named, coloured part per visible
feature, and take the hex colours from the photo itself.

Model only the object — not the background, the surface it rests on, or any
shadow. Reply with the same JSON contract as always."""

PHOTO_MESH_PROMPT = """Look at this photo and describe the main object for a 3D sculptor.

Reply JSON only:
{"mesh_prompt":"short visual English of the isolated object, colors and pose, no background","reply":"Sculpting that."}

No CadQuery. No pedestal, ground plane, or scene."""


async def codegen_from_photo(
    image: bytes,
    mime: str,
    settings: Settings,
    hint: str = "",
) -> Intent:
    """
    CadQuery script for whatever is in a photo, read by Gemini's vision.

    The sculpting providers are a single point of failure for image-to-3D;
    this reaches the same goal through the CAD path that already works.
    """
    import base64

    import httpx

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required to build from a photo.")
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        mime = "image/png"

    instruction = PHOTO_CODEGEN_PROMPT
    if hint.strip():
        instruction += f"\n\nThe file is named {hint.strip()!r} — a hint, not a rule."

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": CODEGEN_SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": instruction},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(image).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
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
    intent = Intent.model_validate(_parse_json_response(raw))
    intent.backend = "cad"
    return intent


async def mesh_prompt_from_photo(
    image: bytes,
    mime: str,
    settings: Settings,
    hint: str = "",
) -> str:
    """
    Short visual English for three.ws text-to-3D when the photo lane is down.

    Image-to-3D on three.ws needs a healthy TRELLIS worker; the text lane
    (`/api/3d/generate`) stays up. Gemini just tells it what the photo shows.
    """
    import base64

    import httpx

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required to describe a photo.")
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        mime = "image/png"

    instruction = PHOTO_MESH_PROMPT
    if hint.strip():
        instruction += f"\n\nThe file is named {hint.strip()!r} — a hint, not a rule."

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": instruction},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(image).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
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
    prompt = ""
    if isinstance(parsed, dict):
        prompt = str(parsed.get("mesh_prompt") or parsed.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Gemini returned no mesh prompt for the photo.")
    return prompt


async def generate_code(
    text: str,
    settings: Settings,
    current_script: str | None = None,
    last_error: str | None = None,
    last_summary: str | None = None,
    current_color: str | None = None,
) -> Intent:
    """Generate CadQuery code from natural language using LLM."""
    kwargs = {
        "text": text,
        "settings": settings,
        "current_script": current_script,
        "last_error": last_error,
        "last_summary": last_summary,
        "current_color": current_color,
    }
    if settings.gemini_api_key:
        try:
            return await _gemini_codegen(**kwargs)
        except Exception as exc:
            logger.warning("Gemini codegen failed (%s)", exc)
            if kwargs.get("current_script") and not kwargs.get("last_error"):
                try:
                    logger.info("Retrying Gemini as a new object (no current_script)")
                    kwargs = {**kwargs, "current_script": None}
                    return await _gemini_codegen(**kwargs)
                except Exception as exc2:
                    logger.warning("Gemini retry failed (%s); trying OpenAI", exc2)
            else:
                logger.warning("Trying OpenAI fallback")

    if settings.openai_api_key:
        try:
            return await _openai_codegen(**kwargs)
        except Exception as exc:
            logger.warning("OpenAI codegen failed (%s)", exc)

    keyed = bool(settings.gemini_api_key or settings.openai_api_key)
    return Intent(
        action="clarify",
        reply=(
            "I couldn't generate that model. Try saying it again."
            if keyed
            else "Code generation unavailable. Please configure GEMINI_API_KEY or OPENAI_API_KEY."
        ),
    )


def _unavailable_or_mesh_intent(
    text: str,
    settings: Settings,
    previous_prompt: str | None,
) -> Intent:
    from mesh.factory import mesh_ready

    if not mesh_ready(settings):
        return Intent(
            action="clarify",
            backend="mesh",
            reply=_MESH_UNAVAILABLE_REPLY,
        )
    return _mesh_generate_intent(text, previous_prompt)


async def parse_intent(
    text: str,
    settings: Settings,
    current_template: str | None,
    current_params: dict[str, Any],
    current_script: str | None = None,
    last_summary: str | None = None,
    current_color: str | None = None,
    last_backend: str | None = None,
    last_mesh_prompt: str | None = None,
) -> tuple[Intent, float]:
    """
    Parse user utterance into Intent (CadQuery script or mesh prompt).

    Flow:
    1. Normalize STT errors
    2. Route cad vs mesh (CAD cues, organic cues, session)
    3. Fast path: named color only on CAD sessions
    4. Mesh: skip sandbox codegen. CAD: LLM script, new object drops current_script.

    Returns (Intent, latency_ms).
    """
    t0 = time.perf_counter()
    cleaned = _normalize_transcript(text)
    logger.info("Intent parsing: %r", cleaned)

    if is_photo_search(cleaned):
        query = photo_query(cleaned)
        logger.info("Photo search → %r", query)
        return (
            Intent(
                action="find_photos",
                backend="mesh",
                photo_query=query,
                reply="Let me pull that up.",
            ),
            (time.perf_counter() - t0) * 1000,
        )

    is_new = _is_new_object_request(cleaned)
    session_backend = last_backend or ("cad" if current_script or current_template else None)
    routed = choose_backend(
        cleaned,
        session_backend=session_backend,
        is_new_object=is_new,
    )
    logger.info("Router → %s (session=%s new=%s)", routed, session_backend, is_new)

    if session_backend == "mesh" and not is_new:
        scale_intent = _check_scale_only(cleaned)
        if scale_intent:
            scale_intent.backend = "mesh"
            logger.info("Fast path: resize x%.2f", scale_intent.params["factor"])
            return scale_intent, (time.perf_counter() - t0) * 1000

    if routed == "clarify_mesh":
        return (
            Intent(action="clarify", backend="mesh", reply=_MESH_CAD_CLARIFY_REPLY),
            (time.perf_counter() - t0) * 1000,
        )

    if routed == "mesh":
        prev = last_mesh_prompt if session_backend == "mesh" and not is_new else None
        intent = _unavailable_or_mesh_intent(cleaned, settings, prev)
        logger.info("Mesh path → action=%s", intent.action)
        return intent, (time.perf_counter() - t0) * 1000

    has_model = bool(current_script or current_template)
    color_intent = _check_color_only(cleaned, has_model=has_model)
    if color_intent:
        color_intent.backend = "cad"
        if color_intent.action == "set_material":
            logger.info("Fast path: color change → %s", color_intent.params.get("color"))
        else:
            logger.info("Fast path: color clarify")
        return color_intent, (time.perf_counter() - t0) * 1000

    script_for_llm = current_script
    if current_script and is_new:
        logger.info("New object request — not sending current_script to codegen")
        script_for_llm = None

    intent = await generate_code(
        cleaned,
        settings,
        current_script=script_for_llm,
        last_summary=last_summary if script_for_llm else None,
        current_color=current_color or (current_params or {}).get("color"),
    )
    final = choose_backend(
        cleaned,
        session_backend=session_backend,
        intent_backend=intent.backend,
        is_new_object=is_new,
    )
    if final == "clarify_mesh":
        intent = Intent(action="clarify", backend="mesh", reply=_MESH_CAD_CLARIFY_REPLY)
    elif final == "mesh":
        prev = last_mesh_prompt if session_backend == "mesh" and not is_new else None
        if intent.mesh_prompt:
            mesh_intent = _unavailable_or_mesh_intent(cleaned, settings, prev)
            if mesh_intent.action == "generate":
                mesh_intent.mesh_prompt = intent.mesh_prompt
                mesh_intent.reply = intent.reply or mesh_intent.reply
                mesh_intent.size_mm = intent.size_mm
            intent = mesh_intent
        else:
            intent = _unavailable_or_mesh_intent(cleaned, settings, prev)
    else:
        intent.backend = "cad"
        intent.mesh_prompt = None
    logger.info("LLM codegen → action=%s backend=%s", intent.action, intent.backend)
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
