/**
 * Tests for client-side model update logic.
 *
 * Run with: node web-client/src/test_model_update.js
 *
 * Response contract (per Taha 1715dba, 631905a):
 *   ok, rebuilt, model_id, glb_url, color, reply
 *   Color path always returns fresh model_id/glb_url with rebuilt:true
 *
 * Tests that:
 * 1. rebuilt && model_id && glb_url → load GLB + swap mesh
 * 2. Color-only updates apply without GLB reload (fallback)
 * 3. glb_url change detection as defensive fallback
 * 4. lastLoadedGlbUrl and lastModelId tracking
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
console.log("(Response contract: rebuilt && model_id && glb_url → swap)");
console.log("=".repeat(60));

console.log("\n=== Testing Response Contract (rebuilt && model_id && glb_url) ===");

// Primary contract: rebuilt && model_id && glb_url → load new model
function shouldLoadNewModelContract(data) {
  return !!(data.rebuilt && data.model_id && data.glb_url);
}

// Fallback: glb_url changed (defensive)
function glbUrlChanged(data, lastLoadedGlbUrl) {
  return !!(data.glb_url && data.glb_url !== lastLoadedGlbUrl);
}

// Combined logic matching setModelFromResponse
function shouldLoadNewModel(data, lastLoadedGlbUrl) {
  const needsNewModel = data.rebuilt && data.model_id && data.glb_url;
  const urlChanged = data.glb_url && data.glb_url !== lastLoadedGlbUrl;
  return !!(needsNewModel || urlChanged);
}

test("Contract: rebuilt && model_id && glb_url triggers load", () => {
  const data = { glb_url: "/media/glb/new.glb", model_id: "model_123", rebuilt: true, color: "#FFD700" };
  assert(shouldLoadNewModelContract(data) === true, "Contract should trigger load");
  assert(shouldLoadNewModel(data, "/media/glb/old.glb") === true, "Combined should trigger load");
});

test("Contract: missing model_id does NOT trigger via contract", () => {
  const data = { glb_url: "/media/glb/new.glb", rebuilt: true, color: "#FFD700" };
  assert(shouldLoadNewModelContract(data) === false, "Contract requires model_id");
});

test("Contract: rebuilt=false does NOT trigger via contract", () => {
  const data = { glb_url: "/media/glb/new.glb", model_id: "model_123", rebuilt: false, color: "#FFD700" };
  assert(shouldLoadNewModelContract(data) === false, "Contract requires rebuilt=true");
});

test("Fallback: Different GLB URL triggers load even without rebuilt", () => {
  const data = { glb_url: "/media/glb/new.glb", rebuilt: false, color: "#FFD700" };
  const lastLoaded = "/media/glb/old.glb";
  assert(glbUrlChanged(data, lastLoaded) === true, "URL changed should be detected");
  assert(shouldLoadNewModel(data, lastLoaded) === true, "Fallback should trigger load");
});

test("Same GLB URL with rebuilt=false does NOT trigger load", () => {
  const data = { glb_url: "/media/glb/same.glb", model_id: "model_123", rebuilt: false, color: "#FFD700" };
  const lastLoaded = "/media/glb/same.glb";
  assert(shouldLoadNewModel(data, lastLoaded) === false, "Should not trigger load");
});

test("No GLB URL does not trigger load", () => {
  const data = { color: "#FFD700", rebuilt: false };
  const lastLoaded = "/media/glb/old.glb";
  assert(!shouldLoadNewModel(data, lastLoaded), "Should not trigger load without URL");
});

test("First GLB URL triggers load", () => {
  const data = { glb_url: "/media/glb/first.glb", model_id: "model_001", rebuilt: true, color: "#C0C0C0" };
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

console.log("\n=== Testing Model Tracker (lastLoadedGlbUrl + lastModelId) ===");

class ModelTracker {
  constructor() {
    this.lastLoadedGlbUrl = null;
    this.lastModelId = null;
  }

  processResponse(data) {
    const newGlbUrl = data.glb_url;
    const newModelId = data.model_id;
    
    // Contract: rebuilt && model_id && glb_url → load
    const needsNewModel = data.rebuilt && newModelId && newGlbUrl;
    // Fallback: glb_url changed
    const urlChanged = newGlbUrl && newGlbUrl !== this.lastLoadedGlbUrl;

    if (needsNewModel || urlChanged) {
      this.lastLoadedGlbUrl = newGlbUrl;
      this.lastModelId = newModelId;
      return { action: "load_new_model", url: newGlbUrl, model_id: newModelId };
    } else if (data.color) {
      return { action: "apply_color_only", color: data.color };
    }
    return { action: "none" };
  }
}

test("Tracker loads first model with contract", () => {
  const tracker = new ModelTracker();
  const result = tracker.processResponse({ glb_url: "/a.glb", model_id: "m1", rebuilt: true });
  assert(result.action === "load_new_model", "Action");
  assert(tracker.lastLoadedGlbUrl === "/a.glb", "Tracked URL");
  assert(tracker.lastModelId === "m1", "Tracked model_id");
});

test("Tracker detects same URL as color-only", () => {
  const tracker = new ModelTracker();
  tracker.processResponse({ glb_url: "/a.glb", model_id: "m1", rebuilt: true });
  const result = tracker.processResponse({ glb_url: "/a.glb", model_id: "m1", color: "#FF0000", rebuilt: false });
  assert(result.action === "apply_color_only", "Action should be color only");
});

test("Tracker loads new model via contract (color change with new model)", () => {
  const tracker = new ModelTracker();
  tracker.processResponse({ glb_url: "/a.glb", model_id: "m1", rebuilt: true });
  // Color path now returns fresh model_id/glb_url with rebuilt:true
  const result = tracker.processResponse({ glb_url: "/b.glb", model_id: "m2", color: "#FF0000", rebuilt: true });
  assert(result.action === "load_new_model", "Should load new model for color change");
  assert(tracker.lastModelId === "m2", "Model ID updated");
});

test("Tracker detects URL change as fallback", () => {
  const tracker = new ModelTracker();
  tracker.processResponse({ glb_url: "/a.glb", model_id: "m1", rebuilt: true });
  const result = tracker.processResponse({ glb_url: "/b.glb", color: "#FF0000", rebuilt: false });
  assert(result.action === "load_new_model", "Fallback should load on URL change");
  assert(tracker.lastLoadedGlbUrl === "/b.glb", "Tracked URL updated");
});

test("Tracker handles color-only response (no glb_url)", () => {
  const tracker = new ModelTracker();
  tracker.lastLoadedGlbUrl = "/a.glb";
  tracker.lastModelId = "m1";
  const result = tracker.processResponse({ color: "#00FF00" });
  assert(result.action === "apply_color_only", "Color only");
  assert(tracker.lastLoadedGlbUrl === "/a.glb", "URL unchanged");
  assert(tracker.lastModelId === "m1", "Model ID unchanged");
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
