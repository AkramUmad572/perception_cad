#!/usr/bin/env python3
"""
Regression tests for pipeline: color follow-up response contract.

Run with: python -m app.test_pipeline
Or:       python backend/app/test_pipeline.py

Tests that color-only follow-ups (set_material) ALWAYS return:
- rebuilt=True
- new model_id (fresh, different from session's old model_id)
- new glb_url

A "successful" color change with no new asset would be a client-invisible no-op.
Contract with Tabish: client uses model_id + glb_url to swap mesh.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import Intent, SessionState, CommandResponse
from app.pipeline import apply_intent


def _mock_settings():
    """Create mock settings for testing."""
    settings = MagicMock()
    settings.glb_dir = Path("/tmp/test_glb")
    settings.audio_dir = Path("/tmp/test_audio")
    settings.elevenlabs_api_key = None  # Disable TTS
    return settings


# ============================================================================
# Test 1: Color change on session with last_script MUST return new model
# ============================================================================

async def test_color_change_with_script_returns_new_model():
    """Test that set_material with last_script returns new model_id and glb_url."""
    print("\n=== Test: Color change with script returns new model ===")
    
    session = SessionState(
        session_id="test",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},  # yellow
        reply="Changed to yellow.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return success with new model
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "new_model_456", None)
        
        # Mock synthesize_speech to avoid actual TTS
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            
            # Mock save_session
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    # Assertions
    errors = []
    
    if not result.rebuilt:
        errors.append("rebuilt should be True")
    
    if result.model_id != "new_model_456":
        errors.append(f"model_id should be 'new_model_456', got {result.model_id!r}")
    
    if result.glb_url is None:
        errors.append("glb_url should not be None")
    
    if result.action != "set_material":
        errors.append(f"action should be 'set_material', got {result.action!r}")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Color change correctly returns new model_id, glb_url")
        return 1, 0


# ============================================================================
# Test 2: Color change with NO script/template should fail gracefully
# ============================================================================

async def test_color_change_no_model_fails():
    """Test that set_material with no script or template returns an error."""
    print("\n=== Test: Color change with no model fails gracefully ===")
    
    session = SessionState(
        session_id="test",
        last_script=None,
        template=None,
        model_id=None,
        glb_url=None,
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Changed to yellow.",
    )
    
    settings = _mock_settings()
    
    with patch("app.pipeline.synthesize_speech") as mock_tts:
        mock_tts.return_value = (None, 0.0)
        with patch("app.pipeline.save_session"):
            result = await apply_intent(intent, session, settings)
    
    errors = []
    
    if result.ok:
        errors.append("ok should be False when no model to apply color to")
    
    if result.rebuilt:
        errors.append("rebuilt should be False when no model exists")
    
    if result.error is None:
        errors.append("error should contain a message")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Color change with no model correctly returns error")
        return 1, 0


# ============================================================================
# Test 3: Color change rebuild failure should return error, not silent success
# ============================================================================

async def test_color_change_rebuild_failure_returns_error():
    """Test that set_material rebuild failure returns an error, not silent success."""
    print("\n=== Test: Color change rebuild failure returns error ===")
    
    session = SessionState(
        session_id="test",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="set_material",
        params={"color": "#FFD700"},
        reply="Changed to yellow.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return failure
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (False, None, "Sandbox timeout")
        
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    errors = []
    
    # The key assertion: a rebuild failure should NOT return ok=True
    if result.ok:
        errors.append("ok should be False when rebuild fails")
    
    if result.rebuilt:
        errors.append("rebuilt should be False when rebuild fails")
    
    if result.error is None:
        errors.append("error should contain the failure reason")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Color change rebuild failure correctly returns error")
        return 1, 0


# ============================================================================
# Run All Tests
# ============================================================================

def run_all_tests():
    """Run all pipeline regression tests."""
    print("=" * 60)
    print("PIPELINE REGRESSION TESTS")
    print("(Color follow-up response contract)")
    print("=" * 60)
    
    total_pass = 0
    total_fail = 0
    
    # Run async tests
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        p, f = loop.run_until_complete(test_color_change_with_script_returns_new_model())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_color_change_no_model_fails())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_color_change_rebuild_failure_returns_error())
        total_pass += p
        total_fail += f
    finally:
        loop.close()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"TOTAL: {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
