/**
 * PercyAssistant - Main voice assistant coordinator
 *
 * Orchestrates: WakeWordDetector → VADListener → API handoff
 * Manages VoiceState transitions: idle → listening → thinking → speaking → idle
 */

import { voiceState, VoiceStates } from "./VoiceState.js";
import { WakeWordDetector } from "./WakeWordDetector.js";
import { VADListener } from "./VADListener.js";

const API_BASE = "";
const SESSION_ID = "default";

export class PercyAssistant {
  constructor(options = {}) {
    this.onModelUpdate = options.onModelUpdate || (() => {});
    this.onStatusMessage = options.onStatusMessage || (() => {});

    this.wakeDetector = null;
    this.vadListener = new VADListener();
    this.replyAudio = null;
    this.started = false;
  }

  async start() {
    if (this.started) return;

    try {
      this.wakeDetector = new WakeWordDetector(() => this._onWakeWord());
      await this.wakeDetector.start();
      this.started = true;
      voiceState.toIdle();
      this.onStatusMessage("Percy ready. Say 'Percy' to activate.", true);
    } catch (e) {
      console.error("[Percy] Failed to start:", e);
      voiceState.toError("Microphone access required");
      this.onStatusMessage("Mic access required. Allow and refresh.", false);
    }
  }

  stop() {
    if (this.wakeDetector) {
      this.wakeDetector.stop();
      this.wakeDetector = null;
    }
    this.vadListener.abort();
    this.started = false;
    voiceState.toIdle();
  }

  toggleMute() {
    const muted = voiceState.toggleMute();
    if (muted) {
      this.onStatusMessage("Percy muted", true);
    } else {
      this.onStatusMessage("Percy unmuted. Say 'Percy' to activate.", true);
    }
    return muted;
  }

  async _onWakeWord() {
    if (voiceState.isMuted) return;
    if (!voiceState.isIdle) return;

    console.log("[Percy] Wake word triggered");
    voiceState.toListening();
    this.onStatusMessage("Listening...", true);

    if (this.replyAudio) {
      try {
        this.replyAudio.pause();
      } catch (_) {}
      this.replyAudio = null;
    }

    try {
      const audioBlob = await this.vadListener.listen();
      if (!audioBlob) {
        voiceState.toIdle();
        this.onStatusMessage("Didn't catch that. Say 'Percy' again.", true);
        return;
      }

      voiceState.toThinking();
      this.onStatusMessage("Processing...", true);

      const result = await this._sendVoice(audioBlob);
      await this._handleResponse(result);
    } catch (e) {
      console.error("[Percy] Error in voice pipeline:", e);
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
    }
  }

  async _sendVoice(blob) {
    const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("audio", blob, `utterance.${ext}`);
    form.append("session_id", SESSION_ID);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 90000);

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
    this.onStatusMessage(`Processing: "${text}"...`, true);

    try {
      const res = await fetch(`${API_BASE}/api/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, session_id: SESSION_ID }),
      });
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
