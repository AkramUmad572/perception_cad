# Perception CAD

Voice-driven CAD on **Meta Quest 3** via **WebXR passthrough AR** (Quest Browser) + **CadQuery**.

Say a command while **holding** left trigger / pinch → model floats in your **real room** → right-pinch to move/spin.

## Stack

| Piece | Tech |
|---|---|
| Headset UI | Three.js WebXR **immersive-ar** passthrough |
| CAD | CadQuery sandbox (dimensional / printable parts) |
| Mesh | three.ws / NVIDIA TRELLIS (free) or Meshy (optional paid) |
| API | FastAPI |
| Voice | Hold-to-talk (left trigger / pinch) + ElevenLabs STT/TTS |

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
cp .env.example .env  # GEMINI_API_KEY, ELEVENLABS_API_KEY, MESHY_API_KEY
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

6. **Talk**:
   - Hold **left trigger** or **left pinch** (anywhere — not on the model) and speak
   - Release to send. Try: "build me a ring", "make it yellow", "bigger"
   - Model appears in front of you
   - **Right** pinch near the model to grab/move/spin

### Voice Commands

Free-rein: describe any object. CadQuery for dimensional/printable parts; Meshy for characters and organic models.

| Say (while holding to talk) | Result |
|---|---|
| "build me a Pikachu keychain" | CadQuery charm + lug hole |
| "make me a Pikachu" | Meshy sculpted character |
| "build me a 12 tooth gear" | CadQuery |
| "make me a toy car" | Meshy |
| "make the ears longer" | Gemini edits the current CAD script |
| "add a hole for a keychain" | Same CAD object, extra feature |
| "make it twice as big" | CAD: scales dimensions. Mesh: re-sculpts |
| "make it navy" / "paint it gold" | Recolor + rebuild |
| "change the color" | Percy asks which color |

### Controls

- **Talk**: Hold left trigger, left pinch, overlay **Hold** button, or **Space**. Release to send.
- **Mute**: Press **M** (desktop)
- **Grab**: Right pinch near the model, or right controller trigger near the model
- **Move/Spin**: While grabbing, move the right hand

### Troubleshooting

| Issue | Fix |
|---|---|
| "API offline" | Check backend is running on port 8000 |
| Nothing happens on hold | Allow microphone; hold longer than a tap |
| Model not appearing | Look forward after entering AR |
| Certificate error | Accept self-signed cert in Quest Browser |
| Mic blocked | Quest Settings → Apps → Browser → Permissions |

## Desktop Preview

```bash
cd web-client && npm run dev
```

Open **https://localhost:5173** (IWSDK emulator window). Hold **Space** or the **Hold** button to talk. Right-controller trigger near the model to grab.

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
  mesh/          Text-to-3D factories (three.ws, NVIDIA, Meshy)
  ai/            Intent parsing + cad/mesh router
  voice/         STT/TTS (ElevenLabs)

web-client/
  src/
    main.js      WebXR scene + model interaction
    voice/
      VoiceState.js       State machine (idle|listening|thinking|speaking|error|muted)
      PTTRecorder.js      Hold-to-talk MediaRecorder
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
