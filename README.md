# listen.py

Speech-to-text helper designed to automatically play Roblox spelling bee games by listening to system audio, extracting words, and typing them.

---

## What it does

- Continuously listens to audio input (ideally system/loopback audio)
- Uses Vosk for offline speech recognition
- Transforms detected words using `words.json`
- Automatically types the result into the active window
- Optionally allows manual trigger (F12 on Linux)

---

## Requirements

- Python 3
- Packages:
  - `vosk`
  - `sounddevice`
- A Vosk model  
  Download from: https://alphacephei.com/vosk/models

### Linux only
- `ydotool`
- `ydotoold` running
- `libinput` (for F12 detection)

---

## Setup

### 1. Install dependencies
```bash
pip install vosk sounddevice
```

### 2. Download model

Extract to:
```
~/.local/share/vosk/model
```

Or set:
```bash
export VOSK_MODEL=/path/to/model
```

---

## Run
```bash
python listen.py
```

Or specify a model path directly:
```bash
python listen.py /path/to/model
```

---

## Audio Device

The script captures audio from an input device.

### Modes

- **Interactive selection** (default) — lists devices and suggests best option
- **Auto selection** — set `AUTO_SELECT_DEVICE = True`
- **Manual device** — set `AUDIO_DEVICE_ID = 21` (or your device ID)

> **Important:** Use a loopback / monitor device (not a microphone), e.g.:
> - Stereo Mix
> - Monitor of output
> - Virtual audio cable

---

## Controls

- **F12** (Linux only) — manually types the last recognised word
- Otherwise, auto typing runs continuously

---

## Configuration Variables

These are defined at the top of the script.

### Audio

| Variable | Default | Description |
|---|---|---|
| `AUDIO_DEVICE_ID` | `None` | Force a specific device ID |
| `AUTO_SELECT_DEVICE` | `False` | Automatically choose best loopback device |
| `SAMPLE_RATE` | `48000` | Audio sample rate |
| `BLOCK_SIZE` | `2000` | Audio block size |

### Recognition / Behaviour

| Variable | Default | Description |
|---|---|---|
| `STRICT_MODE` | `True` | Only allow known/close words; set `False` to fall back to raw words |
| `AUTO_TYPE` | `True` | Automatically type detected words |
| `MAX_COMBINE_WORDS` | `1` | How many words to combine when matching phrases |

### Model Path

Resolution order:
1. CLI argument
2. `VOSK_MODEL` environment variable
3. Default path (`~/.local/share/vosk/model`)

---

## Word Processing

### `words.json`

Controls word transformations. **A preset list is already included** — you can extend it as needed.
Currently no mappings are set but you can add you're own 
Example structure:
```json
{
  "mappings": {
    "bee": "b",
    "see": "c"
  },
  "targets": [
    "apple",
    "banana"
  ]
}
```

### Behaviour

1. Removes blacklisted words
2. Attempts match in this order:
   - Exact mapping
   - Direct match
   - Fuzzy match
3. Outputs best match, or nothing in strict mode

### Blacklist

Defined in code as `BLACKLIST = {...}`. Words in this list are ignored during processing.

---

## Typing Behaviour

- Simulates human typing with randomised delays
- Presses Enter after typing
- Platform-specific implementation:
  - **Windows** → WinAPI
  - **macOS** → AppleScript
  - **Linux** → `ydotool`

---

## Notes / Limitations

- Accuracy depends heavily on audio quality and correct device selection
- Loopback capture is required for Roblox audio
- Vosk models vary in accuracy and speed
- No context awareness — word-by-word only

---

## Typical Use (Roblox Spelling Bee)

1. Set your system audio output
2. Select loopback/monitor device when prompted
3. Run the script
4. Focus the Roblox window
5. Let the script listen and type answers automatically

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No words detected | Wrong audio device, sample rate mismatch, or model not loaded |
| Wrong words | Adjust `words.json` or disable `STRICT_MODE` |
| No typing (Linux) | Ensure `ydotoold` is running |
| F12 not working | Requires `sudo libinput debug-events` |

---

## File Overview

| File | Description |
|---|---|
| `listen.py` | Main script |
| `words.json` | Word mappings and target list (preset list included)
