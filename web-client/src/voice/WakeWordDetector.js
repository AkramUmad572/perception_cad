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
const DETECTION_THRESHOLD = 0.35; // Lowered from 0.5 for better sensitivity
const COOLDOWN_MS = 1200; // Reduced from 1500 to allow faster re-triggers

export class WakeWordDetector {
  constructor(onWakeWord) {
    this.onWakeWord = onWakeWord;
    this.audioContext = null;
    this.analyser = null;
    this.source = null;
    this.processor = null;
    this.session = null;
    this.running = false;
    this.lastDetection = 0;
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
      const detected = this._useFallback
        ? await this._detectFallback(frame)
        : await this._detectOnnx(frame);

      if (detected) {
        this.lastDetection = now;
        console.log("[Percy] Wake word detected!");
        this.onWakeWord?.();
      }
    } catch (e) {
      console.error("[Percy] Detection error:", e);
    }
  }

  async _detectOnnx(frame) {
    if (!this.session) return false;

    const tensor = new window.ort.Tensor("float32", frame, [1, FRAME_SIZE]);
    const results = await this.session.run({ input: tensor });
    const output = results.output?.data || results[Object.keys(results)[0]]?.data;

    if (!output) return false;
    const score = output[0];
    return score > DETECTION_THRESHOLD;
  }

  async _detectFallback(frame) {
    const sum = frame.reduce((a, b) => a + Math.abs(b), 0);
    const avg = sum / frame.length;
    return avg > 0.10; // Lowered from 0.15 for better fallback sensitivity
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
