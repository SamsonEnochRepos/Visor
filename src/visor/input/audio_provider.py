"""
audio_provider.py — Abstract audio input for VISOR.

Provides a clean abstraction over microphone hardware for the voice
engine, preparing for future AR glasses integration.
"""

import abc
import logging
from typing import Optional

logger = logging.getLogger("VISOR.input.audio")


class AudioProvider(abc.ABC):
    """Abstract audio input source."""

    @abc.abstractmethod
    def start(self) -> bool:
        """Open the audio stream. Returns True on success."""
        ...

    @abc.abstractmethod
    def read_chunk(self) -> Optional[bytes]:
        """Read one chunk of audio data. Returns None on failure."""
        ...

    @abc.abstractmethod
    def stop(self) -> None:
        """Release audio resources."""
        ...

    @abc.abstractmethod
    def get_sample_rate(self) -> int:
        """Return the sample rate in Hz."""
        ...


class PyAudioProvider(AudioProvider):
    """PyAudio microphone implementation."""

    def __init__(self, sample_rate: int = 16000,
                 chunk_size: int = 8192,
                 device_index: int = -1) -> None:
        self._sample_rate = sample_rate
        self._chunk_size = chunk_size
        self._device_index = device_index
        self._pa = None
        self._stream = None

    def start(self) -> bool:
        """Open the microphone stream."""
        try:
            import pyaudio
        except ImportError:
            logger.error("PyAudio not installed — voice disabled")
            return False

        try:
            self._pa = pyaudio.PyAudio()
            device = None if self._device_index < 0 else self._device_index

            if device is not None:
                info = self._pa.get_device_info_by_index(device)
                logger.info("Microphone: %s [index %d]", info["name"], device)
            else:
                info = self._pa.get_default_input_device_info()
                logger.info("Microphone: %s [system default]", info["name"])

            stream_kwargs = dict(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._sample_rate,
                input=True,
                frames_per_buffer=self._chunk_size,
            )
            if device is not None:
                stream_kwargs["input_device_index"] = device

            self._stream = self._pa.open(**stream_kwargs)
            logger.info("Audio stream opened: %dHz, chunk=%d",
                        self._sample_rate, self._chunk_size)
            return True

        except Exception as exc:
            logger.error("Microphone init failed: %s", exc)
            if self._pa:
                self._pa.terminate()
                self._pa = None
            return False

    def read_chunk(self) -> Optional[bytes]:
        """Read one audio chunk from the microphone."""
        if self._stream is None:
            return None
        try:
            return self._stream.read(self._chunk_size,
                                     exception_on_overflow=False)
        except Exception as exc:
            logger.error("Mic read error: %s", exc)
            return None

    def stop(self) -> None:
        """Close the audio stream and terminate PyAudio."""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
            logger.info("Audio stream closed")

    def get_sample_rate(self) -> int:
        return self._sample_rate

    @staticmethod
    def enumerate_devices() -> None:
        """Print all audio input devices to console."""
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            print("\n  ╔══════════════════════════════════════╗")
            print("  ║       AUDIO INPUT DEVICES            ║")
            print("  ╠══════════════════════════════════════╣")
            found = 0
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d["maxInputChannels"] > 0:
                    name = d["name"][:30]
                    print(f"  ║  [{i}] {name:<30s}  ║")
                    found += 1
            if found == 0:
                print("  ║  No input devices found!            ║")
            print("  ╚══════════════════════════════════════╝\n")
            pa.terminate()
        except ImportError:
            print("  [WARN] PyAudio not installed — cannot enumerate devices")
        except Exception as exc:
            print(f"  [WARN] Audio enumeration failed: {exc}")
