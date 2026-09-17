# Perception CAD

Voice-driven CAD on **Meta Quest 3** via **WebXR passthrough AR** (Quest Browser) + **CadQuery**.

Say **"Percy"** → speak your command → model floats in your **real room** → pinch to move/spin.

## Stack

| Piece | Tech |
|---|---|
| Headset UI | Three.js WebXR **immersive-ar** passthrough |
| CAD | CadQuery (conda) with sandboxed execution |
| API | FastAPI |
| Voice | Percy wake word + VAD + ElevenLabs STT/TTS |

## Quest Testing

### Prerequisites

1. **Same network**: Quest and dev machine on same Wi-Fi/hotspot
2. **Backend running** with API keys configured
3. **HTTPS** for Quest Browser (WebXR requires secure context)

### Step-by-step

1. **Start backend** (on dev machine):

```bash
conda activate perception_cad
cd backend
cp .env.example .env  # Edit with your API keys
PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000
```

2. **Start web client with HTTPS**:

```bash
cd web-client
npm install
npm run dev:https
```

3. **Find your IP**: `ifconfig | grep inet` or check network settings

4. **On Quest Browser**: Navigate to `https://YOUR_IP:5173`
   - Accept the self-signed certificate warning
   - Allow microphone access when prompted

5. **Tap "Enter AR"** to start passthrough mode

6. **Test Percy**:
   - Say **"Percy"** (wake word) - status should change to "Listening..."
   - Give a command: "build me a ring", "make it yellow", "bigger"
   - Model appears in front of you
   - Pinch near the model to grab/move/spin

### Voice Commands

| Say | Result |
|---|---|
| "Percy, build me a ring" | Creates a ring |
| "Percy, make it yellow" | Changes color |
| "Percy, bigger" / "smaller" | Scales the model |
| "Percy, thicker" / "thinner" | Adjusts dimensions |
| "Percy, build a box" | Creates a box |
| "Percy, cylinder" | Creates a cylinder |

### Controls

- **Voice**: Say "Percy" to activate, then speak command
- **Mute**: Press **M** key (desktop) to toggle Percy on/off
- **Grab**: Pinch near the model (hand tracking) or controller select
- **Move/Spin**: While grabbing, move hand to reposition

### Troubleshooting

| Issue | Fix |
|---|---|
| "API offline" | Check backend is running on port 8000 |
| No wake word | Check mic permissions, try refreshing |
| Model not appearing | Look forward after entering AR |
| Certificate error | Accept self-signed cert in Quest Browser |
| Mic blocked | Quest Settings → Apps → Browser → Permissions |

## Desktop Preview

```bash
cd web-client && npm run dev
```

Open **http://localhost:5173**. Percy works on desktop too (say "Percy" or press M to mute).

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Check system status |
| `/api/command` | POST | Text command (JSON: `{text, session_id}`) |
| `/api/voice` | POST | Voice command (multipart: audio file) |
| `/api/script` | POST | Execute CadQuery script (JSON: `{script, session_id, color}`) |
| `/api/session/{id}` | GET | Get session state |

### Script Execution (for codegen integration)

```bash
curl -X POST http://localhost:8000/api/script \
  -H "Content-Type: application/json" \
  -d '{
    "script": "import cadquery as cq\nresult = cq.Workplane(\"XY\").box(20, 20, 10)",
    "session_id": "default",
    "color": "#FFD700"
  }'
```

Scripts run in a sandbox with:
- 30s timeout (kills hung execution)
- No filesystem access
- No network access  
- Non-manifold mesh rejection
- Memory limits

## Architecture

```
backend/
  app/           FastAPI routes + pipeline
  cad/           CadQuery builder + sandbox
  ai/            Intent parsing (Gemini/OpenAI)
  voice/         STT/TTS (ElevenLabs)

web-client/
  src/
    main.js      WebXR scene + model interaction
    voice/
      VoiceState.js       State machine (idle|listening|thinking|speaking|error|muted)
      WakeWordDetector.js  OpenWakeWord "Percy" ONNX
      VADListener.js       Voice activity detection
      PercyAssistant.js    Orchestrator
```

## VoiceState Hooks (for HUD integration)

The client exposes `window.voiceState` for in-world HUD:

```javascript
import { voiceState, VoiceStates } from './voice/VoiceState.js';

// Subscribe to state changes
voiceState.subscribe(({ state, isMuted, errorMessage }) => {
  // state: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error' | 'muted'
  updateHudIndicator(state);
});

// Check current state
if (voiceState.isListening) { /* show recording indicator */ }

// Toggle mute
voiceState.toggleMute();
```

## Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Required for voice
ELEVENLABS_API_KEY=your_key

# LLM for intent parsing (one of)
GEMINI_API_KEY=your_key
OPENAI_API_KEY=your_key

# Optional: better STT
DEEPGRAM_API_KEY=your_key
```
