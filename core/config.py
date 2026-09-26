"""配置加载。

config.yaml 是唯一入口；缺失的键用 DEFAULTS 兜底，避免少写一行就在运行期抛 KeyError。

路径有两种解析方式，区别很重要：
  * workspace 这类"本机路径" —— 相对路径按项目根目录解析，支持 ~ 和 $VAR
  * heygem_host_dir 这类"要拿去做容器路径映射的路径" —— 只展开 ~，不做相对解析，
    因为容器路径（/code/data）在 Windows 上不是绝对路径，拼上项目根目录就废了
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"

DEFAULTS: dict = {
    "paths": {
        "heygem_host_dir": "~/heygem_data/face2face",
        "heygem_container_dir": "/code/data",
        "workspace": "workspace",
        "logs": "logs",
    },
    "services": {
        "heygem_base": "http://127.0.0.1:8383",
        "gpt_sovits_base": "http://127.0.0.1:9880",
    },
    "tts": {
        "engine": "gpt_sovits",
        "text_lang": "zh",
        "prompt_lang": "zh",
        "text_split_method": "cut5",
        "speed_factor": 1.0,
        "max_chars": 50,
        "pause_comma": 200,
        "pause_period": 400,
        "pause_paragraph": 800,
    },
    "audio": {
        "target_sample_rate": 16000,
        "target_channels": 1,
    },
    "video": {
        "target_fps": 25,
        "target_codec": "libx264",
        "target_pix_fmt": "yuv420p",
        "crf": 18,
    },
    "timeouts": {
        "heygem_submit": 30,
        "heygem_task": 3600,
        "poll_interval": 3,
        "tts_request": 120,
    },
    "limits": {
        "max_script_chars": 500,
        "min_video_seconds": 8,
        "min_audio_seconds": 3,
    },
}

# 这些键只做 ~ / 环境变量展开，不拼项目根目录
_RAW_PATH_KEYS = {("paths", "heygem_host_dir")}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _to_namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in value.items()})
    return value


def _expand_raw(p) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def _expand_local(p, root: Path) -> Path:
    path = _expand_raw(p)
    return path if path.is_absolute() else (root / path).resolve()


def load_config(path: str | Path | None = None) -> SimpleNamespace:
    """读取配置。文件不存在时只用默认值（mock 模式可以零配置跑起来）。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    raw = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg = _to_namespace(_deep_merge(DEFAULTS, raw))

    root = cfg_path.resolve().parent if cfg_path.exists() else PROJECT_ROOT
    for section, key in _RAW_PATH_KEYS:
        setattr(getattr(cfg, section), key, _expand_raw(getattr(getattr(cfg, section), key)))
    cfg.paths.workspace = _expand_local(cfg.paths.workspace, root)
    cfg.paths.logs = _expand_local(cfg.paths.logs, root)
    cfg.paths.heygem_container_dir = str(cfg.paths.heygem_container_dir).rstrip("/")
    return cfg


def ensure_dirs(cfg) -> None:
    # logs 是 git 忽略的，新克隆出来的工作区没有它，preflight 写报告会直接崩
    cfg.paths.logs.mkdir(parents=True, exist_ok=True)
    for sub in ("uploads", "temp", "outputs"):
        (cfg.paths.workspace / sub).mkdir(parents=True, exist_ok=True)
