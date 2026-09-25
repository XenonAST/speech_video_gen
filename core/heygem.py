"""HeyGem (Duix.Avatar lite) 合成客户端。

协议以 http://127.0.0.1:8383/docs 为准 —— 社区文档不可靠，P0 时对着 Swagger 核对。

⚠️ query 的返回结构尚未在真机上验证。这里做的是**宽容解析**：
   依次尝试 progress/percent、success/status/done 等字段名，并把每次原始响应写进日志。
   P0 跑完后拿 logs/ 里的原始 JSON 把 _read_status / _extract_percent 收紧。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import media

logger = logging.getLogger(__name__)

DONE_STATUSES = {"success", "succeeded", "done", "completed", "complete", "finish", "finished", "2"}
FAIL_STATUSES = {"failed", "fail", "error", "exception", "-1"}
RUNNING_STATUSES = {"running", "processing", "pending", "queue", "queued", "waiting", "0", "1"}


class HeyGemError(RuntimeError):
    pass


@dataclass
class TaskStatus:
    """归一化后的任务状态。raw 保留原始响应，方便 P0 对齐字段。"""

    percent: float
    raw: dict = field(default_factory=dict)


def _unwrap(resp: dict) -> dict:
    """剥掉 {"code":0,"data":{...}} 这类外层包装。"""
    for key in ("data", "result"):
        inner = resp.get(key)
        if isinstance(inner, dict) and any(
            k in inner for k in ("status", "progress", "percent", "state", "msg")
        ):
            return inner
    return resp


def _extract_percent(info: dict) -> float:
    """取进度百分比。0<x<1 视为小数比例，其余按百分比处理。"""
    for key in ("progress", "percent", "percentage", "rate", "schedule"):
        if key in info:
            try:
                value = float(info[key])
            except (TypeError, ValueError):
                continue
            if 0 < value < 1:
                value *= 100
            return max(0.0, min(100.0, value))
    return 0.0


def _read_status(info: dict) -> tuple[str, bool, bool]:
    """返回 (原始状态串, 是否完成, 是否失败)。"""
    status = ""
    for key in ("status", "state", "code", "msg", "message"):
        if key in info and not isinstance(info[key], dict):
            status = str(info[key])
            break
    lowered = status.strip().lower()
    done = lowered in DONE_STATUSES or info.get("success") is True
    failed = lowered in FAIL_STATUSES or info.get("success") is False
    return status, done, failed


class HeyGemClient:
    """Duix.Avatar (lite) 视频合成客户端。"""

    def __init__(self, base_url: str, mapper: media.PathMapper, cfg):
        self.base = base_url.rstrip("/")
        self.mapper = mapper
        self.cfg = cfg

    # ── HTTP ──

    def health(self) -> bool:
        try:
            return requests.get(f"{self.base}/health", timeout=5).status_code == 200
        except requests.RequestException as e:
            logger.debug("health 检查失败: %s", e)
            return False

    def submit(self, audio_host: Path, video_host: Path, code: str | None = None) -> str:
        """提交合成任务。audio/video 传宿主路径，内部自动转容器路径。"""
        code = code or uuid.uuid4().hex[:12]
        payload = {
            "audio_url": self.mapper.to_container(audio_host),
            "video_url": self.mapper.to_container(video_host),
            "code": code,
            "chaofen": 0,
            "watermark_switch": 0,
            "pn": 1,
        }
        logger.info("提交合成任务 code=%s payload=%s", code, payload)
        try:
            r = requests.post(
                f"{self.base}/easy/submit", json=payload, timeout=self.cfg.timeouts.heygem_submit
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise HeyGemError(f"提交合成任务失败：{e}") from e

        resp = r.json()
        logger.info("submit 响应: %s", resp)
        if resp.get("success") is False:
            raise HeyGemError(f"提交被拒绝：{resp}")
        return resp.get("code", code) if isinstance(resp.get("code"), str) else code

    def query(self, code: str) -> dict:
        try:
            r = requests.get(f"{self.base}/easy/query", params={"code": code}, timeout=30)
            r.raise_for_status()
            resp = r.json()
        except (requests.RequestException, ValueError) as e:
            raise HeyGemError(f"查询任务 {code} 失败：{e}") from e
        # 原始响应完整落盘：P0 就是靠这行日志对齐字段名
        logger.debug("query=%s 原始响应: %s", code, resp)
        return resp

    # ── 轮询 ──

    def poll(self, code: str):
        """轮询直到结束。逐次 yield TaskStatus，结束时 return 成片的宿主路径。"""
        start = time.monotonic()
        timeout = self.cfg.timeouts.heygem_task
        interval = self.cfg.timeouts.poll_interval
        while True:
            if time.monotonic() - start > timeout:
                raise HeyGemError(f"任务 {code} 超过 {timeout}s 未完成")
            raw = self.query(code)
            info = _unwrap(raw)
            status, done, failed = _read_status(info)

            if done:
                out_container = (
                    info.get("result")
                    or info.get("output")
                    or info.get("video_url")
                    or self.mapper.output_container_path(code)
                )
                out_host = self.mapper.to_host(str(out_container))
                logger.info("任务 %s 完成，产物 %s", code, out_host)
                return out_host
            if failed:
                raise HeyGemError(f"任务 {code} 失败：{info}")

            if status.strip().lower() not in RUNNING_STATUSES:
                logger.warning("任务 %s 的状态 %r 未识别，按进行中处理", code, status)
            yield TaskStatus(percent=_extract_percent(info), raw=raw)
            time.sleep(interval)


class MockHeyGemClient(HeyGemClient):
    """不依赖 Docker 的假后端。

    只覆盖 HTTP 那一层（health/submit/query），poll 与路径映射复用真实实现——
    这样编排逻辑、进度回调、错误分支都能在没有容器的情况下验证。

    出片方式是把输入视频与音频直接合流：口型当然对不上，只保证链路通。
    """

    def __init__(self, shared_dir: Path, cfg, simulate_seconds: float = 2.0):
        super().__init__("mock://heygem", media.PathMapper(shared_dir, "/code/data"), cfg)
        self.simulate_seconds = simulate_seconds
        self._tasks: dict[str, dict] = {}

    def health(self) -> bool:
        return True

    def submit(self, audio_host: Path, video_host: Path, code: str | None = None) -> str:
        code = code or uuid.uuid4().hex[:12]
        audio_container = self.mapper.to_container(audio_host)
        video_container = self.mapper.to_container(video_host)

        out_host = self.mapper.to_host(self.mapper.output_container_path(code))
        out_host.parent.mkdir(parents=True, exist_ok=True)
        media.run_ffmpeg(
            [
                "-i", str(video_host),
                "-i", str(audio_host),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                str(out_host),
            ],
            "mock 合流",
        )
        self._tasks[code] = {
            "started": time.monotonic(),
            "result": self.mapper.output_container_path(code),
            "audio_url": audio_container,
            "video_url": video_container,
        }
        logger.info("[mock] 已提交 code=%s 音频=%s 视频=%s", code, audio_container, video_container)
        return code

    def query(self, code: str) -> dict:
        task = self._tasks.get(code)
        if task is None:
            return {"status": "failed", "msg": f"未知任务 {code}"}
        elapsed = time.monotonic() - task["started"]
        percent = min(100.0, elapsed / max(self.simulate_seconds, 0.01) * 100)
        if percent < 100:
            return {"status": "running", "progress": percent}
        return {"status": "success", "progress": 100, "result": task["result"]}
