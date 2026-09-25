"""TTS 引擎选择。"""

from __future__ import annotations

from .base import TTSEngine, TTSUnavailable
from .gpt_sovits import GPTSoVITSEngine
from .mock import MockTTSEngine

__all__ = ["TTSEngine", "TTSUnavailable", "GPTSoVITSEngine", "MockTTSEngine", "build_tts"]


def build_tts(cfg, mock: bool = False) -> TTSEngine:
    if mock or cfg.tts.engine == "mock":
        return MockTTSEngine(cfg)
    if cfg.tts.engine == "gpt_sovits":
        return GPTSoVITSEngine(cfg.services.gpt_sovits_base, cfg)
    raise ValueError(f"未知的 tts.engine: {cfg.tts.engine}")
