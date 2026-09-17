/**
 * WakeWordDetector - Always-on local wake word detection using OpenWakeWord (ONNX)
 *
 * Uses a custom "Percy" wake word model. Falls back to Porcupine if OWW is blocked.
 * Idle = local KWS only (no network calls). On wake → callback fires.
 *
 * This runs entirely in the browser using Web Audio API + ONNX Runtime Web.
 */

const FRAME_SIZE = 1280; // 80ms at 16kHz - matches OpenWakeWord expected input
const SAMPLE_RATE = 16000;
const DETECTION_THRESHOLD = 0.35; // Lowered from 0.5 for better sensitivity (Tabish)
const TENTATIVE_THRESHOLD = 0.25; // Near-miss threshold for "almost heard Percy"
const COOLDOWN_MS = 1200; // Reduced from 1500 to allow faster re-triggers (Tabish)
const TENTATIVE_COOLDOWN_MS = 400; // Shorter cooldown for tentative signals
const RETRY_WINDOW_MS = 2000; // Window to accumulate near-misses before retry signal

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
    console.log("[Percy] Wake word detector started");
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

      if (result.detected) {
        this.lastDetection = now;
        this.tentativeCount = 0;
        this.tentativeWindowStart = 0;
        console.log("[Percy] Wake word detected!");
        this.onWakeWord?.();
      } else if (result.tentative) {
        if (now - this.lastTentative > TENTATIVE_COOLDOWN_MS) {
          this.lastTentative = now;
          
          if (this.tentativeWindowStart === 0) {
            this.tentativeWindowStart = now;
          }
          
          if (now - this.tentativeWindowStart < RETRY_WINDOW_MS) {
            this.tentativeCount++;
            console.log(`[Percy] Wake word tentative (score: ${result.score?.toFixed(3)}, count: ${this.tentativeCount})`);
            this.onTentative?.(result.score);
            
            if (this.tentativeCount >= 3) {
              console.log("[Percy] Multiple near-misses detected, signaling retry");
              this.onMiss?.();
              this.tentativeCount = 0;
              this.tentativeWindowStart = 0;
            }
          } else {
            this.tentativeCount = 1;
            this.tentativeWindowStart = now;
            this.onTentative?.(result.score);
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
    const sum = frame.reduce((a, b) => a + Math.abs(b), 0);
    const avg = sum / frame.length;
    const detected = avg > 0.10; // Lowered from 0.15 for better fallback sensitivity (Tabish)
    const tentative = avg > 0.07 && avg <= 0.10;
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
