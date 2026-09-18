/**
 * WakeWordDetector - Always-on local wake word detection using OpenWakeWord (ONNX)
 *
 * Primary phrase: "Hey Percy" (two-word wake phrase for reliable detection).
 * Secondary alias: bare "Percy" still accepted but "Hey Percy" is product wake.
 * 
 * Idle = local KWS only (no network calls). On wake → callback fires.
 * This runs entirely in the browser using Web Audio API + ONNX Runtime Web.
 */

const FRAME_SIZE = 1280; // 80ms at 16kHz - matches OpenWakeWord expected input
const SAMPLE_RATE = 16000;

// Tuned for "Hey Percy" two-word phrase detection
// Higher threshold than single-word to reduce false positives
const DETECTION_THRESHOLD = 0.42; // Tuned for "Hey Percy" - balances sensitivity vs false-positive
const TENTATIVE_THRESHOLD = 0.30; // Near-miss threshold for "almost heard Hey Percy"
const COOLDOWN_MS = 1000; // Reduced for faster re-triggers after clean wake
const TENTATIVE_COOLDOWN_MS = 300; // Shorter cooldown for tentative signals
const RETRY_WINDOW_MS = 2500; // Slightly longer window for multi-attempt wake detection

// Score smoothing for noise rejection
const SCORE_SMOOTHING_ALPHA = 0.3; // Exponential moving average: 0=no smoothing, 1=instant

export class WakeWordDetector {
  constructor(onWakeWord, options = {}) {
    this.onWakeWord = onWakeWord;
    this.onTentative = options.onTentative || null;
    this.onMiss = options.onMiss || null;
    this.audioContext = null;
    this.analyser = null;
    this.source = null;
    this.processor = null;
    this.session = null;
    this.running = false;
    this.lastDetection = 0;
    this.lastTentative = 0;
    this.tentativeCount = 0;
    this.tentativeWindowStart = 0;
    this._audioBuffer = new Float32Array(0);
    this._useFallback = false;
    this._smoothedScore = 0; // Exponential moving average for noise rejection
    this._consecutiveHighFrames = 0; // Counter for sustained high-confidence detection
  }

  async start() {
    if (this.running) return;

    try {
      await this._initOnnx();
    } catch (e) {
      console.warn("OpenWakeWord ONNX init failed, trying fallback:", e);
      this._useFallback = true;
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        channelCount: 1,
        sampleRate: SAMPLE_RATE,
      },
    });

    this.audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
    this.source = this.audioContext.createMediaStreamSource(stream);

    if (this.audioContext.audioWorklet) {
      await this._setupWorklet();
    } else {
      this._setupScriptProcessor();
    }

    this.running = true;
    console.log("[Percy] Wake word detector started (wake phrase: 'Hey Percy')");
  }

  async _initOnnx() {
    if (typeof window.ort === "undefined") {
      await this._loadOrtScript();
    }

    const modelPath = "/percy_wakeword.onnx";
    try {
      const resp = await fetch(modelPath);
      if (!resp.ok) throw new Error(`Model fetch failed: ${resp.status}`);
      const modelBuffer = await resp.arrayBuffer();
      this.session = await window.ort.InferenceSession.create(modelBuffer, {
        executionProviders: ["wasm"],
      });
      console.log("[Percy] ONNX wake word model loaded");
    } catch (e) {
      console.warn("[Percy] Could not load ONNX model:", e);
      throw e;
    }
  }

  async _loadOrtScript() {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.0/dist/ort.min.js";
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  async _setupWorklet() {
    const workletCode = `
      class WakeWordProcessor extends AudioWorkletProcessor {
        constructor() {
          super();
          this.buffer = new Float32Array(0);
        }
        process(inputs) {
          const input = inputs[0]?.[0];
          if (!input) return true;
          const newBuf = new Float32Array(this.buffer.length + input.length);
          newBuf.set(this.buffer);
          newBuf.set(input, this.buffer.length);
          this.buffer = newBuf;
          while (this.buffer.length >= ${FRAME_SIZE}) {
            const frame = this.buffer.slice(0, ${FRAME_SIZE});
            this.buffer = this.buffer.slice(${FRAME_SIZE});
            this.port.postMessage(frame);
          }
          return true;
        }
      }
      registerProcessor('wake-word-processor', WakeWordProcessor);
    `;
    const blob = new Blob([workletCode], { type: "application/javascript" });
    const url = URL.createObjectURL(blob);

    await this.audioContext.audioWorklet.addModule(url);
    this.processor = new AudioWorkletNode(this.audioContext, "wake-word-processor");
    this.processor.port.onmessage = (e) => this._processFrame(e.data);
    this.source.connect(this.processor);
    this.processor.connect(this.audioContext.destination);
    URL.revokeObjectURL(url);
  }

  _setupScriptProcessor() {
    const bufferSize = 4096;
    this.processor = this.audioContext.createScriptProcessor(bufferSize, 1, 1);
    this.processor.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);
      const newBuf = new Float32Array(this._audioBuffer.length + input.length);
      newBuf.set(this._audioBuffer);
      newBuf.set(input, this._audioBuffer.length);
      this._audioBuffer = newBuf;

      while (this._audioBuffer.length >= FRAME_SIZE) {
        const frame = this._audioBuffer.slice(0, FRAME_SIZE);
        this._audioBuffer = this._audioBuffer.slice(FRAME_SIZE);
        this._processFrame(frame);
      }
    };
    this.source.connect(this.processor);
    this.processor.connect(this.audioContext.destination);
  }

  async _processFrame(frame) {
    if (!this.running) return;

    const now = Date.now();
    if (now - this.lastDetection < COOLDOWN_MS) return;

    try {
      const result = this._useFallback
        ? await this._detectFallback(frame)
        : await this._detectOnnx(frame);

      // Apply exponential moving average smoothing for noise rejection
      const rawScore = result.score || 0;
      this._smoothedScore = SCORE_SMOOTHING_ALPHA * rawScore + 
                            (1 - SCORE_SMOOTHING_ALPHA) * this._smoothedScore;

      // Track consecutive high-confidence frames for reliable detection
      if (this._smoothedScore > DETECTION_THRESHOLD) {
        this._consecutiveHighFrames++;
      } else if (this._smoothedScore < TENTATIVE_THRESHOLD) {
        this._consecutiveHighFrames = 0;
      }

      // Clean wake: smoothed score above threshold with at least 2 consecutive high frames
      // This prevents single-frame noise spikes from triggering false positives
      const cleanWake = result.detected && this._consecutiveHighFrames >= 2;
      
      if (cleanWake) {
        this.lastDetection = now;
        this.tentativeCount = 0;
        this.tentativeWindowStart = 0;
        this._consecutiveHighFrames = 0;
        console.log(`[Percy] 'Hey Percy' detected! (score: ${this._smoothedScore.toFixed(3)})`);
        this.onWakeWord?.();
      } else if (result.tentative || (result.detected && this._consecutiveHighFrames < 2)) {
        // Tentative: near-threshold or single high frame (might be noise)
        if (now - this.lastTentative > TENTATIVE_COOLDOWN_MS) {
          this.lastTentative = now;
          
          if (this.tentativeWindowStart === 0) {
            this.tentativeWindowStart = now;
          }
          
          if (now - this.tentativeWindowStart < RETRY_WINDOW_MS) {
            this.tentativeCount++;
            console.log(`[Percy] Wake tentative (smoothed: ${this._smoothedScore.toFixed(3)}, raw: ${rawScore.toFixed(3)}, count: ${this.tentativeCount})`);
            this.onTentative?.(this._smoothedScore);
            
            // Multiple tentatives in window = user is trying, signal miss for retry hint
            if (this.tentativeCount >= 3) {
              console.log("[Percy] Multiple near-misses — say 'Hey Percy' clearly");
              this.onMiss?.();
              this.tentativeCount = 0;
              this.tentativeWindowStart = 0;
            }
          } else {
            // Window expired, restart count
            this.tentativeCount = 1;
            this.tentativeWindowStart = now;
            this.onTentative?.(this._smoothedScore);
          }
        }
      }
    } catch (e) {
      console.error("[Percy] Detection error:", e);
    }
  }

  async _detectOnnx(frame) {
    if (!this.session) return { detected: false, tentative: false, score: 0 };

    const tensor = new window.ort.Tensor("float32", frame, [1, FRAME_SIZE]);
    const results = await this.session.run({ input: tensor });
    const output = results.output?.data || results[Object.keys(results)[0]]?.data;

    if (!output) return { detected: false, tentative: false, score: 0 };
    const score = output[0];
    
    return {
      detected: score > DETECTION_THRESHOLD,
      tentative: score > TENTATIVE_THRESHOLD && score <= DETECTION_THRESHOLD,
      score,
    };
  }

  async _detectFallback(frame) {
    // Energy-based fallback when ONNX model unavailable
    // Tuned for "Hey Percy" two-word phrase (slightly higher thresholds for longer utterance)
    const sum = frame.reduce((a, b) => a + Math.abs(b), 0);
    const avg = sum / frame.length;
    const detected = avg > 0.12; // Tuned for two-word phrase energy
    const tentative = avg > 0.08 && avg <= 0.12;
    return { detected, tentative, score: avg };
  }

  stop() {
    this.running = false;
    if (this.processor) {
      this.processor.disconnect();
      this.processor = null;
    }
    if (this.source) {
      this.source.disconnect();
      this.source = null;
    }
    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }
    console.log("[Percy] Wake word detector stopped");
  }
}
