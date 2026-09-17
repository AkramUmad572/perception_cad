#!/usr/bin/env python3
"""
Regression tests for pipeline: follow-up response contract.

Run with: python -m app.test_pipeline
Or:       python backend/app/test_pipeline.py

Tests that follow-ups (set_material, generate, etc.) ALWAYS return:
- rebuilt=True
- new model_id (fresh, different from session's old model_id)
- new glb_url

A "successful" modification with no new asset would be a client-invisible no-op.
Contract with Tabish: client uses model_id + glb_url to swap mesh.

=== SIZE/GEOMETRY EDITS ===
Scale ("make it bigger") and geometry modifications ("add a hole") use the `generate`
action path — they produce a new CadQuery script and execute it through the sandbox.
This is intentional: any structural change to the model requires full script execution
and MUST return rebuilt=True with fresh model_id/glb_url on success.

See ai/intent.py _check_scale_modify() for scale fast-path that returns Intent(action="generate").
Complex geometry edits go through LLM codegen and also return action="generate".
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
# Test 4: Generate action with script returns new model (size/geometry edits)
# ============================================================================

async def test_generate_action_with_script_returns_new_model():
    """
    Test that generate action with script returns new model_id and glb_url.
    
    SIZE/GEOMETRY FOLLOW-UPS:
    Scale ("make it bigger", "make it smaller") and geometry modifications
    ("add a hole", "make it taller") use the `generate` action path.
    
    This test confirms that action=generate with a valid script:
    - Returns rebuilt=True
    - Returns new model_id
    - Returns glb_url set
    
    This is the SAME contract as color changes — any successful model modification
    MUST return fresh assets so the client can swap the mesh.
    """
    print("\n=== Test: Generate action with script returns new model ===")
    print("  (Used by scale/geometry edits: 'make it bigger', 'add a hole', etc.)")
    
    session = SessionState(
        session_id="test",
        last_script=None,  # Will be set after execution
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    # Intent with action=generate and a valid script (e.g., from scale modification)
    intent = Intent(
        action="generate",
        script='import cadquery as cq\nresult = cq.Workplane("XY").box(15, 15, 7.5)',  # scaled box
        reply="Made it bigger.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return success with new model
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (True, "new_scaled_model_789", None)
        
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    # Verify _execute_with_retry was called with the script
    mock_exec.assert_called_once()
    call_kwargs = mock_exec.call_args
    assert call_kwargs[1]["script"] == intent.script or call_kwargs[0][0] == intent.script
    
    errors = []
    
    if not result.rebuilt:
        errors.append("rebuilt should be True for successful generate action")
    
    if result.model_id != "new_scaled_model_789":
        errors.append(f"model_id should be 'new_scaled_model_789', got {result.model_id!r}")
    
    if result.glb_url is None:
        errors.append("glb_url should not be None")
    
    if result.action != "generate":
        errors.append(f"action should be 'generate', got {result.action!r}")
    
    if not result.ok:
        errors.append(f"ok should be True, got {result.ok!r}")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Generate action correctly returns rebuilt=True, new model_id, glb_url")
        return 1, 0


# ============================================================================
# Test 5: Generate action failure returns error (not silent success)
# ============================================================================

async def test_generate_action_failure_returns_error():
    """
    Test that generate action sandbox failure returns an error, not silent success.
    
    When a geometry edit fails (e.g., invalid CadQuery), the response MUST:
    - Return ok=False
    - Return rebuilt=False
    - Return error message
    - NOT return a stale/old model_id
    """
    print("\n=== Test: Generate action failure returns error ===")
    
    session = SessionState(
        session_id="test",
        last_script='import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 5)',
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="generate",
        script='import cadquery as cq\nresult = cq.Workplane("XY").invalid_op()',  # Bad script
        reply="Making changes.",
    )
    
    settings = _mock_settings()
    
    # Mock _execute_with_retry to return failure
    with patch("app.pipeline._execute_with_retry") as mock_exec:
        mock_exec.return_value = (False, None, "CadQuery error: invalid_op not found")
        
        with patch("app.pipeline.synthesize_speech") as mock_tts:
            mock_tts.return_value = (None, 0.0)
            with patch("app.pipeline.save_session"):
                result = await apply_intent(intent, session, settings)
    
    errors = []
    
    if result.ok:
        errors.append("ok should be False when generate fails")
    
    if result.rebuilt:
        errors.append("rebuilt should be False when generate fails")
    
    if result.error is None:
        errors.append("error should contain the failure reason")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Generate action failure correctly returns error, rebuilt=False")
        return 1, 0


# ============================================================================
# Test 6: Generate action with no script falls back to clarify
# ============================================================================

async def test_generate_action_no_script_returns_clarify():
    """
    Test that generate action with no script returns clarify action.
    
    Edge case: LLM returns action=generate but no script. Should gracefully
    fall back to clarify, not crash or return rebuilt=True with stale data.
    """
    print("\n=== Test: Generate action with no script returns clarify ===")
    
    session = SessionState(
        session_id="test",
        model_id="old_model_123",
        glb_url="/media/glb/old_model_123.glb",
        color="#C0C0C0",
    )
    
    intent = Intent(
        action="generate",
        script=None,  # No script provided
        reply="I'll build that.",
    )
    
    settings = _mock_settings()
    
    with patch("app.pipeline.synthesize_speech") as mock_tts:
        mock_tts.return_value = (None, 0.0)
        with patch("app.pipeline.save_session"):
            result = await apply_intent(intent, session, settings)
    
    errors = []
    
    if result.rebuilt:
        errors.append("rebuilt should be False when no script")
    
    if result.action != "clarify":
        errors.append(f"action should be 'clarify', got {result.action!r}")
    
    if errors:
        print("  ✗ FAILED:")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    else:
        print("  ✓ Generate with no script correctly returns clarify, rebuilt=False")
        return 1, 0


# ============================================================================
# Run All Tests
# ============================================================================

def run_all_tests():
    """Run all pipeline regression tests."""
    print("=" * 60)
    print("PIPELINE REGRESSION TESTS")
    print("(Color + Size/Geometry follow-up response contract)")
    print("=" * 60)
    
    total_pass = 0
    total_fail = 0
    
    # Run async tests
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Color change tests (existing)
        p, f = loop.run_until_complete(test_color_change_with_script_returns_new_model())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_color_change_no_model_fails())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_color_change_rebuild_failure_returns_error())
        total_pass += p
        total_fail += f
        
        # Generate action tests (new - size/geometry edits)
        p, f = loop.run_until_complete(test_generate_action_with_script_returns_new_model())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_generate_action_failure_returns_error())
        total_pass += p
        total_fail += f
        
        p, f = loop.run_until_complete(test_generate_action_no_script_returns_clarify())
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
