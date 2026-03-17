#!/usr/bin/env python3
import sys
import os
import time
import random
import json
import queue
import signal
import subprocess
import threading
import difflib
import platform
import sounddevice as sd
from vosk import Model, KaldiRecognizer

# ─────────────────────────────────────────────────────────────
#  AUDIO DEVICE CONFIGURATION
#
#  Set AUDIO_DEVICE_ID to an integer to use a specific device.
#  Set AUDIO_DEVICE_ID to None to auto-select or prompt.
#
#  AUTO_SELECT_DEVICE:
#    True  → intelligently pick the best loopback/monitor device silently
#    False → show a menu and let the user choose
# ─────────────────────────────────────────────────────────────
AUDIO_DEVICE_ID     = None   # e.g. 21, or None to auto/prompt
AUTO_SELECT_DEVICE  = False   # True = smart auto-pick, False = interactive menu

SAMPLE_RATE       = 48000
BLOCK_SIZE        = 2000
STRICT_MODE       = True
AUTO_TYPE         = True
MAX_COMBINE_WORDS = 1
MODEL_PATH        = (
    sys.argv[1] if len(sys.argv) > 1
    else os.environ.get("VOSK_MODEL", os.path.expanduser("~/.local/share/vosk/model"))
)

OS = platform.system()

def _score_device(dev: dict) -> int:
    name  = dev.get("name", "").lower()
    score = 0

    if dev.get("max_input_channels", 0) < 1:
        return -1

    reward = ["monitor", "loopback", "stereo mix", "what u hear", "wave out", "output",
              "speaker", "playback", "virtual", "cable", "sum", "mix"]
    for kw in reward:
        if kw in name:
            score += 10

    penalise = ["mic", "microphone", "headset", "webcam", "capture", "record",
                "built-in input", "internal mic", "array", "input only"]
    for kw in penalise:
        if kw in name:
            score -= 10

    default_sr = dev.get("default_samplerate", 0)
    if default_sr == SAMPLE_RATE:
        score += 3
    elif abs(default_sr - SAMPLE_RATE) <= 8000:
        score += 1

    return score


def _auto_select_device() -> int:
    devices  = sd.query_devices()
    best_id  = None
    best_sc  = -999

    for idx, dev in enumerate(devices):
        sc = _score_device(dev)
        if sc > best_sc:
            best_sc = sc
            best_id = idx

    if best_id is None:
        print("[listen.py] No suitable loopback/monitor device found.", file=sys.stderr)
        sys.exit(1)

    dev_name = devices[best_id]["name"]
    print(f"[listen.py] Auto-selected device {best_id}: {dev_name!r} (score={best_sc})", file=sys.stderr)
    return best_id


def _interactive_select_device() -> int:
    devices     = sd.query_devices()
    input_devs  = [(i, d) for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]

    if not input_devs:
        print("[listen.py] No input-capable devices found.", file=sys.stderr)
        sys.exit(1)

    scored = sorted(input_devs, key=lambda x: _score_device(x[1]), reverse=True)
    suggested_id = scored[0][0]

    print("\n[listen.py] Available input-capable devices:", file=sys.stderr)
    print(f"  {'ID':>3}  {'Channels':>8}  {'Sample Rate':>12}  Name", file=sys.stderr)
    print(f"  {'─'*3}  {'─'*8}  {'─'*12}  {'─'*40}", file=sys.stderr)
    for idx, dev in input_devs:
        marker = " ← suggested (loopback/monitor)" if idx == suggested_id else ""
        print(
            f"  {idx:>3}  {dev['max_input_channels']:>8}  "
            f"{int(dev['default_samplerate']):>12}  {dev['name']}{marker}",
            file=sys.stderr,
        )

    print(f"\nPress Enter to use the suggested device [{suggested_id}], or type an ID: ", end="", file=sys.stderr)
    sys.stderr.flush()
    try:
        raw = input().strip()
    except EOFError:
        raw = ""

    if raw == "":
        chosen = suggested_id
    else:
        try:
            chosen = int(raw)
            valid_ids = {i for i, _ in input_devs}
            if chosen not in valid_ids:
                raise ValueError
        except ValueError:
            print(f"[listen.py] Invalid choice — using suggested device {suggested_id}.", file=sys.stderr)
            chosen = suggested_id

    dev_name = devices[chosen]["name"]
    print(f"[listen.py] Using device {chosen}: {dev_name!r}", file=sys.stderr)
    return chosen


def resolve_audio_device() -> int:
    if AUDIO_DEVICE_ID is not None:
        devices = sd.query_devices()
        name = devices[AUDIO_DEVICE_ID]["name"] if AUDIO_DEVICE_ID < len(devices) else "unknown"
        print(f"[listen.py] Using configured device {AUDIO_DEVICE_ID}: {name!r}", file=sys.stderr)
        return AUDIO_DEVICE_ID

    if AUTO_SELECT_DEVICE:
        return _auto_select_device()
    else:
        return _interactive_select_device()


DEVICE_ID = resolve_audio_device()

# ─────────────────────────────────────────────────────────────
#  PLATFORM CHECKS
# ─────────────────────────────────────────────────────────────

if OS == "Linux":
    try:
        subprocess.run(["ydotool", "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[listen.py] ydotool not found or not working. Install it and ensure ydotoold is running.", file=sys.stderr)
        sys.exit(1)

if not os.path.isdir(MODEL_PATH):
    print(
        f"[listen.py] Vosk model not found at: {MODEL_PATH}\n"
        "Download a small English model from https://alphacephei.com/vosk/models\n"
        "and extract it to ~/.local/share/vosk/model\n"
        "Or set the VOSK_MODEL environment variable to the model directory.",
        file=sys.stderr,
    )
    sys.exit(1)

model = Model(MODEL_PATH)

# ─────────────────────────────────────────────────────────────
#  SHARED STATE
# ─────────────────────────────────────────────────────────────

audio_queue: queue.Queue = queue.Queue()
done = threading.Event()

last_word      = ""
last_word_lock = threading.Lock()

recognizer_lock = threading.Lock()
recognizer      = KaldiRecognizer(model, SAMPLE_RATE)

def new_recognizer() -> KaldiRecognizer:
    return KaldiRecognizer(model, SAMPLE_RATE)

# ─────────────────────────────────────────────────────────────
#  AUDIO CALLBACK
# ─────────────────────────────────────────────────────────────

def audio_callback(indata, frames, time, status):
    if status:
        print(f"[sounddevice] {status}", file=sys.stderr)
    audio_queue.put(bytes(indata))

# ─────────────────────────────────────────────────────────────
#  TYPING
# ─────────────────────────────────────────────────────────────

def type_text(text: str) -> None:
    if not text:
        print("[listen.py] Nothing to type.", file=sys.stderr)
        return

    try:
        if OS == "Windows":
            import ctypes
            import ctypes.wintypes

            SendInput = ctypes.windll.user32.SendInput

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk",         ctypes.wintypes.WORD),
                    ("wScan",       ctypes.wintypes.WORD),
                    ("dwFlags",     ctypes.wintypes.DWORD),
                    ("time",        ctypes.wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class INPUT(ctypes.Structure):
                class _INPUT(ctypes.Union):
                    _fields_ = [("ki", KEYBDINPUT)]
                _anonymous_ = ("_input",)
                _fields_ = [("type", ctypes.wintypes.DWORD), ("_input", _INPUT)]

            KEYEVENTF_UNICODE = 0x0004
            KEYEVENTF_KEYUP   = 0x0002
            INPUT_KEYBOARD    = 1

            for char in text:
                for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                    inp = INPUT(
                        type=INPUT_KEYBOARD,
                        ki=KEYBDINPUT(wVk=0, wScan=ord(char), dwFlags=flags, time=0, dwExtraInfo=None),
                    )
                    SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
                time.sleep(random.uniform(0.15, 0.30))

            VK_RETURN = 0x0D
            for flags in (0, 0x0002):
                inp = INPUT(
                    type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None),
                )
                SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

        elif OS == "Darwin":
            script = f'tell application "System Events" to keystroke "{text}"'
            subprocess.run(["osascript", "-e", script], check=True)
            subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 36'], check=True)

        else:
            for char in text:
                subprocess.run(["ydotool", "type", "--", char], check=True)
                time.sleep(random.uniform(0.15, 0.30))
            subprocess.run(["ydotool", "key", "28:1", "28:0"], check=True)

        print(f"[listen.py] Typed: {text!r}", file=sys.stderr)

    except FileNotFoundError as e:
        print(f"[listen.py] Typing tool not found: {e}", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"[listen.py] Typing tool error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[listen.py] Unexpected typing error: {e}", file=sys.stderr)

# ─────────────────────────────────────────────────────────────
#  F12 WATCHER
# ─────────────────────────────────────────────────────────────

def watch_f12() -> None:
    if OS != "Linux":
        print("[listen.py] F12 watcher only supported on Linux.", file=sys.stderr)
        return

    try:
        proc = subprocess.Popen(
            ["sudo", "libinput", "debug-events"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        print("[listen.py] libinput not found — F12 detection unavailable.", file=sys.stderr)
        return

    try:
        for line in proc.stdout:
            if done.is_set():
                break
            if "KEY_F12" in line and "pressed" in line:
                with last_word_lock:
                    word = last_word
                print(f"[listen.py] F12 — typing {word!r}", file=sys.stderr)
                type_text(word)
    finally:
        proc.terminate()

# ─────────────────────────────────────────────────────────────
#  SIGNAL HANDLERS
# ─────────────────────────────────────────────────────────────

def _exit(sig, frame):
    print("\n[listen.py] Shutting down.", file=sys.stderr)
    done.set()

signal.signal(signal.SIGINT,  _exit)
signal.signal(signal.SIGTERM, _exit)

# ─────────────────────────────────────────────────────────────
#  WORD TRANSFORMER
# ─────────────────────────────────────────────────────────────

BLACKLIST = {"word", "spell", "ah", "next", "your", "is", "please", "the", "you", "try", "alright", "you're", "all", "right", "can"}

WORDS_JSON_PATH = os.path.join(os.path.dirname(__file__), "words.json")

class WordTransformer:
    def __init__(self, json_path: str, strict_mode: bool = False, max_combine: int = 3):
        self.mappings = {}
        self.targets = []
        self.strict_mode = strict_mode
        self.max_combine = max_combine
        self.load(json_path)

    def load(self, json_path: str):
        try:
            with open(json_path, "r") as f:
                data = json.load(f)
                self.mappings = {k.lower(): v for k, v in data.get("mappings", {}).items()}
                self.targets = [t.lower() for t in data.get("targets", [])]
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[listen.py] Error loading {json_path}: {e}", file=sys.stderr)

    def _transform_single_word(self, word: str) -> str | None:
        word_lower = word.lower()
        if word_lower in self.mappings:
            return self.mappings[word_lower]
        if word_lower in self.targets:
            return word
        matches = difflib.get_close_matches(word_lower, self.targets, n=1, cutoff=0.6)
        if matches:
            return matches[0]
        elif self.strict_mode:
            return None
        else:
            return word

    def transform_sequence(self, word_sequence: list[str]) -> str | None:
        if not word_sequence:
            return None
        for i in range(len(word_sequence)):
            start_index = max(0, len(word_sequence) - self.max_combine - i)
            current_combination_list = word_sequence[start_index:]
            for j in range(len(current_combination_list)):
                combined_word = " ".join(current_combination_list[j:])
                transformed = self._transform_single_word(combined_word)
                if transformed:
                    return transformed
        return self._transform_single_word(word_sequence[-1])


transformer = WordTransformer(WORDS_JSON_PATH, STRICT_MODE, MAX_COMBINE_WORDS)

def is_any_word_blacklisted(text: str, blacklist_set: set[str]) -> bool:
    return any(word.lower() in blacklist_set for word in text.split())

# ─────────────────────────────────────────────────────────────
#  MAIN LISTEN LOOP
# ─────────────────────────────────────────────────────────────

def listen_loop() -> None:
    global last_word

    with sd.RawInputStream(
        device=DEVICE_ID,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        dtype="int16",
        channels=1,
        callback=audio_callback,
    ):
        print("[listen.py] Always listening. Press F12 to type last recognised word.\n If nothing is detected please manually select sound device", file=sys.stderr)

        prev_word = None

        while not done.is_set():
            try:
                data = audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            with recognizer_lock:
                accepted = recognizer.AcceptWaveform(data)
                if accepted:
                    result_text = json.loads(recognizer.Result()).get("text", "").strip()
                else:
                    result_text = json.loads(recognizer.PartialResult()).get("partial", "").strip()

            if result_text:
                words = result_text.split()
                transformed_word = None

                if words:
                    filtered_words = [w for w in words if w.lower() not in BLACKLIST]
                    transformed_word = transformer.transform_sequence(filtered_words) if filtered_words else None

                if transformed_word:
                    with last_word_lock:
                        last_word = transformed_word

                    if transformed_word != prev_word and transformed_word.lower() not in BLACKLIST:
                        prev_word = transformed_word
                        print(f"[listen.py] Heard: {result_text!r} → last word: {transformed_word!r}", file=sys.stderr)
                        if OS == "Linux":
                            subprocess.Popen(["hyprctl", "notify", "-1", "1000", "rgb(ff1ea3)", transformed_word])
                        if AUTO_TYPE:
                            print(f"[listen.py] AUTO_TYPE enabled — typing {transformed_word!r}", file=sys.stderr)
                            type_text(transformed_word)
                elif not is_any_word_blacklisted(result_text, BLACKLIST):
                    print(f"[listen.py] Heard: {result_text!r} (no transformation)", file=sys.stderr)


if __name__ == "__main__":
    f12_thread = threading.Thread(target=watch_f12, daemon=True)
    f12_thread.start()

    listen_loop()
