/**
 * VADListener - Voice Activity Detection for post-wake listening window
 *
 * After "Hey Percy" wake detection, opens a VAD-controlled listen window:
 * - Starts recording immediately
 * - Uses energy-based VAD to detect speech end
 * - Returns audio blob for STT handoff
 * - Auto-closes after timeout or silence
 * 
 * Tuned for natural "Hey Percy, make me a..." command flow.
 */

const SAMPLE_RATE = 16000;
const SILENCE_THRESHOLD = 0.006; // Lowered further - be patient with natural pauses
const SILENCE_DURATION_MS = 1800; // Wait longer before cutting - "Hey Percy" commands have natural flow
const MAX_LISTEN_MS = 18000; // Extended for longer voice commands
const MIN_AUDIO_MS = 800; // Ensure we capture the full "Hey Percy, <command>" utterance

export class VADListener {
  constructor() {
    this.audioContext = null;
    this.source = null;
    this.processor = null;
    this.mediaRecorder = null;
    this.chunks = [];
    this.silenceStart = null;
    this.listenStart = null;
    this.resolve = null;
    this.reject = null;
    this.stream = null;
  }

  async listen() {
    return new Promise(async (resolve, reject) => {
      this.resolve = resolve;
      this.reject = reject;
      this.chunks = [];
      this.silenceStart = null;
      this.listenStart = Date.now();

      try {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            channelCount: 1,
          },
        });

        const mimeType = this._pickMime();
        this.mediaRecorder = mimeType
          ? new MediaRecorder(this.stream, { mimeType })
          : new MediaRecorder(this.stream);

        this.mediaRecorder.ondataavailable = (e) => {
          if (e.data?.size > 0) this.chunks.push(e.data);
        };

        this.mediaRecorder.onstop = () => {
          const blob = new Blob(this.chunks, { type: this.mediaRecorder.mimeType || "audio/webm" });
          this._cleanup();
          if (blob.size < 400) {
            resolve(null);
          } else {
            resolve(blob);
          }
        };

        this.audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
        this.source = this.audioContext.createMediaStreamSource(this.stream);
        this.analyser = this.audioContext.createAnalyser();
        this.analyser.fftSize = 512;
        this.source.connect(this.analyser);

        this.mediaRecorder.start(100);
        this._startVADLoop();

        setTimeout(() => {
          if (this.mediaRecorder?.state === "recording") {
            console.log("[VAD] Max listen time reached");
            this._stopRecording();
          }
        }, MAX_LISTEN_MS);
      } catch (e) {
        this._cleanup();
        reject(e);
      }
    });
  }

  _pickMime() {
    for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"]) {
      if (window.MediaRecorder?.isTypeSupported?.(t)) return t;
    }
    return "";
  }

  _startVADLoop() {
    const dataArray = new Float32Array(this.analyser.fftSize);

    const checkVAD = () => {
      if (!this.mediaRecorder || this.mediaRecorder.state !== "recording") return;

      this.analyser.getFloatTimeDomainData(dataArray);
      const energy = dataArray.reduce((sum, v) => sum + Math.abs(v), 0) / dataArray.length;

      const now = Date.now();
      const elapsed = now - this.listenStart;

      if (energy < SILENCE_THRESHOLD) {
        if (this.silenceStart === null) {
          this.silenceStart = now;
        } else if (now - this.silenceStart > SILENCE_DURATION_MS && elapsed > MIN_AUDIO_MS) {
          console.log("[VAD] Silence detected, stopping");
          this._stopRecording();
          return;
        }
      } else {
        this.silenceStart = null;
      }

      requestAnimationFrame(checkVAD);
    };

    requestAnimationFrame(checkVAD);
  }

  _stopRecording() {
    if (this.mediaRecorder?.state === "recording") {
      try {
        this.mediaRecorder.requestData();
      } catch (_) {}
      try {
        this.mediaRecorder.stop();
      } catch (_) {}
    }
  }

  _cleanup() {
    if (this.source) {
      this.source.disconnect();
      this.source = null;
    }
    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
    this.mediaRecorder = null;
    this.analyser = null;
  }

  abort() {
    this._cleanup();
    this.resolve?.(null);
  }
}
