"""ffmpeg 封装 + 路径映射。

这里是整个 MVP 最容易出错的地方：
HeyGem 的 `audio_url` / `video_url` 不是文件上传，而是**容器内路径**。
所有要传给 HeyGem 的文件必须先复制进共享目录，再经 PathMapper 转成容器路径。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# HeyGem 的 code 只由我们自己生成，用固定前缀方便在 temp/ 里辨认
SHARED_PREFIX = "svg_"


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args: list[str], desc: str) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise FFmpegError("找不到 ffmpeg，请确认已安装并加入 PATH") from e
    if proc.returncode != 0:
        raise FFmpegError(f"{desc} 失败：\n{' '.join(cmd)}\n{proc.stderr.strip()}")


def probe(path: Path) -> dict:
    try:
        proc = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as e:
        raise FFmpegError("找不到 ffprobe，请确认已安装并加入 PATH") from e
    if proc.returncode != 0:
        raise FFmpegError(f"无法读取媒体信息 {path}：{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


class PathMapper:
    """宿主路径 ⇄ 容器路径 双向转换。

    所有涉及 HeyGem API 的路径都必须经过这里，禁止裸传路径。
    """

    def __init__(self, host_dir: Path, container_dir: str):
        self.host_dir = Path(host_dir).resolve()
        self.container_dir = str(container_dir).rstrip("/")

    def to_container(self, host_path: Path) -> str:
        host_path = Path(host_path).resolve()
        try:
            rel = host_path.relative_to(self.host_dir)
        except ValueError:
            raise ValueError(
                f"路径 {host_path} 不在共享目录 {self.host_dir} 下。"
                f"所有要传给 HeyGem 的文件必须先复制到共享目录。"
            ) from None
        return f"{self.container_dir}/{rel.as_posix()}"

    def to_host(self, container_path: str) -> Path:
        rel = str(container_path).replace(self.container_dir, "").lstrip("/")
        return self.host_dir / rel

    def output_container_path(self, code: str) -> str:
        """HeyGem 约定的成片位置。"""
        return f"{self.container_dir}/temp/{code}-r.mp4"

    def __repr__(self) -> str:
        return f"PathMapper({self.host_dir} ⇄ {self.container_dir})"


def copy_to_shared(path: Path, mapper: PathMapper, name: str | None = None) -> Path:
    """把文件复制进共享目录，返回宿主侧的共享路径。

    用固定前缀重命名，避免与 HeyGem 自己的中间产物撞名；
    目标已存在且大小一致时跳过，重复调试时省一次拷贝。
    """
    path = Path(path).resolve()
    mapper.host_dir.mkdir(parents=True, exist_ok=True)
    dest = mapper.host_dir / (name or f"{SHARED_PREFIX}{path.name}")
    if not (dest.exists() and dest.stat().st_size == path.stat().st_size):
        shutil.copy2(path, dest)
    return dest


def standardize_video(src: Path, dst: Path, cfg) -> Path:
    """统一视频格式。HeyGem 对编码/像素格式敏感，进来先规整一遍。

    音轨直接丢掉——声音由 TTS 那条线单独提供。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i", str(src),
            "-an",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",   # libx264 要求宽高为偶数
            "-c:v", cfg.target_codec,
            "-preset", "medium",
            "-crf", str(cfg.crf),
            "-pix_fmt", cfg.target_pix_fmt,
            "-r", str(cfg.target_fps),
            str(dst),
        ],
        "视频标准化",
    )
    return dst


def standardize_audio(src: Path, dst: Path, cfg) -> Path:
    """统一音频格式（采样率 / 声道 / PCM）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i", str(src),
            "-vn",
            "-ac", str(cfg.target_channels),
            "-ar", str(cfg.target_sample_rate),
            "-c:a", "pcm_s16le",
            str(dst),
        ],
        "音频标准化",
    )
    return dst


def concat_audio(clips: list[tuple[Path, int]], dst: Path, cfg) -> Path:
    """把分句音频按顺序拼起来，每段后面补对应时长的静音。

    clips: [(音频路径, 之后的停顿时长毫秒), ...]
    用 apad 在每段尾部补静音再 concat，比生成一堆静音文件干净；
    用 filter 而不是 concat demuxer，是因为各段采样率不一致时 demuxer 会直接报错。
    """
    if not clips:
        raise ValueError("没有可拼接的音频片段")

    dst.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    for path, _ in clips:
        args += ["-i", str(path)]

    chain = [f"[{i}:a]apad=pad_dur={pause / 1000:.3f}[a{i}]" for i, (_, pause) in enumerate(clips)]
    if len(clips) == 1:
        chain.append("[a0]anull[out]")
    else:
        parts = "".join(f"[a{i}]" for i in range(len(clips)))
        chain.append(f"{parts}concat=n={len(clips)}:v=0:a=1[out]")

    run_ffmpeg(
        [
            *args,
            "-filter_complex", ";".join(chain),
            "-map", "[out]",
            "-ar", str(cfg.target_sample_rate),
            "-ac", str(cfg.target_channels),
            "-c:a", "pcm_s16le",
            str(dst),
        ],
        "音频拼接",
    )
    return dst
