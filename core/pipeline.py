"""流程编排：视频 + 音频素材 + 讲稿 → 口播视频。

run_pipeline 是生成器，逐段 yield Progress，方便 CLI 和 Gradio 共用同一套进度反馈。
"""

from __future__ import annotations

import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import media
from .heygem import HeyGemClient
from .tts import TTSEngine

logger = logging.getLogger(__name__)

# 各阶段在总进度条上的起点
P_CHECK, P_VIDEO, P_VOICE, P_SUBMIT, P_SYNTH, P_FINAL = 0.02, 0.05, 0.10, 0.50, 0.97, 0.98

SENTENCE_END = "。！？!?；;…"
CLAUSE_END = "，,、：:"
TRAILING = " \t\"'”’）)】》>"


@dataclass
class Progress:
    percent: float
    message: str
    output: Path | None = None


@dataclass
class Sentence:
    text: str
    pause_ms: int


def pause_after(text: str, is_paragraph_end: bool, cfg) -> int:
    """按句尾标点决定停顿长度。段落末尾停顿更长。"""
    if is_paragraph_end:
        return cfg.tts.pause_paragraph
    if text.rstrip(TRAILING).endswith(tuple(SENTENCE_END)):
        return cfg.tts.pause_period
    return cfg.tts.pause_comma


def _split_paragraph(para: str, max_chars: int) -> list[str]:
    parts = [p.strip() for p in re.findall(rf"[^{SENTENCE_END}]+[{SENTENCE_END}]*", para)]
    parts = [p for p in parts if p]

    out: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            out.append(part)
            continue
        # 超长句按逗号二次切分；单段仍超长就保持原样（再切会破坏语义）
        current = ""
        for clause in re.findall(rf"[^{CLAUSE_END}]+[{CLAUSE_END}]*", part):
            if len(current) + len(clause) <= max_chars:
                current += clause
            else:
                if current.strip():
                    out.append(current.strip())
                current = clause
        if current.strip():
            out.append(current.strip())
    return out


def split_script(text: str, cfg) -> list[Sentence]:
    """讲稿 → 分句 + 每句后的停顿时长。

    分句有两个约束：语义完整（按标点切）、单句不超长（长句 TTS 韵律会漂移）。
    空行分段，段末停顿更长。
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    sentences: list[Sentence] = []
    for para in paragraphs:
        chunks = _split_paragraph(para, cfg.tts.max_chars)
        for i, chunk in enumerate(chunks):
            sentences.append(Sentence(chunk, pause_after(chunk, i == len(chunks) - 1, cfg)))

    if sentences:
        sentences[-1].pause_ms = 0        # 末尾不留静音
    return sentences


def synthesize_script(engine, sentences, voice, work_dir: Path, cfg):
    """逐句合成再拼接。生成器，逐个 yield Progress，return 拼好的音频路径。

    逐句而不是整段一次合成，是为了避免长文本的韵律漂移。
    """
    clips: list[tuple[Path, int]] = []
    total = len(sentences)
    for i, sentence in enumerate(sentences):
        clip = work_dir / f"seg_{i:04d}.wav"
        engine.synthesize(sentence.text, voice, clip)
        clips.append((clip, sentence.pause_ms))
        percent = P_VOICE + (P_SUBMIT - 0.05 - P_VOICE) * (i + 1) / total
        yield Progress(percent, f"合成语音 {i + 1}/{total}")

    return media.concat_audio(clips, work_dir / "tts_raw.wav", cfg.audio)


def run_pipeline(
    video: Path,
    audio_ref: Path,
    script: str,
    cfg,
    tts_engine: TTSEngine,
    heygem: HeyGemClient,
    ref_text: str | None = None,
    run_id: str | None = None,
):
    """端到端流程。生成器：逐段 yield Progress，最后一段带 output。"""
    for label, path in (("视频素材", video), ("音频素材", audio_ref)):
        if not Path(path).exists():
            raise ValueError(f"{label}不存在：{path}")

    script = (script or "").strip()
    if not script:
        raise ValueError("讲稿为空")
    if len(script) > cfg.limits.max_script_chars:
        raise ValueError(
            f"讲稿 {len(script)} 字，超过 MVP 上限 {cfg.limits.max_script_chars} 字，请分次生成"
        )

    started = time.monotonic()
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    work_dir = cfg.paths.workspace / "temp" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    logger.info("=== 任务 %s 开始，工作目录 %s ===", run_id, work_dir)

    # ── 0. 服务检查 ──
    yield Progress(0.0, "检查服务状态")
    if not heygem.health():
        raise RuntimeError(
            f"HeyGem 未就绪（{heygem.base}）。请确认容器已启动："
            "docker-compose -f docker-compose-lite.yml up -d"
        )
    if not tts_engine.health():
        raise RuntimeError(f"TTS 服务未就绪（{tts_engine.name}）")

    # ── 1. 视频素材 ──
    yield Progress(P_CHECK, "处理视频素材")
    video_std = media.standardize_video(Path(video), work_dir / "input.mp4", cfg.video)
    video_seconds = media.duration(video_std)
    if video_seconds < cfg.limits.min_video_seconds:
        raise ValueError(
            f"视频素材仅 {video_seconds:.1f}s，建议至少 {cfg.limits.min_video_seconds}s"
        )

    # ── 2. 音色 ──
    yield Progress(P_VIDEO, "处理音频素材 / 提取音色")
    audio_std = media.standardize_audio(Path(audio_ref), work_dir / "ref.wav", cfg.audio)
    audio_seconds = media.duration(audio_std)
    if audio_seconds < cfg.limits.min_audio_seconds:
        raise ValueError(
            f"音频素材仅 {audio_seconds:.1f}s，建议至少 {cfg.limits.min_audio_seconds}s"
        )
    voice = tts_engine.prepare_voice(audio_std, ref_text)

    # ── 3. 分句 + 逐句合成 ──
    sentences = split_script(script, cfg)
    logger.info("讲稿切分为 %d 句，首句：%s", len(sentences), sentences[0].text)
    yield Progress(P_VOICE, f"讲稿切分为 {len(sentences)} 句，开始合成语音")
    tts_raw = yield from synthesize_script(tts_engine, sentences, voice, work_dir, cfg)

    # ── 4. 统一采样率（GPT-SoVITS 输出通常是 32k/48k，HeyGem 未必吃） ──
    yield Progress(P_SUBMIT - 0.03, "统一音频格式")
    tts_final = media.standardize_audio(tts_raw, work_dir / "tts_final.wav", cfg.audio)
    tts_shared = media.copy_to_shared(tts_final, heygem.mapper)
    video_shared = media.copy_to_shared(video_std, heygem.mapper)

    # ── 5. 提交合成 ──
    yield Progress(P_SUBMIT, "提交口型合成任务")
    code = heygem.submit(tts_shared, video_shared)

    # ── 6. 轮询 ──
    poll = heygem.poll(code)
    while True:
        try:
            status = next(poll)
        except StopIteration as stop:
            out_host: Path = stop.value
            break
        percent = P_SUBMIT + (P_SYNTH - P_SUBMIT) * status.percent / 100
        yield Progress(percent, f"口型合成 {status.percent:.0f}%")

    # ── 7. 整理产物 ──
    yield Progress(P_FINAL, "整理产物")
    final = cfg.paths.workspace / "outputs" / f"{code}.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_host, final)
    elapsed = time.monotonic() - started
    logger.info("=== 任务 %s 完成，用时 %.1fs，产物 %s ===", run_id, elapsed, final)

    yield Progress(1.0, f"完成，总用时 {elapsed:.1f}s", output=final)
