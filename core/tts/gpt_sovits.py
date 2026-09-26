"""GPT-SoVITS 客户端（api_v2.py）。

零样本克隆：给一段参考音频 + 它的文字，就能用这个音色念任意文本。
参考文本拿不到时走 ref_free 模式（服务端忽略 prompt_text）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from .. import asr
from .base import TTSEngine, TTSUnavailable

logger = logging.getLogger(__name__)


class GPTSoVITSEngine(TTSEngine):
    name = "gpt_sovits"

    def __init__(self, base_url: str, cfg):
        self.base = base_url.rstrip("/")
        self.cfg = cfg

    def health(self) -> bool:
        # api_v2 没有 /health；能拿到任何 HTTP 响应就说明端口是活的
        try:
            requests.get(f"{self.base}/docs", timeout=5)
            return True
        except requests.RequestException as e:
            logger.debug("GPT-SoVITS 探测失败: %s", e)
            return False

    def prepare_voice(self, ref_audio: Path, ref_text: str | None = None) -> dict:
        ref_audio = Path(ref_audio).resolve()
        if ref_text:
            ref_text = ref_text.strip()

        ref_free = not ref_text
        if ref_free:
            # 注意：路径必须是 GPT-SoVITS 所在机器能读到的路径。
            # 同机部署时没问题；一旦分机器部署，这里要先复制过去。
            ref_text = asr.transcribe(ref_audio)
            ref_free = not ref_text
            if ref_free:
                logger.warning("参考文本缺失且 ASR 不可用，改用 ref_free 模式（音色相似度会下降）")

        voice = {
            "ref_audio_path": str(ref_audio),
            "ref_text": ref_text or "",
            "ref_free": ref_free,
        }
        logger.info("音色就绪：ref_audio=%s ref_free=%s", ref_audio.name, ref_free)
        return voice

    def synthesize(self, text: str, voice: dict, out_path: Path) -> Path:
        payload = {
            "text": text,
            "text_lang": self.cfg.tts.text_lang,
            "ref_audio_path": voice["ref_audio_path"],
            "prompt_lang": self.cfg.tts.prompt_lang,
            "text_split_method": self.cfg.tts.text_split_method,
            "speed_factor": self.cfg.tts.speed_factor,
            "media_type": "wav",
            "streaming_mode": False,
        }
        # 服务端 TTS_Request 里没有 ref_free 字段，发了也会被 pydantic 丢掉。
        # 无参考文本的正确表达是 prompt_text 留空，服务端据此走 no_prompt_text 分支。
        if not voice.get("ref_free"):
            payload["prompt_text"] = voice["ref_text"]

        try:
            r = requests.post(f"{self.base}/tts", json=payload, timeout=self.cfg.timeouts.tts_request)
            r.raise_for_status()
        except requests.RequestException as e:
            raise TTSUnavailable(f"GPT-SoVITS 请求失败：{e}") from e

        # 服务端出错时会返回 JSON 而不是音频，别把它当 wav 写进文件
        if "application/json" in r.headers.get("Content-Type", ""):
            raise TTSUnavailable(f"GPT-SoVITS 返回错误：{r.text[:300]}")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return out_path
