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

from ai.intent import _normalize_transcript, _normalize_wake_word, _check_color_only


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
    
    # "See" prefix stripping (only at start, before command-like words)
    ("See, can you build me a cylinder", "can you build me a cylinder",
     "See prefix stripped before 'can'"),
    ("See. Make it bigger", "Make it bigger",
     "See prefix stripped before 'make'"),
    ("See, build me a box", "build me a box",
     "See prefix stripped before 'build'"),
    
    # "I see" prefix stripping
    ("I see. Can you build a ring", "Can you build a ring",
     "I see prefix stripped"),
    ("I see, can you make it yellow", "can you make it yellow",
     "I see with comma stripped"),
    ("I see. Build me a cube", "Build me a cube",
     "I see before build"),
    
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
    ("See, can you make it yello", "can you make it yellow",
     "See stripped + yello → yellow"),
    ("I see. Make it mellow", "make it yellow",
     "I see stripped + mellow → yellow"),
    
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
]

COLOR_NON_INTENT_TESTS = [
    # Should NOT return a color Intent (returns None)
    "build me a box",
    "make me a ring",
    "create a cylinder",
    "make the band thicker",
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
            print(f"  ✓ '{input_text}' correctly NOT detected as color")
            passed += 1
        else:
            print(f"  ✗ '{input_text}' incorrectly detected as color")
            print(f"    Got: {result}")
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
    
    total_pass = w_pass + t_pass + c_pass
    total_fail = w_fail + t_fail + c_fail
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Wake-word normalization:    {w_pass}/{w_pass + w_fail} passed")
    print(f"Full transcript normalize:  {t_pass}/{t_pass + t_fail} passed")
    print(f"Color intent detection:     {c_pass}/{c_pass + c_fail} passed")
    print(f"TOTAL:                      {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
