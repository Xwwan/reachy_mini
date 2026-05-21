# Reachy Dialogue App Models

Place the Silero VAD ONNX model here:

```text
reachy_dialogue_app/models/silero_vad.onnx
```

The automatic voice mode expects a 16 kHz Silero VAD ONNX model with the common
`input`, `state`, and `sr` inputs. Model binaries are ignored by git so local
deployments can pin their own copy.

Download the default model with:

```bash
python scripts/download_silero_vad.py
```

Or override the path at runtime:

```bash
export REACHY_DIALOGUE_VAD_MODEL=/path/to/silero_vad.onnx
```
