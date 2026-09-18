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
 * 
 * POST-WAKE GRACE PERIOD:
 * After wake word detection, there's typically a gap before the user speaks
 * their command. We must NOT end on this gap. The grace period prevents
 * early silence cutoff, ensuring we capture the full command.
 */

const SAMPLE_RATE = 16000;
const SILENCE_THRESHOLD = 0.004; // Lower threshold - more forgiving of soft speech / natural pauses
const SILENCE_DURATION_MS = 1800; // Silence duration required to end (after grace period)
const MAX_LISTEN_MS = 18000; // Extended for longer voice commands
const MIN_AUDIO_MS = 800; // Absolute minimum audio length

// Post-wake grace period: don't allow silence-based end for this duration after wake
// This ensures we don't cut off during the gap between "Hey Percy" and the command
const POST_WAKE_GRACE_MS = 1200; // ~1.2s grace before silence can close the window

// After grace period, require longer sustained silence to confirm end-of-utterance
// This prevents mid-phrase cutoffs from brief pauses
const POST_GRACE_SILENCE_MS = 1600; // Require 1.6s of silence after grace to end

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
    this._speechDetected = false; // Track if we've heard any speech after wake
  }

  async listen() {
    return new Promise(async (resolve, reject) => {
      this.resolve = resolve;
      this.reject = reject;
      this.chunks = [];
      this.silenceStart = null;
      this.listenStart = Date.now();
      this._speechDetected = false;

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
      const inGracePeriod = elapsed < POST_WAKE_GRACE_MS;

      // Track if we've detected speech (helps distinguish silence vs no-speech-yet)
      if (energy >= SILENCE_THRESHOLD) {
        this._speechDetected = true;
        this.silenceStart = null;
      } else {
        // Energy below threshold (silence)
        if (this.silenceStart === null) {
          this.silenceStart = now;
        }
      }

      // Determine if we should stop recording based on silence
      // Rules:
      // 1. Never stop during post-wake grace period (wait for command)
      // 2. After grace, require speech detected + sustained silence to end
      // 3. Use longer silence threshold after grace to avoid mid-phrase cuts
      if (!inGracePeriod && elapsed > MIN_AUDIO_MS && this.silenceStart !== null) {
        const silenceDuration = now - this.silenceStart;
        const requiredSilence = this._speechDetected ? POST_GRACE_SILENCE_MS : SILENCE_DURATION_MS;
        
        // Only stop if we've had sustained silence AND (detected speech OR waited long enough)
        const canEndOnSilence = silenceDuration > requiredSilence && 
                                (this._speechDetected || elapsed > POST_WAKE_GRACE_MS + SILENCE_DURATION_MS);
        
        if (canEndOnSilence) {
          console.log(`[VAD] Silence detected (${silenceDuration}ms), speechDetected=${this._speechDetected}, stopping`);
          this._stopRecording();
          return;
        }
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
    this._speechDetected = false;
  }

  abort() {
    this._cleanup();
    this.resolve?.(null);
  }
}
