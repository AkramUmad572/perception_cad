# Perception CAD

Voice-driven CAD on **Meta Quest 3** via **WebXR passthrough AR** (Quest Browser) + **CadQuery**.

Say “build me a ring” → model floats in your **real room** → pinch to move/spin → “make it yellow”.

## Stack

| Piece | Tech |
|---|---|
| Headset UI | Three.js WebXR **immersive-ar** passthrough |
| CAD | CadQuery (conda) with trimesh fallback |
| API | FastAPI |
| STT + TTS | ElevenLabs |

## Quest quick start

1. Same hotspot/Wi‑Fi as the Quest. On the Mac:

```bash
conda activate perception_cad
cd backend && PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
cd web-client && npm run dev:https
```

2. Quest Browser → `https://YOUR_MAC_IP:5173` (accept cert) → **START AR** / **Enter AR**.

3. Controls:
   - **Hold to talk** → speak → **release** (auto-sends — no Send button)
   - Left-hand pinch or controller squeeze = talk
   - Right-hand pinch **near the model** = grab / move / spin

## Mac preview

```bash
cd web-client && npm run dev
```

Open **http://localhost:5173**.

## Layout

```
backend/       FastAPI + CadQuery + voice
web-client/    Three.js passthrough WebXR app
```
