/**
 * Tests for client-side model update logic.
 *
 * Run with: node web-client/src/test_model_update.js
 *
 * Tests that:
 * 1. setModelFromResponse detects new GLB URLs correctly
 * 2. Color-only updates apply without GLB reload
 * 3. New GLB URL triggers model replacement even if rebuilt=false
 * 4. lastLoadedGlbUrl tracking works correctly
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
console.log("CLIENT MODEL UPDATE LOGIC TESTS");
console.log("=".repeat(60));

console.log("\n=== Testing GLB URL Change Detection ===");

// Simulate the decision logic from setModelFromResponse
function shouldLoadNewModel(data, lastLoadedGlbUrl) {
  const newGlbUrl = data.glb_url;
  return newGlbUrl && (data.rebuilt || newGlbUrl !== lastLoadedGlbUrl);
}

test("New GLB URL with rebuilt=true triggers load", () => {
  const data = { glb_url: "/media/glb/new.glb", rebuilt: true, color: "#FFD700" };
  const lastLoaded = "/media/glb/old.glb";
  assert(shouldLoadNewModel(data, lastLoaded) === true, "Should trigger load");
});

test("Same GLB URL with rebuilt=false does NOT trigger load", () => {
  const data = { glb_url: "/media/glb/same.glb", rebuilt: false, color: "#FFD700" };
  const lastLoaded = "/media/glb/same.glb";
  assert(shouldLoadNewModel(data, lastLoaded) === false, "Should not trigger load");
});

test("Different GLB URL with rebuilt=false DOES trigger load", () => {
  const data = { glb_url: "/media/glb/new.glb", rebuilt: false, color: "#FFD700" };
  const lastLoaded = "/media/glb/old.glb";
  assert(shouldLoadNewModel(data, lastLoaded) === true, "Should trigger load for new URL");
});

test("No GLB URL does not trigger load", () => {
  const data = { color: "#FFD700", rebuilt: false };
  const lastLoaded = "/media/glb/old.glb";
  // data.glb_url is undefined, so newGlbUrl is falsy, shouldLoadNewModel returns falsy
  assert(!shouldLoadNewModel(data, lastLoaded), "Should not trigger load without URL");
});

test("First GLB URL (lastLoaded=null) triggers load", () => {
  const data = { glb_url: "/media/glb/first.glb", rebuilt: true, color: "#C0C0C0" };
  const lastLoaded = null;
  assert(shouldLoadNewModel(data, lastLoaded) === true, "Should trigger first load");
});

console.log("\n=== Testing Color Update Logic ===");

function shouldApplyColorOnly(data, currentModel, needsNewModel) {
  return data.color && currentModel && !needsNewModel;
}

test("Color-only update when model exists and no new GLB", () => {
  const data = { color: "#FFD700", glb_url: "/media/glb/same.glb", rebuilt: false };
  const lastLoaded = "/media/glb/same.glb";
  const currentModel = {}; // Mock model exists
  const needsNew = shouldLoadNewModel(data, lastLoaded);
  assert(shouldApplyColorOnly(data, currentModel, needsNew) === true, "Should apply color only");
});

test("No color-only update when new GLB needed", () => {
  const data = { color: "#FFD700", glb_url: "/media/glb/new.glb", rebuilt: true };
  const lastLoaded = "/media/glb/old.glb";
  const currentModel = {};
  const needsNew = shouldLoadNewModel(data, lastLoaded);
  assert(shouldApplyColorOnly(data, currentModel, needsNew) === false, "Should not apply color only");
});

test("No color-only update when no model exists", () => {
  const data = { color: "#FFD700" };
  const lastLoaded = null;
  const currentModel = null;
  const needsNew = shouldLoadNewModel(data, lastLoaded);
  // currentModel is null, so shouldApplyColorOnly returns falsy
  assert(!shouldApplyColorOnly(data, currentModel, needsNew), "Should not apply without model");
});

console.log("\n=== Testing Response Data Handling ===");

function parseResponseForModelUpdate(data) {
  return {
    hasGlbUrl: !!data.glb_url,
    hasColor: !!data.color,
    isRebuilt: !!data.rebuilt,
    glbUrl: data.glb_url || null,
    color: data.color || null,
  };
}

test("Parse response with all fields", () => {
  const data = { glb_url: "/media/glb/test.glb", color: "#FFD700", rebuilt: true };
  const parsed = parseResponseForModelUpdate(data);
  assert(parsed.hasGlbUrl === true, "hasGlbUrl");
  assert(parsed.hasColor === true, "hasColor");
  assert(parsed.isRebuilt === true, "isRebuilt");
  assert(parsed.glbUrl === "/media/glb/test.glb", "glbUrl");
  assert(parsed.color === "#FFD700", "color");
});

test("Parse response with only color (fast-path)", () => {
  const data = { color: "#FF0000", rebuilt: false };
  const parsed = parseResponseForModelUpdate(data);
  assert(parsed.hasGlbUrl === false, "hasGlbUrl false");
  assert(parsed.hasColor === true, "hasColor true");
  assert(parsed.isRebuilt === false, "isRebuilt false");
});

test("Parse response with glb_url but rebuilt=false (URL change detection)", () => {
  const data = { glb_url: "/media/glb/new.glb", color: "#FFD700", rebuilt: false };
  const parsed = parseResponseForModelUpdate(data);
  assert(parsed.hasGlbUrl === true, "hasGlbUrl true");
  assert(parsed.isRebuilt === false, "isRebuilt false");
});

console.log("\n=== Testing lastLoadedGlbUrl Tracking ===");

class ModelTracker {
  constructor() {
    this.lastLoadedGlbUrl = null;
  }

  processResponse(data) {
    const newGlbUrl = data.glb_url;
    const needsNewModel = newGlbUrl && (data.rebuilt || newGlbUrl !== this.lastLoadedGlbUrl);

    if (needsNewModel) {
      this.lastLoadedGlbUrl = newGlbUrl;
      return { action: "load_new_model", url: newGlbUrl };
    } else if (data.color) {
      return { action: "apply_color_only", color: data.color };
    }
    return { action: "none" };
  }
}

test("Tracker loads first model", () => {
  const tracker = new ModelTracker();
  const result = tracker.processResponse({ glb_url: "/a.glb", rebuilt: true });
  assert(result.action === "load_new_model", "Action");
  assert(tracker.lastLoadedGlbUrl === "/a.glb", "Tracked URL");
});

test("Tracker detects same URL as color-only", () => {
  const tracker = new ModelTracker();
  tracker.processResponse({ glb_url: "/a.glb", rebuilt: true });
  const result = tracker.processResponse({ glb_url: "/a.glb", color: "#FF0000", rebuilt: false });
  assert(result.action === "apply_color_only", "Action should be color only");
});

test("Tracker detects URL change even without rebuilt flag", () => {
  const tracker = new ModelTracker();
  tracker.processResponse({ glb_url: "/a.glb", rebuilt: true });
  const result = tracker.processResponse({ glb_url: "/b.glb", color: "#FF0000", rebuilt: false });
  assert(result.action === "load_new_model", "Action should be load new model");
  assert(tracker.lastLoadedGlbUrl === "/b.glb", "Tracked URL updated");
});

test("Tracker handles color-only response (no glb_url)", () => {
  const tracker = new ModelTracker();
  tracker.lastLoadedGlbUrl = "/a.glb";
  const result = tracker.processResponse({ color: "#00FF00" });
  assert(result.action === "apply_color_only", "Color only");
  assert(tracker.lastLoadedGlbUrl === "/a.glb", "URL unchanged");
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
