#!/bin/bash
# Run all tests for Percy/Quest smoke bug fixes
#
# Tests:
# 1. Sandbox escape tests (existing)
# 2. STT/wake normalization tests
# 3. Pipeline/color follow-up tests
# 4. Client model update tests

set -e

echo "========================================"
echo "RUNNING ALL PERCY/QUEST TESTS"
echo "========================================"

cd "$(dirname "$0")"

TOTAL_PASS=0
TOTAL_FAIL=0

# Test 1: Sandbox tests (existing)
echo ""
echo ">>> Running Sandbox Tests..."
echo ""
cd backend
if python3 -m cad.test_sandbox; then
    echo "Sandbox tests: PASSED"
else
    echo "Sandbox tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 2: Speech/STT tests
echo ""
echo ">>> Running Speech/STT Tests..."
echo ""
cd backend
if python3 -m voice.test_speech; then
    echo "Speech tests: PASSED"
else
    echo "Speech tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 3: Pipeline tests
echo ""
echo ">>> Running Pipeline Tests..."
echo ""
cd backend
if python3 -m app.test_pipeline; then
    echo "Pipeline tests: PASSED"
else
    echo "Pipeline tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi
cd ..

# Test 4: Client model update tests
echo ""
echo ">>> Running Client Model Update Tests..."
echo ""
if node web-client/src/test_model_update.js; then
    echo "Client tests: PASSED"
else
    echo "Client tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi

# Test 5: VAD timing tests (post-wake grace period)
echo ""
echo ">>> Running VAD Timing Tests..."
echo ""
if node web-client/src/test_vad_timing.js; then
    echo "VAD timing tests: PASSED"
else
    echo "VAD timing tests: FAILED"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi

# Final summary
echo ""
echo "========================================"
echo "FINAL SUMMARY"
echo "========================================"

if [ $TOTAL_FAIL -eq 0 ]; then
    echo "✓ ALL TEST SUITES PASSED"
    exit 0
else
    echo "⚠️  $TOTAL_FAIL TEST SUITE(S) FAILED"
    exit 1
fi
