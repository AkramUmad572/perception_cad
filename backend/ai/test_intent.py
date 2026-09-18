#!/usr/bin/env python3
"""
Regression tests for intent parsing: STT normalization and color follow-up.

Run with: python -m ai.test_intent
Or:       python backend/ai/test_intent.py

Tests:
1. STT alias normalization: Mercy/See/I see → Percy (or stripped)
2. Color-only follow-up: Intent set_material with proper params
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.intent import (
    _build_user_payload,
    _check_color_only,
    _compose_mesh_prompt,
    _is_new_object_request,
    _normalize_transcript,
    _normalize_wake_word,
    _parse_json_response,
    choose_backend,
    extract_named_color,
)
import ai.intent as intent_mod


# ============================================================================
# Test 1: STT Wake-Word Alias Normalization
# ============================================================================

STT_WAKE_WORD_TESTS = [
    # (input, expected_output, description)
    
    # =========================================================================
    # "Hey Percy" canonical wake phrase aliases (NEW)
    # =========================================================================
    
    # "hey mercy" → "hey percy"
    ("hey mercy, build me a box", "hey percy, build me a box",
     "hey mercy → hey percy with comma"),
    ("hey mercy build me a ring", "hey percy, build me a ring",
     "hey mercy → hey percy without comma"),
    ("Hey Mercy, make it yellow", "hey percy, make it yellow",
     "Hey Mercy capitalized → hey percy"),
    
    # "hey see" → "hey percy"
    ("hey see, build me a cylinder", "hey percy, build me a cylinder",
     "hey see → hey percy"),
    ("hey see make it bigger", "hey percy, make it bigger",
     "hey see without comma → hey percy"),
    
    # "hey merce" → "hey percy"
    ("hey merce, build me a box", "hey percy, build me a box",
     "hey merce → hey percy"),
    ("hey merce make it gold", "hey percy, make it gold",
     "hey merce without comma → hey percy"),
    
    # "hey pursey" → "hey percy"
    ("hey pursey, build me a ring", "hey percy, build me a ring",
     "hey pursey → hey percy"),
    ("hey pursey make it thicker", "hey percy, make it thicker",
     "hey pursey without comma → hey percy"),
    
    # "a mercy" → "hey percy"
    ("a mercy, build me a box", "hey percy, build me a box",
     "a mercy → hey percy with comma"),
    ("a mercy build me a ring", "hey percy, build me a ring",
     "a mercy → hey percy without comma"),
    
    # Other "hey X" variants
    ("hey pursee, make it blue", "hey percy, make it blue",
     "hey pursee → hey percy"),
    ("hey persey, build a cube", "hey percy, build a cube",
     "hey persey → hey percy"),
    ("hey piercy, make it red", "hey percy, make it red",
     "hey piercy → hey percy"),
    ("hey perce, build me a gear", "hey percy, build me a gear",
     "hey perce → hey percy"),
    ("hey pc, design a ring", "hey percy, design a ring",
     "hey pc → hey percy"),
    ("hey merci, build a vase", "hey percy, build a vase",
     "hey merci → hey percy"),
    
    # Wake phrase only (no command)
    ("hey percy", "hey percy",
     "hey percy alone unchanged"),
    ("hey mercy", "hey percy",
     "hey mercy alone → hey percy"),
    ("a mercy", "hey percy",
     "a mercy alone → hey percy"),
    
    # =========================================================================
    # Legacy single-word aliases (still supported)
    # =========================================================================
    
    # "Mercy" → "Percy" normalization
    ("Mercy, can you build me a box", "Percy, can you build me a box",
     "Mercy → Percy with comma"),
    ("mercy can you make it yellow", "percy can you make it yellow",
     "lowercase mercy → percy"),
    ("Mercy. Build me a ring.", "Percy. Build me a ring.",
     "Mercy with period"),
    
    # Bare "see" / "I see" are NOT treated as wake words (PTT)
    ("See, can you build me a cylinder", "See, can you build me a cylinder",
     "See prefix preserved"),
    ("See. Make it bigger", "See. Make it bigger",
     "See with period preserved"),
    ("See, build me a box", "See, build me a box",
     "See before build preserved"),
    
    # "I see" preserved
    ("I see. Can you build a ring", "I see. Can you build a ring",
     "I see prefix preserved"),
    ("I see, can you make it yellow", "I see, can you make it yellow",
     "I see with comma preserved"),
    ("I see. Build me a cube", "I see. Build me a cube",
     "I see before build preserved"),
    
    # "Percy Percy" deduplication
    ("Percy Percy can you help", "Percy can you help",
     "Double Percy deduplicated"),
    ("percy, percy build me a box", "Percy build me a box",
     "Double percy with comma"),
    
    # Normal commands should pass through unchanged
    ("build me a box", "build me a box",
     "Normal command unchanged"),
    ("make it yellow", "make it yellow",
     "Color command unchanged"),
    ("Percy, build me a ring", "Percy, build me a ring",
     "Correct Percy unchanged"),
    
    # "see" mid-sentence should NOT be stripped
    ("can you see the model", "can you see the model",
     "mid-sentence 'see' preserved"),
    ("I want to see it bigger", "I want to see it bigger",
     "mid-sentence 'I...see' preserved"),
]


def test_wake_word_normalization():
    """Test that STT wake-word aliases are properly normalized."""
    print("\n=== Testing Wake-Word Normalization ===")
    passed = 0
    failed = 0
    
    for input_text, expected, description in STT_WAKE_WORD_TESTS:
        result = _normalize_wake_word(input_text)
        if result == expected:
            print(f"  ✓ {description}")
            passed += 1
        else:
            print(f"  ✗ {description}")
            print(f"    Input:    {input_text!r}")
            print(f"    Expected: {expected!r}")
            print(f"    Got:      {result!r}")
            failed += 1
    
    return passed, failed


# ============================================================================
# Test 2: Full Transcript Normalization (wake-word + STT fixes)
# ============================================================================

FULL_TRANSCRIPT_TESTS = [
    # "Hey Percy" wake phrase + STT word fixes combined
    ("hey mercy, bill me a box", "hey percy, build me a box",
     "hey mercy + 'bill me' → 'build me'"),
    ("hey see, make it yello", "hey percy, make it yellow",
     "hey see + yello → yellow"),
    ("a mercy bill me a ring", "hey percy, build me a ring",
     "a mercy + bill me → build me"),
    
    # Legacy wake-word + STT word fixes combined
    ("Mercy, bill me a box", "percy, build me a box",
     "Mercy + 'bill me' → 'build me'"),
    ("See, can you make it yello", "see, can you make it yellow",
     "See kept + yello → yellow"),
    ("I see. Make it mellow", "i see. make it yellow",
     "I see kept + mellow → yellow"),
    
    # Whitespace normalization
    ("  build   me  a   box  ", "build me a box",
     "Extra whitespace normalized"),
]


def test_full_transcript_normalization():
    """Test full transcript normalization pipeline."""
    print("\n=== Testing Full Transcript Normalization ===")
    passed = 0
    failed = 0
    
    for input_text, expected, description in FULL_TRANSCRIPT_TESTS:
        result = _normalize_transcript(input_text)
        if result == expected:
            print(f"  ✓ {description}")
            passed += 1
        else:
            print(f"  ✗ {description}")
            print(f"    Input:    {input_text!r}")
            print(f"    Expected: {expected!r}")
            print(f"    Got:      {result!r}")
            failed += 1
    
    return passed, failed


# ============================================================================
# Test 3: Color-Only Detection (fast path)
# ============================================================================

COLOR_INTENT_TESTS = [
    # Should return Intent with action="set_material"
    ("make it yellow", "#FFD700", "make it yellow"),
    ("change color to red", "#E53935", "change color to red"),
    ("make this blue", "#1E88E5", "make this blue"),
    ("paint it green", "#43A047", "paint it green (via 'paint')"),
    ("to orange", "#FB8C00", "'to orange' pattern"),
    ("yellow", "#FFD700", "bare color name"),
    ("make it silver", "#C0C0C0", "silver color"),
    ("make it grey", "#C0C0C0", "grey (alias for silver)"),
    ("make it gray", "#C0C0C0", "gray (US spelling)"),
    ("make it purple", "#8E24AA", "purple color"),
    ("change to pink", "#EC407A", "pink color"),
    ("make it cyan", "#00BCD4", "cyan color"),
    ("make it teal", "#009688", "teal color"),
    ("make it golden", "#FFD700", "golden alias"),
    ("make it navy", "#0D47A1", "navy"),
    ("paint it mint", "#66BB6A", "mint"),
    ("make it cream", "#FFF3E0", "cream"),
    ("make it bronze", "#CD7F32", "bronze"),
]

COLOR_NON_INTENT_TESTS = [
    # Should NOT return a color Intent (returns None)
    "build me a box",
    "make me a ring",
    "create a cylinder",
    "make the band thicker",
    "make it bigger",
    "make it twice as big",
    "build me a yellow mug",
    "I want a red gear",
    "make the wheels black",
    "make the ears black",
    "make the ear tips navy",
    "make the holder black",
]

COLOR_CLARIFY_TESTS = [
    "change the color",
    "change color",
    "paint it",
    "what color",
]


def test_color_detection():
    """Test that color-only commands are detected with correct hex codes."""
    print("\n=== Testing Color Intent Detection ===")
    passed = 0
    failed = 0
    
    # Test positive cases (should return Intent)
    for input_text, expected_color, description in COLOR_INTENT_TESTS:
        result = _check_color_only(input_text)
        if result is not None and result.action == "set_material":
            actual_color = result.params.get("color")
            if actual_color == expected_color:
                print(f"  ✓ {description} → {expected_color}")
                passed += 1
            else:
                print(f"  ✗ {description}: wrong color")
                print(f"    Expected: {expected_color}")
                print(f"    Got:      {actual_color}")
                failed += 1
        else:
            print(f"  ✗ {description}: not detected as color intent")
            print(f"    Input: {input_text!r}")
            print(f"    Got:   {result}")
            failed += 1
    
    # Test negative cases (should return None)
    for input_text in COLOR_NON_INTENT_TESTS:
        result = _check_color_only(input_text)
        if result is None:
            print(f"  [ok] '{input_text}' correctly NOT detected as color")
            passed += 1
        else:
            print(f"  [FAIL] '{input_text}' incorrectly detected as color")
            print(f"    Got: {result}")
            failed += 1

    for input_text in COLOR_CLARIFY_TESTS:
        result = _check_color_only(input_text, has_model=True)
        if result is not None and result.action == "clarify":
            print(f"  [ok] '{input_text}' → clarify")
            passed += 1
        else:
            print(f"  [FAIL] '{input_text}' should clarify when a model exists")
            print(f"    Got: {result}")
            failed += 1

        result_no_model = _check_color_only(input_text, has_model=False)
        if result_no_model is None:
            print(f"  [ok] '{input_text}' skipped with no model")
            passed += 1
        else:
            print(f"  [FAIL] '{input_text}' should skip when no model")
            print(f"    Got: {result_no_model}")
            failed += 1
    
    return passed, failed


def test_named_color_and_payload():
    """Named-color extraction and LLM payload for follow-up edits."""
    print("\n=== Testing named color + follow-up payload ===")
    passed = 0
    failed = 0

    cases = [
        ("make it golden", "#FFD700"),
        ("navy", "#0D47A1"),
        ("build me a yellow mug", "#FFD700"),
        ("no color here", None),
    ]
    for text, expected in cases:
        got = extract_named_color(text)
        if got == expected:
            print(f"  [ok] extract_named_color({text!r}) → {expected}")
            passed += 1
        else:
            print(f"  [FAIL] extract_named_color({text!r})")
            print(f"    Expected: {expected}")
            print(f"    Got:      {got}")
            failed += 1

    payload = _build_user_payload(
        "make the ears longer",
        current_script="import cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)",
        last_summary="Yellow Pikachu",
        current_color="#FFD700",
    )
    if (
        "current_script" in payload
        and "last_summary" in payload
        and "Do not scale loop counts" in payload
        and "Yellow Pikachu" in payload
    ):
        print("  [ok] follow-up payload includes script + summary")
        passed += 1
    else:
        print("  [FAIL] follow-up payload missing script/summary")
        failed += 1

    new_payload = _build_user_payload("build me a mug")
    if '"has_existing_model": false' in new_payload and "current_script" not in new_payload:
        print("  [ok] new-object payload has no current_script")
        passed += 1
    else:
        print("  [FAIL] new-object payload should omit current_script")
        failed += 1

    if not hasattr(intent_mod, "_check_scale_modify") and not hasattr(
        intent_mod, "_scale_dimensions_in_script"
    ):
        print("  [ok] naive scale-all-numbers helpers are gone")
        passed += 1
    else:
        print("  [FAIL] scale regex helpers should not exist")
        failed += 1

    extra = _parse_json_response(
        '{"action":"generate","script":"import cadquery as cq\\nresult = cq.Workplane(\\"XY\\").box(1,1,1)","reply":"ok"}\ntrailing junk'
    )
    if extra.get("action") == "generate" and extra.get("reply") == "ok":
        print("  [ok] JSON parser ignores trailing extra data")
        passed += 1
    else:
        print("  [FAIL] JSON extra-data parse")
        failed += 1

    car = "perfect. now can you just make me a toy car?"
    if _is_new_object_request(car):
        print("  [ok] 'make me a toy car' is a new-object request")
        passed += 1
    else:
        print("  [FAIL] toy car should be a new-object request")
        failed += 1

    return passed, failed


def test_choose_backend():
    print("\n=== Test: cad vs mesh router ===")
    passed = failed = 0
    cases = [
        ("build me a Pikachu keychain", None, True, "cad"),
        ("make me a Pikachu", None, True, "mesh"),
        ("build me a 12 tooth gear", None, True, "cad"),
        ("make me a toy car", None, True, "mesh"),
        ("add a hole for a keyring", "cad", False, "cad"),
        ("make it bigger", "mesh", False, "mesh"),
        ("now a dragon", "cad", True, "mesh"),
        ("add a 5mm hole", "mesh", False, "clarify_mesh"),
        ("make me a car with a 4mm axle", None, True, "cad"),
        ("3D print a car", None, True, "cad"),
        ("make it cuter", "mesh", False, "mesh"),
        ("build me a mug", None, True, "cad"),
        ("build me a box", None, True, "cad"),
    ]
    for text, session, is_new, expected in cases:
        got = choose_backend(text, session_backend=session, is_new_object=is_new)
        if got == expected:
            print(f"  [ok] {text!r} → {got}")
            passed += 1
        else:
            print(f"  [FAIL] {text!r} expected {expected}, got {got}")
            failed += 1

    prompt = _compose_mesh_prompt("make me a Pikachu")
    if prompt.lower() == "pikachu":
        print("  [ok] mesh prompt strips 'make me a'")
        passed += 1
    else:
        print(f"  [FAIL] mesh prompt {prompt!r}")
        failed += 1

    follow = _compose_mesh_prompt("make it cuter", previous="a yellow mouse")
    if follow.startswith("a yellow mouse") and "cuter" in follow:
        print("  [ok] mesh follow-up appends variation")
        passed += 1
    else:
        print(f"  [FAIL] follow-up prompt {follow!r}")
        failed += 1

    return passed, failed


def test_parse_intent_mesh_routes_without_llm():
    import asyncio
    from types import SimpleNamespace

    print("\n=== Test: parse_intent mesh skips codegen ===")
    passed = failed = 0
    settings = SimpleNamespace(
        meshy_api_key="test-key",
        nvidia_api_key="",
        three_ws_enabled=True,
        gemini_api_key="",
        openai_api_key="",
    )

    async def _run():
        intent, _ms = await intent_mod.parse_intent(
            "make me a Pikachu",
            settings,
            None,
            {},
        )
        return intent

    intent = asyncio.run(_run())
    if intent.backend == "mesh" and intent.action == "generate" and not intent.script:
        print("  [ok] Pikachu → mesh generate, no script")
        passed += 1
    else:
        print(f"  [FAIL] {intent}")
        failed += 1

    settings_empty = SimpleNamespace(
        meshy_api_key="",
        nvidia_api_key="",
        three_ws_enabled=True,
        gemini_api_key="",
        openai_api_key="",
    )

    async def _run_empty():
        return (
            await intent_mod.parse_intent("make me a Pikachu", settings_empty, None, {})
        )[0]

    empty = asyncio.run(_run_empty())
    if empty.action == "generate" and empty.backend == "mesh":
        print("  [ok] no Meshy key still routes to mesh (three.ws)")
        passed += 1
    else:
        print(f"  [FAIL] empty keys {empty}")
        failed += 1

    settings_off = SimpleNamespace(
        meshy_api_key="",
        nvidia_api_key="",
        three_ws_enabled=False,
        gemini_api_key="",
        openai_api_key="",
    )

    async def _run_off():
        return (
            await intent_mod.parse_intent("make me a Pikachu", settings_off, None, {})
        )[0]

    off = asyncio.run(_run_off())
    if off.action == "clarify":
        print("  [ok] all mesh factories off clarifies")
        passed += 1
    else:
        print(f"  [FAIL] factories off {off}")
        failed += 1

    async def _run_hole():
        return (
            await intent_mod.parse_intent(
                "add a 5mm hole",
                settings,
                None,
                {},
                last_backend="mesh",
                last_mesh_prompt="a corgi",
            )
        )[0]

    hole = asyncio.run(_run_hole())
    if hole.action == "clarify" and hole.backend == "mesh":
        print("  [ok] hole on mesh session clarifies")
        passed += 1
    else:
        print(f"  [FAIL] hole on mesh {hole}")
        failed += 1

    return passed, failed


def test_scale_fast_path():
    """Resizing a sculpt should skip the 90s re-sculpt; shape edits should not."""
    print("\n=== Test: resize fast path (mesh sessions) ===")
    import asyncio
    from types import SimpleNamespace

    passed = failed = 0

    settings = SimpleNamespace(
        meshy_api_key="",
        nvidia_api_key="",
        three_ws_enabled=True,
        gemini_api_key="",
        openai_api_key="",
    )

    async def _run(text):
        return (
            await intent_mod.parse_intent(
                text,
                settings,
                None,
                {},
                last_backend="mesh",
                last_mesh_prompt="a pikachu",
            )
        )[0]

    # (utterance, expected action, expected factor or None)
    cases = [
        ("make it bigger", "set_scale", 1.5),
        ("smaller", "set_scale", 0.67),
        ("twice as big", "set_scale", 2.0),
        ("make it a bit smaller", "set_scale", 0.83),
        ("way bigger", "set_scale", 2.0),
        # Not resizes: a part edit, a colour change, and a brand new object.
        ("make the ears bigger", "generate", None),
        ("make it bigger and red", "generate", None),
        ("make me a bigger corgi", "generate", None),
    ]

    for text, want_action, want_factor in cases:
        got = asyncio.run(_run(text))
        factor = got.params.get("factor") if got.action == "set_scale" else None
        ok = got.action == want_action and (
            want_factor is None or abs((factor or 0) - want_factor) < 0.01
        )
        if ok:
            print(f"  [ok] {text!r} → {got.action} {factor or ''}")
            passed += 1
        else:
            print(f"  [FAIL] {text!r} → {got.action} {factor} (want {want_action} {want_factor})")
            failed += 1

    # A CAD session resizes through codegen so the millimetres stay real.
    async def _cad():
        return (
            await intent_mod.parse_intent(
                "make it bigger", settings, None, {}, last_backend="cad"
            )
        )[0]

    cad = asyncio.run(_cad())
    if cad.action != "set_scale":
        print(f"  [ok] CAD resize stays in codegen (action={cad.action})")
        passed += 1
    else:
        print("  [FAIL] CAD resize took the display-only fast path")
        failed += 1

    return passed, failed


def test_photo_search_fast_path():
    """'From my photos' must skip CAD/mesh and not fire codegen."""
    print("\n=== Test: photo search fast path ===")
    import asyncio
    from types import SimpleNamespace
    from photos.search import is_photo_search, photo_query

    passed = failed = 0
    cases = [
        (
            "can you get this image from my photos of this pikachu keychain that i wanna 3d print",
            True,
            "pikachu keychain",
        ),
        ("find the pikachu in my files", True, "pikachu"),
        ("make me a pikachu", False, None),
        ("build me a pikachu keychain", False, None),
        ("3D print a car", False, None),
    ]
    for text, want, query in cases:
        got = is_photo_search(text)
        q = photo_query(text) if got else None
        ok = got == want and (query is None or (q and query in q.lower()))
        if ok:
            print(f"  [ok] {text[:48]!r} → {got} {q or ''}")
            passed += 1
        else:
            print(f"  [FAIL] {text!r} got {got} {q!r} want {want} {query}")
            failed += 1

    settings = SimpleNamespace(
        meshy_api_key="",
        nvidia_api_key="",
        three_ws_enabled=True,
        gemini_api_key="",
        openai_api_key="",
    )

    async def _run():
        return (
            await intent_mod.parse_intent(
                "get the pikachu keychain from my photos I want to 3d print it",
                settings,
                None,
                {},
            )
        )[0]

    intent = asyncio.run(_run())
    if intent.action == "find_photos" and "pikachu" in (intent.photo_query or "").lower():
        print(f"  [ok] parse_intent → find_photos {intent.photo_query!r}")
        passed += 1
    else:
        print(f"  [FAIL] parse_intent {intent.action} {intent.photo_query}")
        failed += 1

    return passed, failed


# ============================================================================
# Run All Tests
# ============================================================================

def run_all_tests():
    """Run all intent parsing regression tests."""
    print("=" * 60)
    print("INTENT PARSING REGRESSION TESTS")
    print("=" * 60)
    
    w_pass, w_fail = test_wake_word_normalization()
    t_pass, t_fail = test_full_transcript_normalization()
    c_pass, c_fail = test_color_detection()
    p_pass, p_fail = test_named_color_and_payload()
    r_pass, r_fail = test_choose_backend()
    m_pass, m_fail = test_parse_intent_mesh_routes_without_llm()
    s_pass, s_fail = test_scale_fast_path()
    ph_pass, ph_fail = test_photo_search_fast_path()

    total_pass = w_pass + t_pass + c_pass + p_pass + r_pass + m_pass + s_pass + ph_pass
    total_fail = w_fail + t_fail + c_fail + p_fail + r_fail + m_fail + s_fail + ph_fail
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Wake-word normalization:    {w_pass}/{w_pass + w_fail} passed")
    print(f"Full transcript normalize:  {t_pass}/{t_pass + t_fail} passed")
    print(f"Color intent detection:     {c_pass}/{c_pass + c_fail} passed")
    print(f"Named color + payload:      {p_pass}/{p_pass + p_fail} passed")
    print(f"CAD vs mesh router:         {r_pass}/{r_pass + r_fail} passed")
    print(f"Mesh parse_intent:          {m_pass}/{m_pass + m_fail} passed")
    print(f"Resize fast path:           {s_pass}/{s_pass + s_fail} passed")
    print(f"Photo search:               {ph_pass}/{ph_pass + ph_fail} passed")
    print(f"TOTAL:                      {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
