"""参考音频转写。

只为一件事服务：给 GPT-SoVITS 提供 prompt_text（参考音频对应的文字）。
没有它也能跑 —— GPT-SoVITS 会退化成 ref_free 模式，只是音色相似度会降一些。

FunASR 是可选的。离线环境要先手工把模型下好，否则这里会返回 None。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_model = None
_load_failed = False


def available() -> bool:
    try:
        import funasr  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model():
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    try:
        from funasr import AutoModel

        logger.info("加载 FunASR 模型（首次可能需要下载）…")
        _model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc")
    except Exception as e:  # 模型下载失败、离线未预置模型等
        logger.warning("FunASR 不可用（%s），参考文本将回退为 ref_free 模式", e)
        _load_failed = True
    return _model


def transcribe(audio_path: Path) -> str | None:
    """转写参考音频。失败返回 None，调用方需自行兜底。"""
    model = _get_model()
    if model is None:
        return None
    try:
        result = model.generate(input=str(audio_path), batch_size_s=300)
    except Exception as e:
        logger.warning("ASR 转写失败：%s", e)
        return None

    text = "".join(seg.get("text", "") for seg in result).strip()
    if not text:
        logger.warning("ASR 返回空文本：%s", audio_path)
        return None
    logger.info("参考音频转写结果：%s", text)
    return text
