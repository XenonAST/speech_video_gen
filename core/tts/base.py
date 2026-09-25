"""TTS 引擎抽象。

抽这一层的唯一理由：让 mock 和真引擎在编排代码里长得一样，
从而在没有 GPT-SoVITS 服务的情况下也能验证整条链路。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSUnavailable(RuntimeError):
    pass


class TTSEngine(ABC):
    name = "tts"

    @abstractmethod
    def health(self) -> bool:
        """服务是否就绪。"""

    @abstractmethod
    def prepare_voice(self, ref_audio: Path, ref_text: str | None = None) -> dict:
        """准备音色。ref_text 为 None 时自动转写，转不了则退化为无参考文本模式。"""

    @abstractmethod
    def synthesize(self, text: str, voice: dict, out_path: Path) -> Path:
        """合成单句音频。"""
