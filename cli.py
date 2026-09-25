"""命令行入口。

不经过 Gradio 直接跑一遍流程，用来在 P1/P2 阶段验证链路：

    python cli.py --video talk.mp4 --audio voice.wav --script-file script.txt --mock
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import heygem as heygem_mod
from core.config import ensure_dirs, load_config
from core.logs import setup_logging, soften_console_encoding
from core.pipeline import run_pipeline
from core.tts import build_tts

BAR_WIDTH = 24


def _read_script(args) -> str:
    if args.script_file:
        return Path(args.script_file).read_text(encoding="utf-8")
    if args.script is not None:
        return args.script
    raise SystemExit("必须提供 --script 或 --script-file")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="数字人口播视频生成（命令行）")
    p.add_argument("--video", required=True, help="正面讲话视频")
    p.add_argument("--audio", required=True, help="音色参考音频")
    p.add_argument("--script", help="讲稿正文")
    p.add_argument("--script-file", help="讲稿文件（UTF-8）")
    p.add_argument("--ref-text", help="参考音频对应的文字；留空则自动 ASR")
    p.add_argument("--config", help="配置文件路径，默认 ./config.yaml")
    p.add_argument("--mock", action="store_true", help="不连真服务，用本地假后端跑通链路")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 日志")
    return p


def main(argv: list[str] | None = None) -> int:
    soften_console_encoding()
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    ensure_dirs(cfg)
    setup_logging(cfg.paths.logs, args.verbose)

    if args.mock:
        print("⚠️  mock 模式：不连真服务，成片只是音视频合流，口型不会同步")

    last = -1.0
    try:
        pipeline = run_pipeline(
            video=Path(args.video),
            audio_ref=Path(args.audio),
            script=_read_script(args),
            cfg=cfg,
            tts_engine=build_tts(cfg, mock=args.mock),
            heygem=heygem_mod.build_heygem(cfg, mock=args.mock),
            ref_text=args.ref_text,
        )
        for progress in pipeline:
            percent = progress.percent * 100
            if percent - last < 1.0 and progress.output is None:
                continue
            last = percent
            filled = int(BAR_WIDTH * progress.percent)
            bar = "█" * filled + "·" * (BAR_WIDTH - filled)
            print(f"\r[{bar}] {percent:5.1f}%  {progress.message:<32}", end="", flush=True)
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
    except Exception as e:
        print(f"\n❌ {type(e).__name__}: {e}")
        return 1

    print(f"\n✅ 成片：{progress.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
