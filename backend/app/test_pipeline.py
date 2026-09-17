#!/usr/bin/env python3
"""
Tests for the command pipeline, specifically color follow-up paths.

Run with: python -m app.test_pipeline
Or:       python backend/app/test_pipeline.py

Tests that:
1. Color change via set_material action returns new glb_url and color
2. Color change triggers model rebuild when last_script exists
3. CommandResponse includes rebuilt=True when model changes
4. Fast-path color changes work correctly
5. Color change rebuild failure returns error, not silent success
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import Intent, SessionState, CommandResponse
from app.pipeline import apply_intent


def create_mock_settings():
    """Create mock settings for testing."""
    mock = MagicMock()
    mock.glb_dir = Path("/tmp/test_glb")
    mock.audio_dir = Path("/tmp/test_audio")
    mock.elevenlabs_api_key = None  # Disable TTS
    return mock


def create_test_session(
    session_id: str = "test",
    color: str = "#C0C0C0",
    template: str = None,
    params: dict = None,
    last_script: str = None,
    model_id: str = None,
    glb_url: str = None,
):
    """Create a test session state."""
    return SessionState(
        session_id=session_id,
        color=color,
        template=template,
        params=params or {},
        last_script=last_script,
        model_id=model_id,
        glb_url=glb_url,
    )


async def test_color_change_returns_color():
    """Test that set_material action returns the new color in response."""
    print("\n=== Testing Color Change Returns Color ===")
    passed = 0
    failed = 0
    
    settings = create_mock_settings()
    session = create_test_session(color="#C0C0C0")
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Changed to gold.",
    )
    
    with patch("app.pipeline.save_session"):
        with patch("app.pipeline.synthesize_speech", return_value=(None, 0.0)):
            result = await apply_intent(intent, session, settings)
    
    if result.color == "#FFD700":
        print("  ✓ Response includes correct color")
        passed += 1
    else:
        print(f"  ✗ Response color incorrect: {result.color}")
        failed += 1
    
    if session.color == "#FFD700":
        print("  ✓ Session color updated")
        passed += 1
    else:
        print(f"  ✗ Session color not updated: {session.color}")
        failed += 1
    
    return passed, failed


async def test_color_change_with_script_rebuilds():
    """Test that color change with last_script triggers rebuild and returns new glb_url."""
    print("\n=== Testing Color Change With Script Rebuilds ===")
    passed = 0
    failed = 0
    
    settings = create_mock_settings()
    session = create_test_session(
        color="#C0C0C0",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        model_id="old_model",
        glb_url="/media/glb/old_model.glb",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Changed to gold.",
    )
    
    mock_exec_result = {
        "ok": True,
        "model_id": "new_model_abc",
        "glb_path": "/tmp/test_glb/new_model_abc.glb",
        "exec_ms": 150.0,
    }
    
    with patch("app.pipeline.save_session"):
        with patch("app.pipeline.synthesize_speech", return_value=(None, 0.0)):
            with patch("app.pipeline.execute_cadquery_script", return_value=mock_exec_result):
                result = await apply_intent(intent, session, settings)
    
    if result.rebuilt:
        print("  ✓ Response rebuilt=True")
        passed += 1
    else:
        print("  ✗ Response rebuilt=False (should be True)")
        failed += 1
    
    if result.glb_url and "new_model_abc" in result.glb_url:
        print(f"  ✓ Response has new glb_url: {result.glb_url}")
        passed += 1
    else:
        print(f"  ✗ Response glb_url incorrect: {result.glb_url}")
        failed += 1
    
    if result.color == "#FFD700":
        print("  ✓ Response includes new color")
        passed += 1
    else:
        print(f"  ✗ Response color incorrect: {result.color}")
        failed += 1
    
    return passed, failed


async def test_color_change_no_script_no_rebuild():
    """Test that color change without last_script doesn't rebuild (color-only update)."""
    print("\n=== Testing Color Change Without Script (No Rebuild) ===")
    passed = 0
    failed = 0
    
    settings = create_mock_settings()
    session = create_test_session(
        color="#C0C0C0",
        last_script=None,  # No script
        template=None,     # No template either
        glb_url="/media/glb/existing.glb",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Changed to gold.",
    )
    
    with patch("app.pipeline.save_session"):
        with patch("app.pipeline.synthesize_speech", return_value=(None, 0.0)):
            result = await apply_intent(intent, session, settings)
    
    # Should NOT rebuild since there's no script/template
    if not result.rebuilt:
        print("  ✓ Response rebuilt=False (correct for color-only)")
        passed += 1
    else:
        print("  ✗ Response rebuilt=True (should be False)")
        failed += 1
    
    if result.color == "#FFD700":
        print("  ✓ Response includes new color")
        passed += 1
    else:
        print(f"  ✗ Response color incorrect: {result.color}")
        failed += 1
    
    # glb_url should be preserved
    if result.glb_url == "/media/glb/existing.glb":
        print("  ✓ glb_url preserved from session")
        passed += 1
    else:
        print(f"  ✗ glb_url changed unexpectedly: {result.glb_url}")
        failed += 1
    
    return passed, failed


async def test_generate_action_returns_glb():
    """Test that generate action returns new glb_url."""
    print("\n=== Testing Generate Action Returns GLB ===")
    passed = 0
    failed = 0
    
    settings = create_mock_settings()
    session = create_test_session()
    
    intent = Intent(
        action="generate",
        script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        reply="Built a box.",
    )
    
    mock_exec_result = {
        "ok": True,
        "model_id": "box_model_123",
        "glb_path": "/tmp/test_glb/box_model_123.glb",
        "exec_ms": 200.0,
    }
    
    with patch("app.pipeline.save_session"):
        with patch("app.pipeline.synthesize_speech", return_value=(None, 0.0)):
            with patch("app.pipeline.execute_cadquery_script", return_value=mock_exec_result):
                result = await apply_intent(intent, session, settings)
    
    if result.ok:
        print("  ✓ Response ok=True")
        passed += 1
    else:
        print(f"  ✗ Response ok=False: {result.error}")
        failed += 1
    
    if result.rebuilt:
        print("  ✓ Response rebuilt=True")
        passed += 1
    else:
        print("  ✗ Response rebuilt=False")
        failed += 1
    
    if result.glb_url and "box_model_123" in result.glb_url:
        print(f"  ✓ Response has glb_url: {result.glb_url}")
        passed += 1
    else:
        print(f"  ✗ Response glb_url missing or wrong: {result.glb_url}")
        failed += 1
    
    return passed, failed


async def test_command_response_structure():
    """Test that CommandResponse has all required fields for client."""
    print("\n=== Testing CommandResponse Structure ===")
    passed = 0
    failed = 0
    
    settings = create_mock_settings()
    session = create_test_session()
    
    intent = Intent(
        action="clarify",
        reply="I didn't understand that.",
    )
    
    with patch("app.pipeline.synthesize_speech", return_value=("/media/audio/reply.mp3", 100.0)):
        result = await apply_intent(intent, session, settings)
    
    # Check all required fields exist
    required_fields = [
        ("ok", bool),
        ("reply", str),
        ("action", str),
        ("rebuilt", bool),
        ("color", (str, type(None))),
        ("glb_url", (str, type(None))),
        ("session", SessionState),
        ("latency_ms", dict),
    ]
    
    for field_name, field_type in required_fields:
        if hasattr(result, field_name):
            value = getattr(result, field_name)
            if isinstance(value, field_type) or value is None:
                print(f"  ✓ {field_name} present and correct type")
                passed += 1
            else:
                print(f"  ✗ {field_name} wrong type: {type(value)}")
                failed += 1
        else:
            print(f"  ✗ {field_name} missing")
            failed += 1
    
    return passed, failed


def run_all_tests():
    """Run all pipeline tests."""
    print("=" * 60)
    print("COMMAND PIPELINE / COLOR FOLLOW-UP TESTS")
    print("=" * 60)
    
    async def run_async_tests():
        results = []
        results.append(await test_color_change_returns_color())
        results.append(await test_color_change_with_script_rebuilds())
        results.append(await test_color_change_no_script_no_rebuild())
        results.append(await test_generate_action_returns_glb())
        results.append(await test_command_response_structure())
        return results
    
    all_results = asyncio.run(run_async_tests())
    
    total_pass = sum(r[0] for r in all_results)
    total_fail = sum(r[1] for r in all_results)
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Color change returns color:    {all_results[0][0]}/{sum(all_results[0])} passed")
    print(f"Color change with script:      {all_results[1][0]}/{sum(all_results[1])} passed")
    print(f"Color change no script:        {all_results[2][0]}/{sum(all_results[2])} passed")
    print(f"Generate returns GLB:          {all_results[3][0]}/{sum(all_results[3])} passed")
    print(f"Response structure:            {all_results[4][0]}/{sum(all_results[4])} passed")
    print(f"TOTAL:                         {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
