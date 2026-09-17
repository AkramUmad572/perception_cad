/**
 * VoiceState - State machine for Percy voice assistant
 * States: idle | listening | thinking | speaking | error | muted | wake_tentative | wake_miss
 *
 * Idle = local KWS only (no STT/Gemini/ElevenLabs calls)
 * On wake → VAD listen window → STT/API handoff → back to idle
 *
 * wake_tentative = Wake word detection near threshold (amber pulse)
 * wake_miss = Wake word attempt failed / retrying (brief flash)
 */

export const VoiceStates = Object.freeze({
  IDLE: "idle",
  LISTENING: "listening",
  THINKING: "thinking",
  SPEAKING: "speaking",
  ERROR: "error",
  MUTED: "muted",
  WAKE_TENTATIVE: "wake_tentative",
  WAKE_MISS: "wake_miss",
});

export class VoiceStateManager {
  constructor() {
    this._state = VoiceStates.IDLE;
    this._muted = false;
    this._listeners = new Set();
    this._errorMessage = null;
  }

  get state() {
    return this._muted ? VoiceStates.MUTED : this._state;
  }

  get isMuted() {
    return this._muted;
  }

  get errorMessage() {
    return this._errorMessage;
  }

  get isIdle() {
    return this._state === VoiceStates.IDLE && !this._muted;
  }

  get isListening() {
    return this._state === VoiceStates.LISTENING && !this._muted;
  }

  get isThinking() {
    return this._state === VoiceStates.THINKING;
  }

  get isSpeaking() {
    return this._state === VoiceStates.SPEAKING;
  }

  get isError() {
    return this._state === VoiceStates.ERROR;
  }

  setState(newState, errorMessage = null) {
    const prev = this._state;
    this._state = newState;
    this._errorMessage = errorMessage;
    if (prev !== newState || errorMessage) {
      this._notify();
    }
  }

  setMuted(muted) {
    const prevMuted = this._muted;
    this._muted = !!muted;
    if (prevMuted !== this._muted) {
      this._notify();
    }
  }

  toggleMute() {
    this.setMuted(!this._muted);
    return this._muted;
  }

  toIdle() {
    this.setState(VoiceStates.IDLE);
  }

  toListening() {
    if (!this._muted) {
      this.setState(VoiceStates.LISTENING);
    }
  }

  toThinking() {
    this.setState(VoiceStates.THINKING);
  }

  toSpeaking() {
    this.setState(VoiceStates.SPEAKING);
  }

  toError(message) {
    this.setState(VoiceStates.ERROR, message);
  }

  toWakeTentative() {
    if (!this._muted && this._state === VoiceStates.IDLE) {
      this.setState(VoiceStates.WAKE_TENTATIVE);
    }
  }

  toWakeMiss() {
    if (!this._muted) {
      this.setState(VoiceStates.WAKE_MISS);
    }
  }

  get isWakeTentative() {
    return this._state === VoiceStates.WAKE_TENTATIVE;
  }

  get isWakeMiss() {
    return this._state === VoiceStates.WAKE_MISS;
  }

  subscribe(callback) {
    this._listeners.add(callback);
    return () => this._listeners.delete(callback);
  }

  _notify() {
    const snapshot = {
      state: this.state,
      isMuted: this._muted,
      errorMessage: this._errorMessage,
    };
    for (const cb of this._listeners) {
      try {
        cb(snapshot);
      } catch (e) {
        console.error("VoiceState listener error:", e);
      }
    }
  }
}

export const voiceState = new VoiceStateManager();
