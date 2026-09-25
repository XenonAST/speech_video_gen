"""日志配置。同时写控制台和 logs/pipeline.log。

HeyGem 的原始响应会以 DEBUG 级别落在文件里 —— P0 对齐字段名就靠它。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_configured = False

FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    global _configured
    if _configured:
        return
    _configured = True

    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(FORMAT, datefmt="%H:%M:%S"))
    root.addHandler(console)

    file_handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(file_handler)

    # requests/urllib3 的连接日志太吵
    logging.getLogger("urllib3").setLevel(logging.WARNING)
