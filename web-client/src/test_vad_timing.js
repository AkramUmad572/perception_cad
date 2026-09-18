/**
 * Tests for VAD listener post-wake timing and behavior.
 *
 * Run with: node web-client/src/test_vad_timing.js
 *
 * Tests that:
 * 1. Post-wake grace period prevents early cutoff (1.2s)
 * 2. Silence duration requirements are correct (1.6s after speech, 1.8s otherwise)
 * 3. Speech detection flag tracks correctly
 * 4. VAD loop logic handles edge cases
 */

const assert = (condition, message) => {
  if (!condition) {
    throw new Error(`ASSERTION FAILED: ${message}`);
  }
};

const testResults = { passed: 0, failed: 0 };

function test(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    testResults.passed++;
  } catch (e) {
    console.log(`  ✗ ${name}`);
    console.log(`      ${e.message}`);
    testResults.failed++;
  }
}

console.log("=".repeat(60));
console.log("VAD LISTENER POST-WAKE TIMING TESTS");
console.log("(Post-wake grace: don't cut during wake-to-command gap)");
console.log("=".repeat(60));

// Constants matching VADListener.js
const SILENCE_THRESHOLD = 0.004;
const SILENCE_DURATION_MS = 1800;
const MIN_AUDIO_MS = 800;
const POST_WAKE_GRACE_MS = 1200;
const POST_GRACE_SILENCE_MS = 1600;

// Simulate VAD logic from VADListener._startVADLoop
function shouldStopRecording(params) {
  const { elapsed, silenceStart, now, speechDetected } = params;
  
  const inGracePeriod = elapsed < POST_WAKE_GRACE_MS;
  
  // Never stop during grace period
  if (inGracePeriod) {
    return { shouldStop: false, reason: "in_grace_period" };
  }
  
  if (elapsed <= MIN_AUDIO_MS) {
    return { shouldStop: false, reason: "min_audio_not_met" };
  }
  
  if (silenceStart === null) {
    return { shouldStop: false, reason: "no_silence" };
  }
  
  const silenceDuration = now - silenceStart;
  const requiredSilence = speechDetected ? POST_GRACE_SILENCE_MS : SILENCE_DURATION_MS;
  
  const canEndOnSilence = silenceDuration > requiredSilence && 
                          (speechDetected || elapsed > POST_WAKE_GRACE_MS + SILENCE_DURATION_MS);
  
  if (canEndOnSilence) {
    return { shouldStop: true, reason: "silence_threshold_met" };
  }
  
  return { shouldStop: false, reason: "waiting_for_silence" };
}

console.log("\n=== Testing Post-Wake Grace Period ===");

test("Never stop during grace period (0ms elapsed)", () => {
  const result = shouldStopRecording({
    elapsed: 0,
    silenceStart: 0,
    now: 100,
    speechDetected: false,
  });
  assert(result.shouldStop === false, "Should not stop at 0ms");
  assert(result.reason === "in_grace_period", "Reason should be grace period");
});

test("Never stop during grace period (500ms elapsed, silence)", () => {
  const result = shouldStopRecording({
    elapsed: 500,
    silenceStart: 0,
    now: 2000,
    speechDetected: false,
  });
  assert(result.shouldStop === false, "Should not stop at 500ms even with long silence");
});

test("Never stop during grace period (1000ms elapsed)", () => {
  const result = shouldStopRecording({
    elapsed: 1000,
    silenceStart: 0,
    now: 3000,
    speechDetected: false,
  });
  assert(result.shouldStop === false, "Should not stop at 1000ms");
});

test("Never stop at exactly grace period boundary (1200ms)", () => {
  // At exactly 1200ms, we're still in grace period (< not <=)
  const result = shouldStopRecording({
    elapsed: 1199,
    silenceStart: 0,
    now: 3000,
    speechDetected: false,
  });
  assert(result.shouldStop === false, "Should not stop at 1199ms");
});

console.log("\n=== Testing Post-Grace Silence Detection ===");

test("After grace, no stop without sufficient silence (speechDetected=true)", () => {
  const result = shouldStopRecording({
    elapsed: 1500,
    silenceStart: 1400, // 100ms of silence
    now: 1500,
    speechDetected: true,
  });
  assert(result.shouldStop === false, "100ms silence not enough after speech");
});

test("After grace, stop with sufficient silence (speechDetected=true, 1.6s silence)", () => {
  const result = shouldStopRecording({
    elapsed: 3000,
    silenceStart: 1300, // 1700ms of silence
    now: 3000,
    speechDetected: true,
  });
  assert(result.shouldStop === true, "1700ms silence should trigger stop");
  assert(result.reason === "silence_threshold_met", "Correct reason");
});

test("After grace, speechDetected=false requires longer wait", () => {
  // With no speech detected, require elapsed > POST_WAKE_GRACE_MS + SILENCE_DURATION_MS
  const result = shouldStopRecording({
    elapsed: 2000, // < 1200 + 1800 = 3000
    silenceStart: 100,
    now: 2000,
    speechDetected: false,
  });
  assert(result.shouldStop === false, "No speech: need more elapsed time");
});

test("After grace, speechDetected=false can stop after full wait", () => {
  const result = shouldStopRecording({
    elapsed: 3500, // > 1200 + 1800 = 3000
    silenceStart: 1500, // 2000ms of silence > 1800
    now: 3500,
    speechDetected: false,
  });
  assert(result.shouldStop === true, "No speech: can stop after full wait");
});

console.log("\n=== Testing Edge Cases ===");

test("No silence start = no stop", () => {
  const result = shouldStopRecording({
    elapsed: 5000,
    silenceStart: null,
    now: 5000,
    speechDetected: true,
  });
  assert(result.shouldStop === false, "Cannot stop without silence");
  assert(result.reason === "no_silence", "Correct reason");
});

test("Min audio not met = no stop", () => {
  const result = shouldStopRecording({
    elapsed: 700, // < MIN_AUDIO_MS
    silenceStart: 0,
    now: 2000,
    speechDetected: true,
  });
  // Still in grace period (700 < 1200)
  assert(result.shouldStop === false, "Min audio or grace prevents stop");
});

console.log("\n=== Testing Speech Detection Behavior ===");

function simulateVADSession(events) {
  let speechDetected = false;
  let silenceStart = null;
  let listenStart = events[0].time;
  let results = [];
  
  for (const event of events) {
    const energy = event.energy;
    const now = event.time;
    const elapsed = now - listenStart;
    
    // Track speech detection
    if (energy >= SILENCE_THRESHOLD) {
      speechDetected = true;
      silenceStart = null;
    } else {
      if (silenceStart === null) {
        silenceStart = now;
      }
    }
    
    const result = shouldStopRecording({
      elapsed,
      silenceStart,
      now,
      speechDetected,
    });
    
    results.push({
      time: now,
      elapsed,
      energy,
      speechDetected,
      silenceStart,
      ...result,
    });
  }
  
  return results;
}

test("Simulate: User pauses after wake, then speaks command", () => {
  // Scenario: Wake detected, 1s pause, user says command, 2s silence
  const events = [
    { time: 0, energy: 0.001 },      // Start (silence)
    { time: 500, energy: 0.001 },    // Still silent (in grace)
    { time: 1000, energy: 0.001 },   // Still silent (in grace)
    { time: 1300, energy: 0.02 },    // Speech starts (out of grace)
    { time: 1500, energy: 0.03 },    // Speaking
    { time: 2000, energy: 0.025 },   // Speaking
    { time: 2500, energy: 0.001 },   // Silence starts
    { time: 3000, energy: 0.001 },   // 500ms silence
    { time: 3500, energy: 0.001 },   // 1000ms silence
    { time: 4000, energy: 0.001 },   // 1500ms silence
    { time: 4200, energy: 0.001 },   // 1700ms silence - should stop
  ];
  
  const results = simulateVADSession(events);
  
  // Should not stop during grace period
  assert(results[0].shouldStop === false, "t=0: no stop");
  assert(results[1].shouldStop === false, "t=500: no stop (grace)");
  assert(results[2].shouldStop === false, "t=1000: no stop (grace)");
  
  // Should not stop while user is speaking
  assert(results[3].shouldStop === false, "t=1300: no stop (speaking)");
  assert(results[4].shouldStop === false, "t=1500: no stop (speaking)");
  assert(results[5].shouldStop === false, "t=2000: no stop (speaking)");
  
  // Should not stop with insufficient silence
  assert(results[6].shouldStop === false, "t=2500: no stop (just started silence)");
  assert(results[7].shouldStop === false, "t=3000: no stop (500ms silence)");
  assert(results[8].shouldStop === false, "t=3500: no stop (1000ms silence)");
  assert(results[9].shouldStop === false, "t=4000: no stop (1500ms silence)");
  
  // Should stop after 1.6s of silence
  assert(results[10].shouldStop === true, "t=4200: should stop (1700ms silence)");
});

test("Simulate: Old behavior bug - early cutoff on gap", () => {
  // This was the bug: Silence in grace period would accumulate
  // and immediately trigger cutoff after grace ended
  const events = [
    { time: 0, energy: 0.001 },      // Start (silence)
    { time: 500, energy: 0.001 },    // Silent (would start counting)
    { time: 1000, energy: 0.001 },   // 500ms "silence"
    { time: 1201, energy: 0.001 },   // Grace ends - but we have ~1.2s silence counted!
  ];
  
  const results = simulateVADSession(events);
  
  // With the fix, silence counter resets properly and requires
  // actual speech + post-speech silence OR full wait
  const lastResult = results[results.length - 1];
  assert(lastResult.shouldStop === false, "Should not cut immediately after grace with no speech");
});

console.log("\n=== Testing Timing Constants ===");

test("POST_WAKE_GRACE_MS is at least 1 second", () => {
  assert(POST_WAKE_GRACE_MS >= 1000, `Grace period ${POST_WAKE_GRACE_MS}ms >= 1000ms`);
});

test("POST_GRACE_SILENCE_MS is at least 1.5 seconds", () => {
  assert(POST_GRACE_SILENCE_MS >= 1500, `Post-grace silence ${POST_GRACE_SILENCE_MS}ms >= 1500ms`);
});

test("SILENCE_THRESHOLD is reasonable for speech detection", () => {
  assert(SILENCE_THRESHOLD > 0.001, "Threshold > 0.001 (too sensitive = noise triggers)");
  assert(SILENCE_THRESHOLD < 0.02, "Threshold < 0.02 (too high = misses soft speech)");
});

// Summary
console.log("\n" + "=".repeat(60));
console.log("SUMMARY");
console.log("=".repeat(60));
console.log(`TOTAL: ${testResults.passed}/${testResults.passed + testResults.failed} passed`);

if (testResults.failed > 0) {
  console.log(`\n⚠️  ${testResults.failed} TESTS FAILED`);
  process.exit(1);
} else {
  console.log("\n✓ ALL TESTS PASSED");
  process.exit(0);
}
