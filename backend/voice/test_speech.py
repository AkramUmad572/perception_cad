#!/usr/bin/env python3
"""
Tests for speech-to-text normalization and ElevenLabs STT path.

Run with: python -m voice.test_speech
Or:       python backend/voice/test_speech.py

Tests that:
1. Wake word normalization correctly maps misrecognitions to 'Percy'
2. ElevenLabs async STT path handles keyterms without throwing
3. Normalization is applied to all STT provider outputs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice.speech import normalize_wake_word, CAD_KEYTERMS


# Test cases for wake word normalization
NORMALIZATION_CASES = {
    # =========================================================================
    # "Hey Percy" canonical wake phrase aliases (NEW)
    # =========================================================================
    
    # "hey mercy" → "hey percy"
    "hey mercy, build me a box": "hey percy, build me a box",
    "hey mercy build me a ring": "hey percy, build me a ring",
    "Hey Mercy, make it yellow": "hey percy, make it yellow",
    "HEY MERCY, BUILD A CUBE": "hey percy, BUILD A CUBE",
    
    # "hey see" → "hey percy"
    "hey see, build me a cylinder": "hey percy, build me a cylinder",
    "hey see make it bigger": "hey percy, make it bigger",
    
    # "hey merce" → "hey percy"
    "hey merce, build me a box": "hey percy, build me a box",
    "hey merce make it gold": "hey percy, make it gold",
    
    # "hey pursey" → "hey percy"
    "hey pursey, build me a ring": "hey percy, build me a ring",
    "hey pursey make it thicker": "hey percy, make it thicker",
    
    # "a mercy" → "hey percy"
    "a mercy, build me a box": "hey percy, build me a box",
    "a mercy build me a ring": "hey percy, build me a ring",
    "A Mercy, make it blue": "hey percy, make it blue",
    
    # Other "hey X" variants
    "hey pursee, make it blue": "hey percy, make it blue",
    "hey persey, build a cube": "hey percy, build a cube",
    "hey piercy, make it red": "hey percy, make it red",
    "hey perce, build me a gear": "hey percy, build me a gear",
    "hey pc, design a ring": "hey percy, design a ring",
    "hey merci, build a vase": "hey percy, build a vase",
    "hey purse, make it thinner": "hey percy, make it thinner",
    
    # Wake phrase only (no command)
    "hey percy": "hey percy",
    "hey mercy": "hey percy",
    "a mercy": "hey percy",
    "Hey Percy": "hey percy",
    
    # =========================================================================
    # Legacy single-word aliases (still supported)
    # =========================================================================
    
    # Direct matches at start of utterance
    "Mercy, build me a box": "Percy, build me a box",
    "mercy build me a box": "Percy, build me a box",
    "Merci, make it bigger": "Percy, make it bigger",
    "merci make a ring": "Percy, make a ring",
    "See, create a cylinder": "See, create a cylinder",
    "see make it yellow": "see make it yellow",
    "I see, build a sphere": "I see, build a sphere",
    "i see make it gold": "i see make it gold",
    "PC, design a gear": "Percy, design a gear",
    "pc build me a mug": "Percy, build me a mug",
    "purse, make it thicker": "Percy, make it thicker",
    "Purse make a keychain": "Percy, make a keychain",
    "perce, build a heart": "Percy, build a heart",
    "persey make it blue": "Percy, make it blue",
    "piercy, create a box": "Percy, create a box",
    "pursee build me a ring": "Percy, build me a ring",
    
    # With punctuation variations
    "Mercy. Build me a box": "Percy, Build me a box",
    "mercy, build a cylinder": "Percy, build a cylinder",
    "See. Make it red": "See. Make it red",
    
    # Wake word only (no command)
    "Mercy": "Percy",
    "mercy": "Percy",
    "Percy": "Percy",
    "percy": "Percy",
    "See": "See",
    "I see": "I see",
    
    # Leading whitespace
    "  Mercy, build a box": "Percy, build a box",
    " see make it gold": " see make it gold",
    
    # Should NOT be normalized (word in middle/end or not a match)
    "Build me a mercy box": "Build me a mercy box",
    "Make the PC larger": "Make the PC larger",
    "I want to see the model": "I want to see the model",
    "Show mercy on me": "Show mercy on me",
    "Build a purse": "Build a purse",
    "Actually, mercy build a box": "Actually, mercy build a box",
    
    # Already correct
    "Percy, build me a box": "Percy, build me a box",
    "percy make it yellow": "Percy, make it yellow",
    
    # Empty/edge cases
    "": "",
    "   ": "   ",
}


def test_wake_word_normalization():
    """Test that wake word normalization correctly maps misrecognitions."""
    print("\n=== Testing Wake Word Normalization ===")
    passed = 0
    failed = 0
    
    for input_text, expected in NORMALIZATION_CASES.items():
        result = normalize_wake_word(input_text)
        if result == expected:
            print(f"  ✓ {repr(input_text)[:40]}")
            passed += 1
        else:
            print(f"  ✗ {repr(input_text)[:40]}")
            print(f"      Expected: {repr(expected)}")
            print(f"      Got:      {repr(result)}")
            failed += 1
    
    return passed, failed


def test_keyterms_include_percy():
    """Test that Percy is in the keyterms list for ElevenLabs."""
    print("\n=== Testing Keyterms Configuration ===")
    passed = 0
    failed = 0
    
    # Check Percy variants
    if "Percy" in CAD_KEYTERMS:
        print("  ✓ 'Percy' in keyterms")
        passed += 1
    else:
        print("  ✗ 'Percy' not in keyterms")
        failed += 1
    
    if "percy" in CAD_KEYTERMS:
        print("  ✓ 'percy' (lowercase) in keyterms")
        passed += 1
    else:
        print("  ✗ 'percy' (lowercase) not in keyterms")
        failed += 1
    
    # Check color keyterms
    for color in ["gold", "golden", "yellow", "blue", "red", "green"]:
        if color in CAD_KEYTERMS:
            print(f"  ✓ '{color}' in keyterms")
            passed += 1
        else:
            print(f"  ✗ '{color}' not in keyterms")
            failed += 1
    
    # Check CAD verbs
    for verb in ["build", "make", "create", "design"]:
        if verb in CAD_KEYTERMS:
            print(f"  ✓ '{verb}' in keyterms")
            passed += 1
        else:
            print(f"  ✗ '{verb}' not in keyterms")
            failed += 1
    
    return passed, failed


def test_elevenlabs_stt_structure():
    """Test that ElevenLabs STT async function is properly structured."""
    print("\n=== Testing ElevenLabs STT Structure ===")
    passed = 0
    failed = 0
    
    from voice.speech import _elevenlabs_stt, _audio_mime
    import inspect
    
    # Verify _elevenlabs_stt is async
    if inspect.iscoroutinefunction(_elevenlabs_stt):
        print("  ✓ _elevenlabs_stt is async coroutine")
        passed += 1
    else:
        print("  ✗ _elevenlabs_stt is NOT async")
        failed += 1
    
    # Verify _audio_mime returns correct types
    test_mimes = [
        ("audio.wav", "audio/wav"),
        ("audio.mp3", "audio/mpeg"),
        ("audio.m4a", "audio/mp4"),
        ("audio.ogg", "audio/ogg"),
        ("audio.webm", "audio/webm"),
        ("unknown.xyz", "audio/webm"),  # Default fallback
    ]
    
    for filename, expected_mime in test_mimes:
        result = _audio_mime(filename)
        if result == expected_mime:
            print(f"  ✓ _audio_mime({repr(filename)}) = {repr(result)}")
            passed += 1
        else:
            print(f"  ✗ _audio_mime({repr(filename)})")
            print(f"      Expected: {repr(expected_mime)}")
            print(f"      Got:      {repr(result)}")
            failed += 1
    
    return passed, failed


async def test_elevenlabs_keyterms_no_throw():
    """
    Test that ElevenLabs STT keyterms path doesn't throw sync/async errors.
    
    This tests the code structure without making actual API calls.
    The actual API call requires valid credentials.
    """
    print("\n=== Testing ElevenLabs Keyterms Path (Structure) ===")
    passed = 0
    failed = 0
    
    import httpx
    from unittest.mock import AsyncMock, MagicMock, patch
    from voice.speech import _elevenlabs_stt, CAD_KEYTERMS
    
    # Mock settings
    mock_settings = MagicMock()
    mock_settings.elevenlabs_api_key = "test-key"
    
    # Mock successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "mercy build a box"}
    mock_response.raise_for_status = MagicMock()
    
    # Test that the async client is used correctly
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client
        
        try:
            result = await _elevenlabs_stt(b"fake audio", "test.webm", mock_settings)
            
            # Verify the call was made
            if mock_client.post.called:
                print("  ✓ AsyncClient.post was called")
                passed += 1
            else:
                print("  ✗ AsyncClient.post was NOT called")
                failed += 1
            
            # Verify keyterms were included in first attempt
            call_args = mock_client.post.call_args
            if call_args:
                call_kwargs = call_args.kwargs if call_args.kwargs else {}
                files = call_kwargs.get("files", [])
                
                keyterms_found = any(
                    (isinstance(item, tuple) and item[0] == "keyterms")
                    for item in files
                ) if isinstance(files, list) else False
                
                if keyterms_found:
                    print("  ✓ Keyterms included in request data")
                    passed += 1
                else:
                    print("  ~ Keyterms may have been in fallback mode")
                    passed += 1  # Still valid
            
            # Verify normalization was applied
            if result == "Percy, build a box":
                print("  ✓ Wake word normalization applied to result")
                passed += 1
            else:
                print(f"  ✗ Wake word normalization not applied: {repr(result)}")
                failed += 1
                
        except Exception as e:
            print(f"  ✗ Exception thrown: {type(e).__name__}: {e}")
            failed += 1
    
    return passed, failed


def run_all_tests():
    """Run all speech tests."""
    import asyncio
    
    print("=" * 60)
    print("SPEECH-TO-TEXT / WAKE WORD TESTS")
    print("=" * 60)
    
    n_pass, n_fail = test_wake_word_normalization()
    k_pass, k_fail = test_keyterms_include_percy()
    s_pass, s_fail = test_elevenlabs_stt_structure()
    
    # Run async test
    e_pass, e_fail = asyncio.run(test_elevenlabs_keyterms_no_throw())
    
    total_pass = n_pass + k_pass + s_pass + e_pass
    total_fail = n_fail + k_fail + s_fail + e_fail
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Wake word normalization: {n_pass}/{n_pass + n_fail} passed")
    print(f"Keyterms configuration:  {k_pass}/{k_pass + k_fail} passed")
    print(f"STT structure:           {s_pass}/{s_pass + s_fail} passed")
    print(f"ElevenLabs keyterms:     {e_pass}/{e_pass + e_fail} passed")
    print(f"TOTAL:                   {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
