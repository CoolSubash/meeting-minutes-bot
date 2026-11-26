"""Audio conversion, speech-to-text, and text-to-speech."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import wave

import azure.cognitiveservices.speech as speechsdk

from .errors import PipelineError

ALLOWED_EXTENSIONS = {"wav", "mp3", "webm", "ogg", "m4a", "aac"}
MAX_AUDIO_SECONDS = 180
RECOGNITION_TIMEOUT_SECONDS = 200
TICKS_PER_SECOND = 10_000_000
VOICES = {"en-US-JennyNeural", "en-US-GuyNeural", "en-US-AriaNeural"}
DEFAULT_VOICE = "en-US-JennyNeural"


def _speech_config() -> speechsdk.SpeechConfig:
    key, region = os.environ.get("AZURE_SPEECH_KEY"), os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise PipelineError("Azure Speech is not configured on the server.", 500)
    return speechsdk.SpeechConfig(subscription=key, region=region)


def ffmpeg_path() -> str | None:
    """Use a system ffmpeg if there is one, otherwise the binary bundled by imageio-ffmpeg.

    The bundled binary means App Service does not have to apt-get ffmpeg on every start,
    which used up the F1 plan's daily CPU quota.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def prepare_audio(source_path: str, output_path: str) -> str:
    """Convert any supported upload to 16 kHz, 16-bit, mono WAV."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise PipelineError("ffmpeg is not installed on the server.", 500)
    command = [
        ffmpeg, "-y", "-loglevel", "error", "-i", source_path,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", output_path,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        raise PipelineError("The audio file is damaged or in an unsupported format.", 415) from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError("Audio conversion took too long.", 504) from exc

    with wave.open(output_path, "rb") as wav:
        seconds = wav.getnframes() / wav.getframerate()
    if seconds > MAX_AUDIO_SECONDS:
        raise PipelineError("Recordings must be 3 minutes or shorter.", 413)
    return output_path


def transcribe(wav_path: str) -> dict:
    """Transcribe a WAV file with continuous recognition."""
    config = _speech_config()
    config.speech_recognition_language = "en-US"
    config.output_format = speechsdk.OutputFormat.Detailed
    config.request_word_level_timestamps()

    try:
        audio = speechsdk.audio.AudioConfig(filename=wav_path)
        recognizer = speechsdk.SpeechRecognizer(speech_config=config, audio_config=audio)
    except RuntimeError as exc:
        raise PipelineError("The audio format is not supported.", 415) from exc

    done = threading.Event()
    segments: list[dict] = []
    errors: list[str] = []

    def on_recognized(event) -> None:
        if event.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            detail = json.loads(event.result.json)
            best = (detail.get("NBest") or [{}])[0]
            segments.append({"text": event.result.text, "best": best})

    def on_canceled(event) -> None:
        if event.cancellation_details.reason == speechsdk.CancellationReason.Error:
            errors.append(event.cancellation_details.error_details)
        done.set()

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(lambda _event: done.set())

    recognizer.start_continuous_recognition_async().get()
    finished = done.wait(RECOGNITION_TIMEOUT_SECONDS)
    recognizer.stop_continuous_recognition_async().get()

    if not finished:
        raise PipelineError("Transcription timed out.", 504)
    if errors:
        status = 415 if "SPXERR_INVALID_HEADER" in errors[0] else 502
        raise PipelineError("Azure Speech could not transcribe this audio.", status)
    return _build_result(segments)


def _build_result(segments: list[dict]) -> dict:
    words, scores = [], []
    duration = 0.0
    for segment in segments:
        best = segment["best"]
        if "Confidence" in best:
            scores.append(best["Confidence"])
        for word in best.get("Words", []):
            offset = word["Offset"] / TICKS_PER_SECOND
            length = word["Duration"] / TICKS_PER_SECOND
            duration = max(duration, offset + length)
            words.append({
                "word": word["Word"],
                "offset": round(offset, 2),
                "duration": round(length, 2),
                "confidence": word.get("Confidence"),
            })
    return {
        "transcript": " ".join(s["text"] for s in segments if s["text"]).strip(),
        "language": "en-US",
        "duration_seconds": round(duration, 2),
        "confidence": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "words": words,
    }


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> dict:
    """Create the spoken recap as base64-encoded MP3."""
    config = _speech_config()
    config.speech_synthesis_voice_name = voice
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
    result = synthesizer.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise PipelineError("Azure Text-to-Speech could not create the recap.", 502)
    return {
        "summary_audio": base64.b64encode(result.audio_data).decode("ascii"),
        "char_count": len(text),
        "voice": voice,
    }
