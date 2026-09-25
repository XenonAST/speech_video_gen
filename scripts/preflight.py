"""P0 手测自动化：跑一遍 docs/MVP方案.md 第 3 章的检查项。

    python scripts/preflight.py --video test.mp4 --audio test.wav

不传 --video/--audio 就只做静态检查（环境、挂载点、服务健康）。
报告写到 logs/preflight-<时间戳>.json，重点是里面原样保留的
HeyGem submit / query 响应 —— 拿它去收紧 core/heygem.py 里的解析逻辑。
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from core import heygem as heygem_mod  # noqa: E402
from core import media  # noqa: E402
from core.config import ensure_dirs, load_config  # noqa: E402
from core.logs import soften_console_encoding  # noqa: E402
from core.tts import build_tts  # noqa: E402

COMPOSE_CANDIDATES = [
    "~/Duix-Avatar/deploy/docker-compose-lite.yml",
    "~/duix-avatar/deploy/docker-compose-lite.yml",
    "~/Duix.Avatar/deploy/docker-compose-lite.yml",
]

checks: list[dict] = []


def record(name: str, ok: bool | None, detail: str, **extra) -> None:
    mark = "✓" if ok else ("–" if ok is None else "✗")
    print(f"[{mark}] {name}: {detail}")
    checks.append({"name": name, "ok": ok, "detail": detail, **extra})


def check_environment(cfg) -> None:
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        record(f"{tool}", path is not None, path or "未找到，请装好并加入 PATH")

    usage = shutil.disk_usage(cfg.paths.workspace.parent)
    free_gb = usage.free / 1024**3
    record("磁盘可用", free_gb >= 100, f"{free_gb:.1f} GB（要求 ≥100GB）")

    if platform.system() == "Linux":
        meminfo = dict(
            line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line
        )
        total_gb = int(meminfo["MemTotal"].split()[0]) / 1024**2
        avail_gb = int(meminfo["MemAvailable"].split()[0]) / 1024**2
        record("内存", total_gb >= 32, f"总计 {total_gb:.1f} GB / 可用 {avail_gb:.1f} GB（要求 ≥32GB）")
    else:
        record("内存", None, f"非 Linux（{platform.system()}），跳过；本项需在 WSL2 内运行")

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        )
        record("GPU", out.returncode == 0, out.stdout.strip() or out.stderr.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        record("GPU", False, f"nvidia-smi 不可用：{e}")


def check_compose(cfg, compose_path: str | None) -> None:
    candidates = [Path(compose_path).expanduser()] if compose_path else []
    candidates += [Path(p).expanduser() for p in COMPOSE_CANDIDATES]

    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        record("挂载点", None, "没找到 docker-compose-lite.yml，请用 --compose 指定", volumes=[])
        return

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    volumes = []
    for service, spec in (data.get("services") or {}).items():
        if not isinstance(spec, dict):
            continue
        for vol in spec.get("volumes") or []:
            if isinstance(vol, str) and ":" in vol:
                parts = vol.split(":")
                volumes.append({"service": service, "host": parts[0], "container": parts[1]})

    matched = [v for v in volumes if v["container"].rstrip("/") == cfg.paths.heygem_container_dir]
    if matched:
        detail = f"{matched[0]['host']} ⇄ {matched[0]['container']}（与 config.yaml 一致）"
    elif volumes:
        detail = (
            f"config.yaml 写着 {cfg.paths.heygem_container_dir}，compose 里没这个挂载点。"
            f"实际：{[(v['host'], v['container']) for v in volumes]}"
        )
    else:
        detail = f"compose 里没有 volumes 定义（{path}）"

    record("挂载点", bool(matched), detail, compose=str(path), volumes=volumes)
    if not matched:
        print(f"     ↳ 请把 config.yaml 的 heygem_host_dir/heygem_container_dir 改成上面实际的映射")


def check_services(cfg, video: str | None, audio: str | None) -> dict:
    result: dict = {}

    client = heygem_mod.build_heygem(cfg)
    healthy = client.health()
    record("HeyGem /health", healthy, f"{cfg.services.heygem_base} → {'200' if healthy else '无响应'}")

    tts = build_tts(cfg)
    tts_ok = tts.health()
    record("TTS 服务", tts_ok, f"{cfg.services.gpt_sovits_base} → {'可用' if tts_ok else '无响应'}")

    if not (video and audio):
        record("端到端提交", None, "未提供 --video/--audio，跳过")
        return result
    if not healthy:
        record("端到端提交", None, "HeyGem 未就绪，跳过")
        return result

    try:
        video_shared = media.copy_to_shared(Path(video), client.mapper)
        audio_shared = media.copy_to_shared(Path(audio), client.mapper)
        record(
            "共享目录可写",
            True,
            f"{video_shared.name} / {audio_shared.name} → {client.mapper.to_container(video_shared)}",
        )
    except Exception as e:
        record("共享目录可写", False, f"{type(e).__name__}: {e}")
        return result

    code = f"p0{int(time.time()) % 10**8}"
    try:
        returned = client.submit(audio_shared, video_shared, code=code)
        record("submit", True, f"code={returned}", submit_code=returned)
        code = returned
    except Exception as e:
        record("submit", False, f"{type(e).__name__}: {e}")
        return result

    print("     轮询中（Ctrl-C 可中断）…")
    try:
        poll = client.poll(code)
        while True:
            try:
                status = next(poll)
            except StopIteration as stop:
                out = stop.value
                break
            print(f"     {status.percent:.0f}%  {json.dumps(status.raw, ensure_ascii=False)[:160]}")
        record("合成产物", out.exists(), str(out))
        result["output"] = str(out)
    except Exception as e:
        record("合成产物", False, f"{type(e).__name__}: {e}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="P0 手测自动化")
    parser.add_argument("--video", help="测试用正面讲话视频（≥10 秒）")
    parser.add_argument("--audio", help="测试用音频（≥5 秒）")
    parser.add_argument("--compose", help="docker-compose-lite.yml 路径")
    parser.add_argument("--config", help="配置文件路径")
    args = parser.parse_args()
    soften_console_encoding()

    cfg = load_config(args.config)
    ensure_dirs(cfg)

    print("── 环境 ──")
    check_environment(cfg)
    print("\n── 挂载点 ──")
    check_compose(cfg, args.compose)
    print("\n── 服务 ──")
    extra = check_services(cfg, args.video, args.audio)

    report = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "paths": {
            "heygem_host_dir": str(cfg.paths.heygem_host_dir),
            "heygem_container_dir": cfg.paths.heygem_container_dir,
        },
        "checks": checks,
        **extra,
    }
    report_path = cfg.paths.logs / f"preflight-{time.strftime('%Y%m%d-%H%M%S')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [c for c in checks if c["ok"] is False]
    print(f"\n报告：{report_path}")
    print(f"结论：{len(checks) - len(failed)}/{len(checks)} 项通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
