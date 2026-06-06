from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import queue
import struct
import sys
import tempfile
import threading
import time
import tarfile
import wave
from dataclasses import dataclass
from pathlib import Path

import keyboard
import numpy as np
import pyautogui
import pyperclip
import pystray
import sherpa_onnx
import sounddevice as sd
import soundfile as sf
from PIL import Image, ImageDraw

from opencc import OpenCC

try:
    import winsound
except ImportError:  # pragma: no cover - Windows-only helper.
    winsound = None


SHIFT_KEY_NAMES = {"shift", "left shift", "right shift"}
DOUBLE_TAP_SECONDS = 0.45
MIN_RECORD_SECONDS = 0.35
AUDIO_BLOCK_SECONDS = 0.1
APP_NAME = "Speech Hotkey Tool"
APP_DATA_DIR = Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir())) / "SpeechHotkeyTool"
LOG_PATH = APP_DATA_DIR / "app.log"
TARGET_ASR_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class Settings:
    language: str
    sample_rate: int
    input_device: int | str | None
    model_dir: Path
    num_threads: int
    provider: str
    simplify_chinese: bool
    endpoint_silence: float
    silence_threshold: float
    max_utterance_seconds: float
    tray: bool
    notifications: bool
    preload_model: bool
    restore_clipboard: bool


class SpeechHotkeyApp:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.RLock()
        self.model_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.last_shift_release_at = 0.0

        self.stream: sd.InputStream | None = None
        self.audio_queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self.transcription_queue: queue.Queue[tuple[list[np.ndarray], int] | None] = queue.Queue()
        self.recording_started_at = 0.0
        self.recording_sample_rate = settings.sample_rate
        self.is_recording = False
        self.is_processing = False
        self.recognizer: sherpa_onnx.OfflineRecognizer | None = None
        self.simplifier = OpenCC("t2s") if settings.simplify_chinese else None
        self.segment_thread: threading.Thread | None = None
        self.transcription_thread: threading.Thread | None = None
        self.tray_icon: pystray.Icon | None = None
        self.status_text = "Ready"
        self.max_observed_rms = 0.0

    def run(self) -> None:
        if sys.platform != "win32":
            self._emit("This tool is intended for Windows only.")
            return

        pyautogui.PAUSE = 0.05
        self._emit("Speech-to-Text Hotkey Tool")
        self._emit("Double-tap Shift to start live dictation, double-tap Shift again to stop.")
        self._emit("While dictation is on, each pause after a sentence triggers transcription and paste.")
        self._emit(f"ASR engine: sherpa-onnx, model dir: {self.settings.model_dir}")
        self._emit(f"Log file: {LOG_PATH}")
        self._set_status("Ready. Double-tap Shift to start dictation.")

        keyboard.on_release(self._on_key_release)
        if self.settings.preload_model:
            self._start_model_preload()

        try:
            if self.settings.tray:
                self._run_tray()
            else:
                self._emit("Press Ctrl+C in this window to exit.")
                while not self.stop_event.wait(0.25):
                    pass
        except KeyboardInterrupt:
            self._emit("Exiting...")
        finally:
            keyboard.unhook_all()
            self._force_stop_recording()

    def _on_key_release(self, event: keyboard.KeyboardEvent) -> None:
        if event.name not in SHIFT_KEY_NAMES:
            return

        now = time.monotonic()
        should_toggle = False
        with self.lock:
            if now - self.last_shift_release_at <= DOUBLE_TAP_SECONDS:
                should_toggle = True
                self.last_shift_release_at = 0.0
            else:
                self.last_shift_release_at = now

        if should_toggle:
            self.toggle_recording()

    def toggle_recording(self) -> None:
        with self.lock:
            if self.is_processing:
                self._emit("Still transcribing the previous recording. Please wait.", notify=True)
                return
            should_stop = self.is_recording

        if should_stop:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        with self.lock:
            if self.is_recording or self.is_processing:
                return
            self.audio_queue = queue.Queue()
            self.transcription_queue = queue.Queue()
            self.recording_started_at = time.monotonic()

        try:
            sample_rate = self._choose_sample_rate()
            stream = sd.InputStream(
                samplerate=sample_rate,
                blocksize=max(1, int(sample_rate * AUDIO_BLOCK_SECONDS)),
                channels=1,
                dtype="float32",
                device=self.settings.input_device,
                callback=self._audio_callback,
            )
            stream.start()
        except Exception as exc:
            fallback_rate = self._default_input_sample_rate()
            if self.settings.sample_rate and self.settings.sample_rate != fallback_rate:
                self._emit(f"Could not open microphone at {self.settings.sample_rate} Hz. Retrying at {fallback_rate} Hz...")
                try:
                    sample_rate = fallback_rate
                    stream = sd.InputStream(
                        samplerate=sample_rate,
                        blocksize=max(1, int(sample_rate * AUDIO_BLOCK_SECONDS)),
                        channels=1,
                        dtype="float32",
                        device=self.settings.input_device,
                        callback=self._audio_callback,
                    )
                    stream.start()
                except Exception as fallback_exc:
                    with self.lock:
                        self.recording_started_at = 0.0
                    self._emit(f"Could not start microphone recording: {fallback_exc}", notify=True)
                    return
            else:
                with self.lock:
                    self.recording_started_at = 0.0
                self._emit(f"Could not start microphone recording: {exc}", notify=True)
                return

        segment_thread = threading.Thread(
            target=self._segment_audio_loop,
            args=(sample_rate,),
            daemon=True,
        )
        transcription_thread = threading.Thread(
            target=self._transcription_loop,
            daemon=True,
        )

        with self.lock:
            self.stream = stream
            self.recording_sample_rate = sample_rate
            self.is_recording = True
            self.max_observed_rms = 0.0
            self.segment_thread = segment_thread
            self.transcription_thread = transcription_thread

        transcription_thread.start()
        segment_thread.start()

        self._beep("start")
        self._set_status("Dictation on. Pause after each sentence.")
        self._emit(f"Live dictation started at {sample_rate} Hz. Pause after each sentence to paste it.", notify=True)

    def stop_recording(self) -> None:
        with self.lock:
            if not self.is_recording:
                return
            stream = self.stream
            self.stream = None
            self.is_recording = False
            self.is_processing = True

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                self._emit(f"Could not cleanly stop microphone stream: {exc}")

        with self.lock:
            self.recording_started_at = 0.0

        self._beep("stop")
        self.audio_queue.put(None)
        self._set_status("Stopping. Finishing queued speech...")
        self._emit("Live dictation stopped. Finishing queued speech...", notify=True)

    def _force_stop_recording(self) -> None:
        with self.lock:
            stream = self.stream
            self.stream = None
            self.is_recording = False
            self.recording_started_at = 0.0

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.audio_queue.put(None)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            self._emit(f"Audio warning: {status}")

        with self.lock:
            if self.is_recording:
                self.audio_queue.put(indata.copy())

    def _segment_audio_loop(self, sample_rate: int) -> None:
        utterance_frames: list[np.ndarray] = []
        speech_started = False
        silence_seconds = 0.0
        utterance_seconds = 0.0
        chunks_seen = 0

        while True:
            chunk = self.audio_queue.get()
            if chunk is None:
                break

            chunk_seconds = len(chunk) / sample_rate
            chunk_rms = self._rms(chunk)
            chunks_seen += 1
            if chunk_rms > self.max_observed_rms:
                self.max_observed_rms = chunk_rms
            if chunks_seen % 20 == 0:
                self._emit(
                    f"Audio monitor: max RMS {self.max_observed_rms:.5f}, threshold {self.settings.silence_threshold:.5f}"
                )
            has_voice = chunk_rms >= self.settings.silence_threshold

            if has_voice:
                if not speech_started:
                    speech_started = True
                    silence_seconds = 0.0
                    utterance_seconds = 0.0
                    utterance_frames = []
                    self._emit(f"Voice detected. RMS {chunk_rms:.5f}")
                silence_seconds = 0.0
                utterance_frames.append(chunk)
            elif speech_started:
                silence_seconds += chunk_seconds
                utterance_frames.append(chunk)

            if not speech_started:
                continue

            utterance_seconds += chunk_seconds
            if (
                silence_seconds >= self.settings.endpoint_silence
                or utterance_seconds >= self.settings.max_utterance_seconds
            ):
                self._enqueue_utterance(utterance_frames, sample_rate)
                utterance_frames = []
                speech_started = False
                silence_seconds = 0.0
                utterance_seconds = 0.0

        if speech_started and utterance_frames:
            self._enqueue_utterance(utterance_frames, sample_rate)

        self._emit(f"Audio monitor stopped. Max RMS was {self.max_observed_rms:.5f}.")
        self.transcription_queue.put(None)

    def _enqueue_utterance(self, frames: list[np.ndarray], sample_rate: int) -> None:
        if not frames:
            return
        duration = sum(len(frame) for frame in frames) / sample_rate
        if duration < MIN_RECORD_SECONDS:
            return
        self.transcription_queue.put((list(frames), sample_rate))
        self._set_status("Sentence detected. Transcribing...")
        self._emit("Sentence detected. Transcribing...")

    def _transcription_loop(self) -> None:
        try:
            while True:
                item = self.transcription_queue.get()
                if item is None:
                    break
                frames, sample_rate = item
                self._transcribe_and_paste(frames, sample_rate)
        finally:
            with self.lock:
                self.is_processing = False
            self._set_status("Ready. Double-tap Shift to start dictation.")
            self._emit("Ready.")

    def _transcribe_and_paste(self, frames: list[np.ndarray], sample_rate: int) -> None:
        try:
            duration = sum(len(frame) for frame in frames) / sample_rate
            if duration < MIN_RECORD_SECONDS or not frames:
                self._emit("Recording was too short. Nothing to transcribe.")
                return

            audio = np.concatenate(frames, axis=0)
            if self._looks_silent(audio):
                self._emit("Recording looked silent. Nothing to paste.")
                return

            text = self._transcribe(audio, sample_rate)

            if not text:
                self._emit("sherpa-onnx did not return any text. Try speaking longer/louder, or select a different microphone with --input-device.")
                return

            text = self._post_process_text(text)
            self._paste_text(text)
            self._set_status("Pasted text. Listening..." if self.is_recording else "Pasted text.")
            self._emit(f"Pasted: {text}")
        except Exception as exc:
            self._emit(f"Transcription failed: {exc}", notify=True)

    def _choose_sample_rate(self) -> int:
        if self.settings.sample_rate > 0:
            return self.settings.sample_rate
        return self._default_input_sample_rate()

    def _default_input_sample_rate(self) -> int:
        device_info = sd.query_devices(self.settings.input_device, "input")
        return int(device_info["default_samplerate"])

    def _get_recognizer(self) -> sherpa_onnx.OfflineRecognizer:
        with self.model_lock:
            if self.recognizer is None:
                self._set_status("Loading sherpa-onnx model...")
                self._emit("Loading sherpa-onnx model from local files...", notify=True)
                self.recognizer = self._create_recognizer()
                self._emit("sherpa-onnx model is ready.")
        return self.recognizer

    def _create_recognizer(self) -> sherpa_onnx.OfflineRecognizer:
        model_dir = self.settings.model_dir
        if not model_dir.exists():
            raise FileNotFoundError(f"Model directory does not exist: {model_dir}")

        funasr = self._find_funasr_nano_files(model_dir)
        if funasr is None:
            extracted = self._extract_supported_model_archive(model_dir)
            if extracted:
                funasr = self._find_funasr_nano_files(model_dir)

        if funasr is not None:
            self._validate_funasr_nano_files(funasr)
            return sherpa_onnx.OfflineRecognizer.from_funasr_nano(
                encoder_adaptor=str(funasr["encoder_adaptor"]),
                llm=str(funasr["llm"]),
                embedding=str(funasr["embedding"]),
                tokenizer=str(funasr["tokenizer"]),
                num_threads=self.settings.num_threads,
                sample_rate=TARGET_ASR_SAMPLE_RATE,
                provider=self.settings.provider,
                language=self.settings.language,
                itn=True,
            )

        detected = [p.name for p in model_dir.rglob("*") if p.is_file()]
        if any(name.lower().endswith(".gguf") for name in detected):
            raise RuntimeError(
                "Detected a FunASR GGUF package, but sherpa-onnx Python 1.13.2 requires "
                "encoder_adaptor*.onnx, llm*.onnx, embedding*.onnx, and a Qwen3 tokenizer folder. "
                "Please use the official sherpa-onnx-funasr-nano-int8-2025-12-30 model package."
            )

        raise RuntimeError(
            "Could not find a supported sherpa-onnx FunASR Nano model layout. "
            "Expected encoder_adaptor*.onnx, llm*.onnx, embedding*.onnx, and tokenizer.json under the model directory."
        )

    def _validate_funasr_nano_files(self, files: dict[str, Path]) -> None:
        minimum_sizes = {
            "encoder_adaptor": 100_000_000,
            "llm": 300_000_000,
            "embedding": 50_000_000,
            "tokenizer": 1_000_000,
        }

        for key, path in files.items():
            if not path.exists():
                raise FileNotFoundError(f"Missing FunASR model file: {key}={path}")
            size = path.stat().st_size if path.is_file() else self._directory_size(path)
            self._emit(f"FunASR model component: {key}={path} ({size:,} bytes)")
            minimum_size = minimum_sizes[key]
            if size < minimum_size:
                raise RuntimeError(
                    f"FunASR model component looks incomplete: {key}={path} "
                    f"({size:,} bytes, expected at least {minimum_size:,} bytes). "
                    "Please re-extract or re-download the portable zip."
                )

    @staticmethod
    def _directory_size(path: Path) -> int:
        return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())

    @staticmethod
    def _find_funasr_nano_files(root: Path) -> dict[str, Path] | None:
        candidates = [root, *[p for p in root.iterdir() if p.is_dir()]]
        for model_dir in candidates:
            encoder = SpeechHotkeyApp._first_existing(
                model_dir,
                ["encoder_adaptor.int8.onnx", "encoder_adaptor.onnx", "encoder_adaptor.fp16.onnx"],
            )
            llm = SpeechHotkeyApp._first_existing(
                model_dir,
                ["llm.int8.onnx", "llm.fp16.onnx", "llm.onnx", "llm.fp32.onnx"],
            )
            embedding = SpeechHotkeyApp._first_existing(
                model_dir,
                ["embedding.int8.onnx", "embedding.onnx", "embedding.fp16.onnx"],
            )
            tokenizer = SpeechHotkeyApp._find_tokenizer_dir(model_dir)
            if encoder and llm and embedding and tokenizer:
                return {
                    "encoder_adaptor": encoder,
                    "llm": llm,
                    "embedding": embedding,
                    "tokenizer": tokenizer,
                }
        return None

    def _extract_supported_model_archive(self, model_dir: Path) -> bool:
        archives = sorted(model_dir.glob("sherpa-onnx-funasr-nano-int8-*.tar.bz2"))
        if not archives:
            return False

        archive = archives[-1]
        self._emit(f"Extracting sherpa-onnx FunASR model archive: {archive.name}")
        with tarfile.open(archive, "r:bz2") as tar:
            safe_members = []
            for member in tar.getmembers():
                member_path = (model_dir / member.name).resolve()
                if not member_path.is_relative_to(model_dir.resolve()):
                    raise RuntimeError(f"Unsafe path in model archive: {member.name}")
                safe_members.append(member)
            tar.extractall(model_dir, members=safe_members)
        self._emit("Model archive extracted.")
        return True

    @staticmethod
    def _first_existing(model_dir: Path, names: list[str]) -> Path | None:
        lowered = {p.name.lower(): p for p in model_dir.rglob("*.onnx")}
        for name in names:
            found = lowered.get(name.lower())
            if found is not None:
                return found
        return None

    @staticmethod
    def _find_tokenizer_dir(model_dir: Path) -> Path | None:
        for tokenizer_json in model_dir.rglob("tokenizer.json"):
            return tokenizer_json.parent
        return None

    def _start_model_preload(self) -> None:
        thread = threading.Thread(target=self._preload_model, daemon=True)
        thread.start()

    def _preload_model(self) -> None:
        try:
            self._get_recognizer()
            if not self.is_recording:
                self._set_status("Ready. Double-tap Shift to start dictation.")
        except Exception as exc:
            self._emit(f"Could not preload sherpa-onnx model: {exc}")

    def _transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        recognizer = self._get_recognizer()
        prepared_audio = self._prepare_audio_for_asr(audio, sample_rate)

        stream = recognizer.create_stream()
        stream.accept_waveform(TARGET_ASR_SAMPLE_RATE, prepared_audio)
        recognizer.decode_stream(stream)
        return stream.result.text.strip()

    @staticmethod
    def _prepare_audio_for_asr(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if sample_rate != TARGET_ASR_SAMPLE_RATE:
            audio = SpeechHotkeyApp._resample_linear(audio, sample_rate, TARGET_ASR_SAMPLE_RATE)
        return np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)

    @staticmethod
    def _load_audio_file(path: Path) -> tuple[np.ndarray, int]:
        if not path.exists():
            raise FileNotFoundError(f"Audio file does not exist: {path}")

        suffix = path.suffix.lower()
        pydub_first = suffix in {".mp3", ".m4a", ".aac", ".wma", ".mp4"}
        loaders = (
            (SpeechHotkeyApp._load_audio_file_with_pydub, SpeechHotkeyApp._load_audio_file_with_soundfile)
            if pydub_first
            else (SpeechHotkeyApp._load_audio_file_with_soundfile, SpeechHotkeyApp._load_audio_file_with_pydub)
        )

        errors: list[str] = []
        for loader in loaders:
            try:
                return loader(path)
            except Exception as exc:
                errors.append(f"{loader.__name__}: {exc}")
        raise RuntimeError("Could not read audio file. " + " | ".join(errors))

    @staticmethod
    def _load_audio_file_with_soundfile(path: Path) -> tuple[np.ndarray, int]:
        audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
        return np.asarray(audio, dtype=np.float32), int(sample_rate)

    @staticmethod
    def _load_audio_file_with_pydub(path: Path) -> tuple[np.ndarray, int]:
        from pydub import AudioSegment

        segment = AudioSegment.from_file(str(path))
        segment = segment.set_channels(1).set_frame_rate(TARGET_ASR_SAMPLE_RATE).set_sample_width(2)
        samples = np.asarray(segment.get_array_of_samples(), dtype=np.float32)
        audio = samples / 32768.0
        return audio, TARGET_ASR_SAMPLE_RATE

    @staticmethod
    def _resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        if len(audio) == 0 or source_rate == target_rate:
            return audio.astype(np.float32, copy=False)

        duration = len(audio) / float(source_rate)
        target_len = max(1, int(round(duration * target_rate)))
        source_x = np.linspace(0.0, duration, num=len(audio), endpoint=False)
        target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
        return np.interp(target_x, source_x, audio).astype(np.float32)

    def _post_process_text(self, text: str) -> str:
        if self.simplifier is None:
            return text
        return self.simplifier.convert(text)

    def _paste_text(self, text: str) -> None:
        previous_clipboard = None
        if self.settings.restore_clipboard:
            try:
                previous_clipboard = pyperclip.paste()
            except Exception:
                previous_clipboard = None

        pyperclip.copy(text)
        time.sleep(0.12)
        pyautogui.hotkey("ctrl", "v")
        if self.settings.restore_clipboard:
            time.sleep(0.25)
            try:
                pyperclip.copy(previous_clipboard or "")
            except Exception as exc:
                self._emit(f"Could not restore clipboard: {exc}")

    def _run_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Start / Stop Dictation", self._tray_toggle),
            pystray.MenuItem("Open Log", self._tray_open_log),
            pystray.MenuItem("Exit", self._tray_exit),
        )
        self.tray_icon = pystray.Icon(
            "speech_hotkey_tool",
            self._create_tray_image(),
            self._tray_title(),
            menu,
        )
        self.tray_icon.run()

    def _tray_toggle(self, icon, item) -> None:
        self.toggle_recording()

    def _tray_exit(self, icon, item) -> None:
        self.stop_event.set()
        self._force_stop_recording()
        keyboard.unhook_all()
        if self.tray_icon is not None:
            self.tray_icon.stop()

    def _tray_open_log(self, icon, item) -> None:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOG_PATH.touch(exist_ok=True)
        os.startfile(LOG_PATH)

    def _set_status(self, status: str) -> None:
        self.status_text = status
        if self.tray_icon is not None:
            try:
                self.tray_icon.title = self._tray_title()
            except Exception:
                pass

    def _tray_title(self) -> str:
        return f"{APP_NAME}\n{self.status_text}"

    def _emit(self, message: str, notify: bool = False) -> None:
        logging.info(message)
        try:
            print(message)
        except Exception:
            pass
        if notify and self.settings.notifications and self.tray_icon is not None:
            try:
                self.tray_icon.notify(message, APP_NAME)
            except Exception:
                pass

    @staticmethod
    def _create_tray_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (22, 28, 36, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((22, 10, 42, 38), radius=9, fill=(95, 190, 150, 255))
        draw.rectangle((29, 38, 35, 49), fill=(95, 190, 150, 255))
        draw.arc((16, 24, 48, 52), 0, 180, fill=(238, 242, 247, 255), width=4)
        draw.line((20, 52, 44, 52), fill=(238, 242, 247, 255), width=4)
        return image

    @staticmethod
    def _looks_silent(audio: np.ndarray) -> bool:
        return float(np.max(np.abs(audio))) < 0.005

    @staticmethod
    def _rms(audio: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(audio))))

    @staticmethod
    def _beep(kind: str) -> None:
        if winsound is None:
            return
        try:
            APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = APP_DATA_DIR / f"{kind}_chime.wav"
            if not path.exists():
                SpeechHotkeyApp._write_chime(path, kind)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    @staticmethod
    def _write_chime(path: Path, kind: str) -> None:
        sample_rate = 22050
        duration = 0.16
        frequency = 523.25 if kind == "start" else 392.0
        amplitude = 2600
        frame_count = int(sample_rate * duration)

        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for i in range(frame_count):
                fade = min(1.0, i / (sample_rate * 0.02), (frame_count - i) / (sample_rate * 0.04))
                value = int(amplitude * fade * math.sin(2 * math.pi * frequency * i / sample_rate))
                frames.extend(struct.pack("<h", value))
            wav_file.writeframes(bytes(frames))


def parse_args() -> Settings:
    parser = argparse.ArgumentParser(
        description="Double-tap Shift to record speech, transcribe it locally, and paste the text.",
    )
    parser.add_argument(
        "--model-dir",
        default=os.getenv("SPEECH_HOTKEY_MODEL_DIR", "models"),
        help="Directory containing sherpa-onnx model files. Default: models",
    )
    parser.add_argument(
        "--language",
        default=os.getenv("SPEECH_HOTKEY_LANGUAGE", "zh"),
        help="Recognition language hint. Default: zh",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=int(os.getenv("SPEECH_HOTKEY_SAMPLE_RATE", "0")),
        help="Microphone sample rate. Default: 0, meaning auto-detect device default.",
    )
    parser.add_argument(
        "--input-device",
        default=os.getenv("SPEECH_HOTKEY_INPUT_DEVICE"),
        help="Optional sounddevice input device id or name. Example: --input-device 18",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List audio devices and exit.",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("SPEECH_HOTKEY_PROVIDER", "cpu"),
        help="sherpa-onnx execution provider. Default: cpu",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=int(os.getenv("SPEECH_HOTKEY_NUM_THREADS", "4")),
        help="CPU thread count for sherpa-onnx. Default: 4",
    )
    parser.add_argument(
        "--endpoint-silence",
        type=float,
        default=float(os.getenv("SPEECH_HOTKEY_ENDPOINT_SILENCE", "0.8")),
        help="Seconds of silence that ends one sentence. Default: 0.8",
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=float(os.getenv("SPEECH_HOTKEY_SILENCE_THRESHOLD", "0.003")),
        help="RMS threshold used to detect speech. Default: 0.003",
    )
    parser.add_argument(
        "--max-utterance-seconds",
        type=float,
        default=float(os.getenv("SPEECH_HOTKEY_MAX_UTTERANCE_SECONDS", "18")),
        help="Force transcription after this many seconds even without silence. Default: 18",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run in console mode without a system tray icon.",
    )
    parser.add_argument(
        "--enable-notifications",
        action="store_true",
        help="Enable Windows tray notifications. Disabled by default to avoid system notification sounds.",
    )
    parser.add_argument(
        "--no-preload",
        action="store_true",
        help="Do not load the sherpa-onnx model in the background when the app starts.",
    )
    parser.add_argument(
        "--keep-transcript-in-clipboard",
        action="store_true",
        help="Do not restore the previous clipboard after pasting transcribed text.",
    )
    parser.add_argument(
        "--no-simplified",
        action="store_true",
        help="Disable Traditional Chinese to Simplified Chinese conversion.",
    )
    parser.add_argument(
        "--list-model-files",
        action="store_true",
        help="List files under --model-dir and exit.",
    )
    parser.add_argument(
        "--check-model",
        action="store_true",
        help="Load the sherpa-onnx model and exit. Useful for validating packaged model files.",
    )
    parser.add_argument(
        "--transcribe-file",
        type=str,
        help="Transcribe an audio file and exit. MP3/M4A decoding requires ffmpeg for pydub.",
    )
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        raise SystemExit(0)

    model_dir = Path(args.model_dir).expanduser()
    if not model_dir.is_absolute():
        model_dir = Path.cwd() / model_dir

    if args.list_model_files:
        for path in sorted(model_dir.rglob("*")):
            if path.is_file():
                print(path)
        raise SystemExit(0)

    input_device: int | str | None = args.input_device
    if isinstance(input_device, str) and input_device.isdigit():
        input_device = int(input_device)

    settings = Settings(
        language=args.language,
        sample_rate=args.sample_rate,
        input_device=input_device,
        model_dir=model_dir,
        num_threads=args.num_threads,
        provider=args.provider,
        simplify_chinese=not args.no_simplified,
        endpoint_silence=args.endpoint_silence,
        silence_threshold=args.silence_threshold,
        max_utterance_seconds=args.max_utterance_seconds,
        tray=not args.no_tray,
        notifications=args.enable_notifications,
        preload_model=not args.no_preload,
        restore_clipboard=not args.keep_transcript_in_clipboard,
    )

    if args.check_model:
        SpeechHotkeyApp(settings)._get_recognizer()
        print("Model loaded successfully.")
        raise SystemExit(0)

    if args.transcribe_file:
        app = SpeechHotkeyApp(settings)
        audio_path = Path(args.transcribe_file).expanduser()
        if not audio_path.is_absolute():
            audio_path = Path.cwd() / audio_path
        audio, sample_rate = app._load_audio_file(audio_path)
        text = app._post_process_text(app._transcribe(audio, sample_rate))
        print(text)
        raise SystemExit(0)

    return settings


def main() -> None:
    setup_logging()
    settings = parse_args()
    SpeechHotkeyApp(settings).run()


def setup_logging() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


if __name__ == "__main__":
    main()
