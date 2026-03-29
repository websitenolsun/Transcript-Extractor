"""
Video Transcription Engine
Uses faster-whisper for efficient transcription with progress reporting.
"""

import os
import subprocess
import threading
import uuid
import time
from pathlib import Path
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Optional


class JobStatus(Enum):
    PENDING = "pending"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TranscriptionResult:
    text: str = ""
    segments: list = field(default_factory=list)
    language: str = ""
    duration: float = 0.0


@dataclass
class TranscriptionJob:
    job_id: str
    video_path: str
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    message: str = "Sıraya alındı..."
    result: Optional[TranscriptionResult] = None
    error: Optional[str] = None
    audio_path: Optional[str] = None

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "result": {
                "text": self.result.text,
                "language": self.result.language,
                "duration": self.result.duration,
                "segment_count": len(self.result.segments),
            } if self.result else None,
        }


class TranscriptionEngine:
    def __init__(self, model_size: str = "base", upload_dir: str = "uploads"):
        self.model_size = model_size
        self.upload_dir = upload_dir
        self.jobs: dict[str, TranscriptionJob] = {}
        self._model = None
        self._model_lock = threading.Lock()

    def _get_model(self):
        """Lazy-load the whisper model."""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel
                    print(f"[Whisper] Loading model: {self.model_size}...")
                    self._model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                    )
                    print("[Whisper] Model loaded successfully.")
        return self._model

    def create_job(self, video_path: str) -> TranscriptionJob:
        """Create a new transcription job."""
        job_id = str(uuid.uuid4())[:8]
        job = TranscriptionJob(job_id=job_id, video_path=video_path)
        self.jobs[job_id] = job
        return job

    def start_job(self, job_id: str):
        """Start processing a transcription job in background thread."""
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        thread = threading.Thread(target=self._process_job, args=(job,), daemon=True)
        thread.start()

    def _process_job(self, job: TranscriptionJob):
        """Full transcription pipeline: extract audio -> transcribe."""
        try:
            # Step 1: Extract audio
            job.status = JobStatus.EXTRACTING_AUDIO
            job.progress = 5.0
            job.message = "Videodan ses çıkarılıyor..."

            audio_path = self._extract_audio(job.video_path)
            job.audio_path = audio_path
            job.progress = 15.0
            job.message = "Ses çıkarıldı, transkripsiyon başlatılıyor..."

            # Step 2: Transcribe
            job.status = JobStatus.TRANSCRIBING
            job.progress = 20.0

            result = self._transcribe(audio_path, job)

            job.result = result
            job.status = JobStatus.COMPLETED
            job.progress = 100.0
            job.message = "Transkripsiyon tamamlandı!"

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.message = f"Hata: {str(e)}"
            print(f"[Error] Job {job.job_id} failed: {e}")

        finally:
            # Cleanup audio file
            if job.audio_path and os.path.exists(job.audio_path):
                try:
                    os.remove(job.audio_path)
                except:
                    pass
            # Cleanup video file
            if os.path.exists(job.video_path):
                try:
                    os.remove(job.video_path)
                except:
                    pass

    def _extract_audio(self, video_path: str) -> str:
        """Extract audio from video using FFmpeg."""
        audio_path = video_path.rsplit(".", 1)[0] + ".wav"

        cmd = [
            "ffmpeg", "-i", video_path,
            "-vn",                    # no video
            "-acodec", "pcm_s16le",   # 16-bit PCM
            "-ar", "16000",           # 16kHz sample rate (optimal for Whisper)
            "-ac", "1",               # mono
            "-y",                     # overwrite
            audio_path,
        ]

        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min timeout
        )

        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg hatası: {process.stderr[:500]}")

        if not os.path.exists(audio_path):
            raise RuntimeError("Ses dosyası oluşturulamadı")

        return audio_path

    def _transcribe(self, audio_path: str, job: TranscriptionJob) -> TranscriptionResult:
        """Transcribe audio using faster-whisper."""
        model = self._get_model()

        segments_iter, info = model.transcribe(
            audio_path,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )

        result = TranscriptionResult()
        result.language = info.language
        result.duration = info.duration

        all_segments = []
        total_duration = info.duration if info.duration > 0 else 1

        for segment in segments_iter:
            seg_data = {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "words": [],
            }

            if segment.words:
                for w in segment.words:
                    seg_data["words"].append({
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                        "probability": round(w.probability, 3),
                    })

            all_segments.append(seg_data)
            result.text += segment.text

            # Update progress (20% -> 95%)
            progress = 20 + (segment.end / total_duration) * 75
            progress = min(progress, 95)
            job.progress = round(progress, 1)
            job.message = f"Transkripsiyon: %{int(progress)}..."

        result.segments = all_segments
        result.text = result.text.strip()

        return result


def format_timestamp_srt(seconds: float) -> str:
    """Format seconds to SRT timestamp: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Format seconds to VTT timestamp: HH:MM:SS.mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def generate_srt(segments: list) -> str:
    """Generate SRT subtitle content."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = format_timestamp_srt(seg["start"])
        end = format_timestamp_srt(seg["end"])
        lines.append(f"{i}")
        lines.append(f"{start} --> {end}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


def generate_vtt(segments: list) -> str:
    """Generate WebVTT subtitle content."""
    lines = ["WEBVTT", ""]
    for i, seg in enumerate(segments, 1):
        start = format_timestamp_vtt(seg["start"])
        end = format_timestamp_vtt(seg["end"])
        lines.append(f"{i}")
        lines.append(f"{start} --> {end}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)
