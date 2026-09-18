/**
 * Hold-to-talk recorder. Starts on press, stops on release.
 * No silence-VAD — the user decides when the utterance ends.
 */

const MAX_HOLD_MS = 18000;

export class PTTRecorder {
  constructor() {
    this.stream = null;
    this.mediaRecorder = null;
    this.chunks = [];
    this.maxTimer = null;
    this.recording = false;
    this.onMaxHold = null;
  }

  async ensureMic() {
    if (this.stream?.active) return this.stream;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        channelCount: 1,
      },
    });
    return this.stream;
  }

  async start() {
    if (this.recording) return;
    this.chunks = [];
    const stream = await this.ensureMic();
    const mimeType = this._pickMime();
    this.mediaRecorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);

    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data?.size > 0) this.chunks.push(e.data);
    };

    this.recording = true;
    this.mediaRecorder.start(100);

    this.maxTimer = setTimeout(() => {
      if (this.recording) {
        console.log("[PTT] Max hold time reached");
        this.onMaxHold?.();
      }
    }, MAX_HOLD_MS);
  }

  stop() {
    if (!this.recording) {
      return Promise.resolve(null);
    }
    this.recording = false;
    if (this.maxTimer) {
      clearTimeout(this.maxTimer);
      this.maxTimer = null;
    }

    return new Promise((resolve) => {
      const recorder = this.mediaRecorder;
      if (!recorder || recorder.state === "inactive") {
        this.mediaRecorder = null;
        resolve(null);
        return;
      }

      recorder.onstop = () => {
        const blob = new Blob(this.chunks, {
          type: recorder.mimeType || "audio/webm",
        });
        this.mediaRecorder = null;
        this.chunks = [];
        resolve(blob.size < 400 ? null : blob);
      };

      try {
        recorder.requestData();
      } catch (_) {}
      try {
        recorder.stop();
      } catch (_) {
        this.mediaRecorder = null;
        resolve(null);
      }
    });
  }

  abort() {
    this.recording = false;
    if (this.maxTimer) {
      clearTimeout(this.maxTimer);
      this.maxTimer = null;
    }
    if (this.mediaRecorder?.state === "recording") {
      try {
        this.mediaRecorder.stop();
      } catch (_) {}
    }
    this.mediaRecorder = null;
    this.chunks = [];
  }

  releaseMic() {
    this.abort();
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
  }

  _pickMime() {
    for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"]) {
      if (window.MediaRecorder?.isTypeSupported?.(t)) return t;
    }
    return "";
  }
}
