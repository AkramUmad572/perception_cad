/**
 * PercyAssistant - Hold-to-talk coordinator
 *
 * beginTalk (hold) → record → endTalk (release) → /api/voice → CAD/TTS
 */

import { voiceState } from "./VoiceState.js";
import { PTTRecorder } from "./PTTRecorder.js";

const API_BASE = "";
const SESSION_ID = "default";
const READY_HINT =
  "Hold left trigger / pinch to talk. Right pinch the model to move it.";

export class PercyAssistant {
  constructor(options = {}) {
    this.onModelUpdate = options.onModelUpdate || (() => {});
    this.onStatusMessage = options.onStatusMessage || (() => {});
    this.onTalkingChange = options.onTalkingChange || (() => {});
    this.onPhotoCandidates = options.onPhotoCandidates || (() => {});

    this.recorder = new PTTRecorder();
    this.recorder.onMaxHold = () => this.endTalk();
    this.replyAudio = null;
    this.started = false;
    this._ending = false;
  }

  async start() {
    if (this.started) return;
    this.started = true;
    voiceState.toIdle();
    this.onStatusMessage(`Percy ready. ${READY_HINT}`, true);
    try {
      await this.recorder.ensureMic();
    } catch (e) {
      console.error("[Percy] Mic permission failed:", e);
      voiceState.toError("Microphone access required");
      this.onStatusMessage("Mic access required. Allow and refresh.", false);
    }
  }

  stop() {
    this.recorder.releaseMic();
    this.started = false;
    voiceState.toIdle();
    this.onTalkingChange(false);
  }

  toggleMute() {
    const muted = voiceState.toggleMute();
    if (muted) {
      this.recorder.abort();
      this.onTalkingChange(false);
      this.onStatusMessage("Percy muted", true);
    } else {
      this.onStatusMessage(`Percy unmuted. ${READY_HINT}`, true);
    }
    return muted;
  }

  async beginTalk() {
    if (!this.started || voiceState.isMuted) return;
    if (voiceState.isListening || voiceState.isThinking) return;

    if (this.replyAudio) {
      try {
        this.replyAudio.pause();
      } catch (_) {}
      this.replyAudio = null;
    }

    try {
      await this.recorder.start();
      voiceState.toListening();
      this.onTalkingChange(true);
      this.onStatusMessage("Listening… hold to talk, release to send.", true);
    } catch (e) {
      console.error("[Percy] Failed to start recording:", e);
      voiceState.toError("Microphone access required");
      this.onStatusMessage("Mic access required. Allow and retry.", false);
    }
  }

  async endTalk() {
    if (!voiceState.isListening || this._ending) return;
    this._ending = true;
    this.onTalkingChange(false);

    try {
      const audioBlob = await this.recorder.stop();
      if (!audioBlob) {
        voiceState.toIdle();
        this.onStatusMessage(`Hold a bit longer, then release. ${READY_HINT}`, true);
        return;
      }

      voiceState.toThinking();
      this.onStatusMessage("Looking that up…", true);

      const result = await this._sendVoice(audioBlob);
      await this._handleResponse(result);
    } catch (e) {
      console.error("[Percy] Error in voice pipeline:", e);
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
    } finally {
      this._ending = false;
    }
  }

  async _sendVoice(blob) {
    const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("audio", blob, `utterance.${ext}`);
    form.append("session_id", SESSION_ID);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 180000);

    try {
      const res = await fetch(`${API_BASE}/api/voice`, {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async sendTextCommand(text) {
    if (voiceState.isMuted) return null;

    voiceState.toThinking();
    this.onStatusMessage(`Looking that up… ("${text}")`, true);

    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 180000);
      let res;
      try {
        res = await fetch(`${API_BASE}/api/command`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, session_id: SESSION_ID }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      await this._handleResponse(result);
      return result;
    } catch (e) {
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
      return null;
    }
  }

  async choosePhoto(fileId) {
    voiceState.toThinking();
    this.onStatusMessage("Building that from the photo… this takes a bit.", true);
    try {
      try {
        const confirmRes = await fetch(`${API_BASE}/api/photos/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_id: fileId, session_id: SESSION_ID }),
        });
        if (confirmRes.ok) {
          const confirm = await confirmRes.json();
          if (confirm.reply) this.onStatusMessage(confirm.reply, true);
          if (confirm.reply_audio_url) {
            voiceState.toSpeaking();
            if (this.replyAudio) {
              try { this.replyAudio.pause(); } catch (_) {}
            }
            this.replyAudio = new Audio(confirm.reply_audio_url);
            this.replyAudio.play().catch(() => {});
            this.replyAudio.onended = () => {
              if (voiceState.isSpeaking) voiceState.toThinking();
            };
          }
        }
      } catch (_) {}

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 180000);
      let res;
      try {
        res = await fetch(`${API_BASE}/api/photos/choose`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_id: fileId, session_id: SESSION_ID }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      await this._handleResponse(result);
      return result;
    } catch (e) {
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
      return null;
    }
  }

  async _handleResponse(data) {
    const heard = data.transcript ? `"${data.transcript}" → ` : "";
    const ms = data.latency_ms?.total_ms ? ` (${Math.round(data.latency_ms.total_ms)}ms)` : "";

    if (data.action === "find_photos" && (data.candidates || []).length) {
      this.onPhotoCandidates(data.candidates);
    } else if (data.rebuilt && data.glb_url) {
      this.onPhotoCandidates(null);
    }

    this.onModelUpdate(data);

    if (data.reply_audio_url) {
      voiceState.toSpeaking();
      this.onStatusMessage(`${heard}${data.reply}${ms}`, data.ok);

      try {
        this.replyAudio = new Audio(data.reply_audio_url);
        await new Promise((resolve) => {
          this.replyAudio.onended = resolve;
          this.replyAudio.onerror = resolve;
          this.replyAudio.play().catch(resolve);
        });
      } catch (_) {}

      voiceState.toIdle();
    } else {
      this.onStatusMessage(`${heard}${data.reply}${ms}`, data.ok);
      voiceState.toIdle();
    }
  }

  getState() {
    return voiceState.state;
  }

  isMuted() {
    return voiceState.isMuted;
  }
}
