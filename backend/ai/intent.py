"""LLM intent parsing: natural language → structured CAD ops."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.config import Settings
from app.models import Intent
from cad.builder import DEFAULTS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You convert spoken CAD commands into JSON for a parametric CAD system.
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
"""

# Common STT mis-hearings → intended CAD words
_STT_FIXES = [
    (r"\bbrings?\b", "ring"),
    (r"\brinks?\b", "ring"),
    (r"\bwrings?\b", "ring"),
    (r"\brang\b", "ring"),
    (r"\bwrong\b", "ring"),
    (r"\bdonut\b", "ring"),
    (r"\bdoughnut\b", "ring"),
    (r"\btorus\b", "ring"),
    (r"\bcubes?\b", "box"),
    (r"\bblocks?\b", "box"),
    (r"\bsquares?\b", "box"),
    (r"\btubes?\b", "cylinder"),
    (r"\bpipes?\b", "cylinder"),
    (r"\bcylinders?\b", "cylinder"),
    (r"\bbill me\b", "build me"),
    (r"\bbuilt me\b", "build me"),
    (r"\bbuilding me\b", "build me"),
    (r"\bmake me a\b", "build me a"),
    (r"\bcreate me a\b", "build me a"),
    (r"\byello\b", "yellow"),
    (r"\bmellow\b", "yellow"),
]


def _normalize_transcript(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    lower = t.lower()
    for pattern, repl in _STT_FIXES:
        lower = re.sub(pattern, repl, lower, flags=re.IGNORECASE)
    return lower.strip()


def _rule_based_intent(
    text: str, current_template: str | None, current_params: dict[str, Any]
) -> Intent:
    t = _normalize_transcript(text)
    logger.info("Intent rules on: %r", t)

    color_map = {
        "yellow": "#FFD700",
        "gold": "#FFD700",
        "red": "#E53935",
        "blue": "#1E88E5",
        "green": "#43A047",
        "silver": "#C0C0C0",
        "grey": "#C0C0C0",
        "gray": "#C0C0C0",
        "black": "#212121",
        "white": "#FAFAFA",
        "orange": "#FB8C00",
        "purple": "#8E24AA",
        "pink": "#EC407A",
    }

    # Color — any mention of a color word with make/change/paint/to, or just "yellow"
    for name, hex_color in color_map.items():
        if re.search(rf"\b{name}\b", t):
            if (
                "color" in t
                or "make it" in t
                or "make this" in t
                or "change" in t
                or "paint" in t
                or f"to {name}" in t
                or t.strip() == name
                or re.search(rf"\b(it|this|that)\s+{name}\b", t)
            ):
                return Intent(
                    action="set_material",
                    params={"color": hex_color},
                    reply=f"Made it {name}.",
                )

    # Create — ring / box / cylinder (+ synonyms already normalized)
    create_map = {
        "ring": ["ring", "rings"],
        "box": ["box", "boxes"],
        "cylinder": ["cylinder", "cylinders"],
    }
    color_in_utterance = None
    for name, hex_color in color_map.items():
        if re.search(rf"\b{name}\b", t):
            color_in_utterance = (name, hex_color)
            break

    scale = 1.0
    if re.search(r"\b(small|tiny|little)\b", t):
        scale = 0.75
    elif re.search(r"\b(large|big|huge)\b", t):
        scale = 1.4

    for template, words in create_map.items():
        for w in words:
            if re.search(
                rf"\b(build|make|create|generate|spawn|add|give)\b.*\b{w}\b", t
            ) or re.search(rf"\b(a|an)\s+{w}\b", t):
                params = dict(DEFAULTS[template])
                for k, v in list(params.items()):
                    if isinstance(v, (int, float)) and k.endswith("_mm"):
                        params[k] = round(float(v) * scale, 2)
                reply = f"Building a {template}."
                if color_in_utterance:
                    params["color"] = color_in_utterance[1]
                    reply = f"Building a {color_in_utterance[0]} {template}."
                return Intent(
                    action="create",
                    template=template,  # type: ignore[arg-type]
                    params=params,
                    reply=reply,
                )
            if t.strip() in {w, f"a {w}", f"an {w}"}:
                params = dict(DEFAULTS[template])
                if color_in_utterance:
                    params["color"] = color_in_utterance[1]
                return Intent(
                    action="create",
                    template=template,  # type: ignore[arg-type]
                    params=params,
                    reply=f"Building a {template}.",
                )

    # Thicker / thinner (ring band or generic height)
    if current_template and ("thicker" in t or "thicken" in t):
        if current_template == "ring":
            height = float(current_params.get("height_mm", 4.0)) + 2.0
            outer = float(current_params.get("outer_diameter_mm", 22.0))
            inner = float(current_params.get("inner_diameter_mm", 18.0))
            outer = max(outer, inner + 6.0)
            return Intent(
                action="modify",
                template="ring",
                params={"height_mm": height, "outer_diameter_mm": outer},
                reply="Making the band thicker.",
            )
        height = float(current_params.get("height_mm", 20.0)) * 1.3
        return Intent(
            action="modify",
            template=current_template,  # type: ignore[arg-type]
            params={"height_mm": height},
            reply="Making it thicker.",
        )

    if current_template and ("thinner" in t or "skinny" in t):
        if current_template == "ring":
            height = max(1.5, float(current_params.get("height_mm", 4.0)) - 1.5)
            outer = float(current_params.get("outer_diameter_mm", 22.0))
            inner = float(current_params.get("inner_diameter_mm", 18.0))
            gap = max(2.0, (outer - inner) - 1.5)
            outer = inner + gap
            return Intent(
                action="modify",
                template="ring",
                params={"height_mm": height, "outer_diameter_mm": outer},
                reply="Making the band thinner.",
            )
        height = max(5.0, float(current_params.get("height_mm", 20.0)) * 0.75)
        return Intent(
            action="modify",
            template=current_template,  # type: ignore[arg-type]
            params={"height_mm": height},
            reply="Making it thinner.",
        )

    # Inner diameter N mm
    m = re.search(
        r"inner\s+(?:diameter|hole)?\s*(?:to\s*)?(\d+(?:\.\d+)?)\s*(mm|millimeters?)?",
        t,
    )
    if current_template == "ring" and m:
        return Intent(
            action="modify",
            template="ring",
            params={"inner_diameter_mm": float(m.group(1))},
            reply=f"Setting inner diameter to {m.group(1)} millimeters.",
        )

    # taller / shorter
    if current_template and ("taller" in t or "higher" in t):
        height = float(current_params.get("height_mm", 20.0)) * 1.35
        return Intent(
            action="modify",
            template=current_template,  # type: ignore[arg-type]
            params={"height_mm": height},
            reply="Making it taller.",
        )
    if current_template and ("shorter" in t or "lower" in t):
        height = max(3.0, float(current_params.get("height_mm", 20.0)) * 0.7)
        return Intent(
            action="modify",
            template=current_template,  # type: ignore[arg-type]
            params={"height_mm": height},
            reply="Making it shorter.",
        )

    # Generic scale
    if current_template and ("bigger" in t or "larger" in t or "scale up" in t):
        params = {
            k: float(v) * 1.25
            for k, v in current_params.items()
            if isinstance(v, (int, float)) and k.endswith("_mm")
        }
        return Intent(
            action="modify",
            template=current_template,  # type: ignore[arg-type]
            params=params,
            reply="Making it bigger.",
        )

    if current_template and ("smaller" in t or "scale down" in t):
        params = {
            k: float(v) * 0.8
            for k, v in current_params.items()
            if isinstance(v, (int, float)) and k.endswith("_mm")
        }
        return Intent(
            action="modify",
            template=current_template,  # type: ignore[arg-type]
            params=params,
            reply="Making it smaller.",
        )

    return Intent(
        action="clarify",
        reply=(
            "Try: build me a ring, box, or cylinder. "
            "Then: make it yellow, make it bigger, or make the band thicker."
        ),
    )


async def _intent_from_gemini(
    text: str,
    cleaned: str,
    settings: Settings,
    current_template: str | None,
    current_params: dict[str, Any],
) -> Intent:
    import httpx

    user = {
        "utterance": text,
        "normalized": cleaned,
        "current_template": current_template,
        "current_params": current_params,
    }
    model = settings.gemini_model
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": json.dumps(user)}],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
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
    # Strip markdown fences if model wraps JSON anyway
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    parsed = json.loads(raw)
    intent = Intent.model_validate(parsed)
    if intent.action == "clarify":
        fallback = _rule_based_intent(cleaned, current_template, current_params)
        if fallback.action != "clarify":
            return fallback
    return intent


async def _intent_from_openai(
    text: str,
    cleaned: str,
    settings: Settings,
    current_template: str | None,
    current_params: dict[str, Any],
) -> Intent:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    user = {
        "utterance": text,
        "normalized": cleaned,
        "current_template": current_template,
        "current_params": current_params,
    }
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user)},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    intent = Intent.model_validate(data)
    if intent.action == "clarify":
        fallback = _rule_based_intent(cleaned, current_template, current_params)
        if fallback.action != "clarify":
            return fallback
    return intent


async def parse_intent(
    text: str,
    settings: Settings,
    current_template: str | None,
    current_params: dict[str, Any],
) -> tuple[Intent, float]:
    t0 = time.perf_counter()
    cleaned = _normalize_transcript(text)

    # Prefer Gemini (free-tier), then OpenAI, then rules
    if settings.gemini_api_key:
        try:
            intent = await _intent_from_gemini(
                text, cleaned, settings, current_template, current_params
            )
            logger.info("Intent via Gemini: %s", intent.action)
            return intent, (time.perf_counter() - t0) * 1000
        except Exception as exc:
            logger.warning("Gemini intent failed (%s); trying fallback", exc)

    if settings.openai_api_key:
        try:
            intent = await _intent_from_openai(
                text, cleaned, settings, current_template, current_params
            )
            logger.info("Intent via OpenAI: %s", intent.action)
            return intent, (time.perf_counter() - t0) * 1000
        except Exception as exc:
            logger.warning("OpenAI intent failed (%s); using rules", exc)

    intent = _rule_based_intent(cleaned, current_template, current_params)
    return intent, (time.perf_counter() - t0) * 1000
