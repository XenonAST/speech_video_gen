"""假 TTS：用正弦波占位，不依赖任何服务。

时长按字数估算，音高随句子略微变化 —— 这样拼出来的音频能听出句子边界，
分句和拼接逻辑出问题时耳朵就能发现。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import media
from .base import TTSEngine

logger = logging.getLogger(__name__)

CHARS_PER_SECOND = 5.0
MIN_SECONDS = 0.4


class MockTTSEngine(TTSEngine):
    name = "mock"

    def __init__(self, cfg):
        self.cfg = cfg

    def health(self) -> bool:
        return True

    def prepare_voice(self, ref_audio: Path, ref_text: str | None = None) -> dict:
        voice = {"ref_audio_path": str(Path(ref_audio).resolve()), "ref_text": ref_text or ""}
        logger.info("[mock] 音色已就绪：%s", voice["ref_audio_path"])
        return voice

    def synthesize(self, text: str, voice: dict, out_path: Path) -> Path:
        seconds = max(MIN_SECONDS, len(text) / CHARS_PER_SECOND)
        freq = 300 + (len(text) % 7) * 40
        out_path.parent.mkdir(parents=True, exist_ok=True)
        media.run_ffmpeg(
            [
                "-f", "lavfi",
                "-i", f"sine=frequency={freq}:duration={seconds:.2f}:sample_rate=32000",
                "-ac", str(self.cfg.audio.target_channels),
                "-c:a", "pcm_s16le",
                str(out_path),
            ],
            "mock 合成",
        )
        logger.debug("[mock] 合成 %r -> %s（%.2fs）", text, out_path.name, seconds)
        return out_path
