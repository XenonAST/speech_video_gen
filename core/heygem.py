"""HeyGem (Duix.Avatar lite) 合成客户端。

协议以 P0 实测为准：8383 是 **Flask** 应用（不是 FastAPI，所以没有 /docs），
只注册了两个路由 —— `/easy/submit` 与 `/easy/query`，**没有 /health**。

响应统一是 {code, success, msg, data}，code 为数字码：

    10000 成功   10001 忙碌中   10002 参数异常   10004 任务不存在   9999 系统异常

⚠️ 10001 和 10004 的 success 同样是 true，判定必须看 code 而非 success。

query 的业务数据嵌在 data 里，status 枚举：run=1 / success=2 / error=3。
⚠️ 任务进入终态（success/error）后服务端会**立即把任务从字典里删掉**，
   再查返回 10004 —— 轮询必须在首次拿到终态时收手，不能重复查。
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

# /easy/* 的外层响应码
CODE_SUCCESS = 10000
CODE_BUSY = 10001
CODE_BAD_PARAM = 10002
CODE_NO_TASK = 10004

# data.status 枚举
STATUS_RUN = 1
STATUS_SUCCESS = 2
STATUS_ERROR = 3


class HeyGemError(RuntimeError):
    pass


@dataclass
class TaskStatus:
    """归一化后的任务状态。raw 保留原始响应，方便 P0 对齐字段。"""

    percent: float
    raw: dict = field(default_factory=dict)


def _unwrap(resp: dict) -> dict:
    """业务数据嵌在 data 里。拿不到就原样返回，由 _read_status 判为未识别。"""
    data = resp.get("data")
    return data if isinstance(data, dict) else resp


def _extract_percent(info: dict) -> float:
    """取 data.progress。0<x<1 视为小数比例，其余按百分比处理。"""
    try:
        value = float(info.get("progress"))
    except (TypeError, ValueError):
        return 0.0
    if 0 < value < 1:
        value *= 100
    return max(0.0, min(100.0, value))


def _read_status(info: dict) -> tuple[int | None, bool, bool]:
    """返回 (status, 是否完成, 是否失败)。status 见上方枚举，未识别为 None。"""
    try:
        status = int(info.get("status"))
    except (TypeError, ValueError):
        return None, False, False
    return status, status == STATUS_SUCCESS, status == STATUS_ERROR


class HeyGemClient:
    """Duix.Avatar (lite) 视频合成客户端。"""

    def __init__(self, base_url: str, mapper: media.PathMapper, cfg):
        self.base = base_url.rstrip("/")
        self.mapper = mapper
        self.cfg = cfg

    # ── HTTP ──

    def health(self) -> bool:
        """服务没有 /health。用一个必然不存在的 code 探 /easy/query：
        返回 10004「任务不存在」说明服务活着，且确实是它。"""
        try:
            r = requests.get(f"{self.base}/easy/query", params={"code": "__ping__"}, timeout=5)
            return r.status_code == 200 and r.json().get("code") == CODE_NO_TASK
        except (requests.RequestException, ValueError) as e:
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
        # 忙碌(10001)与任务不存在(10004)的 success 都是 true，只能看 code
        if resp.get("code") != CODE_SUCCESS:
            raise HeyGemError(f"提交被拒绝 code={resp.get('code')}：{resp.get('msg')}")
        return code

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
                # 不取 data.result —— 实测它不含 temp/，指向的位置根本不存在。
                # 服务端内部工作目录是 temp/{code}/，成片落在 temp/{code}-r.mp4。
                out_host = self.mapper.to_host(self.mapper.output_container_path(code))
                if not out_host.exists():
                    raise HeyGemError(f"任务 {code} 已完成，但产物不在约定位置：{out_host}")
                logger.info("任务 %s 完成，产物 %s", code, out_host)
                return out_host
            if failed:
                raise HeyGemError(f"任务 {code} 失败：{info.get('msg') or info}")
            if status is None:
                if raw.get("code") == CODE_NO_TASK:
                    raise HeyGemError(
                        f"任务 {code} 已不在服务端"
                        "（终态任务会被立即回收，可能已被取走）"
                    )
                logger.warning("任务 %s 状态未识别：%s", code, raw)

            yield TaskStatus(percent=_extract_percent(info), raw=raw)
            time.sleep(interval)


def build_heygem(cfg, mock: bool = False) -> HeyGemClient:
    """构造客户端。mock 模式下共享目录落在 workspace 内，不碰真实挂载点。"""
    if mock:
        return MockHeyGemClient(cfg.paths.workspace / "mock_shared", cfg)
    return HeyGemClient(
        cfg.services.heygem_base,
        media.PathMapper(cfg.paths.heygem_host_dir, cfg.paths.heygem_container_dir),
        cfg,
    )


class MockHeyGemClient(HeyGemClient):
    """不依赖 Docker 的假后端。

    只覆盖 HTTP 那一层（health/submit/query），poll 与路径映射复用真实实现——
    这样编排逻辑、进度回调、错误分支都能在没有容器的情况下验证。

    出片方式是把输入视频与音频直接合流：口型当然对不上，只保证链路通。
    视频不够长就循环播放——真机上成片时长也是由音频决定的，这里保持同样语义，
    免得调试时对着一个被截断的成片找错。
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
        # 用显式 -t 而不是 -shortest：stream_loop 下 -shortest 不会截断 copy 出来的视频流
        seconds = media.duration(audio_host)
        media.run_ffmpeg(
            [
                "-stream_loop", "-1", "-i", str(video_host),
                "-i", str(audio_host),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac",
                "-t", f"{seconds:.3f}",
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
        """按真实契约的形状返回 —— mock 下验证过的解析逻辑，换真服务才作数。"""
        task = self._tasks.get(code)
        if task is None:
            return {"code": CODE_NO_TASK, "success": True, "msg": "任务不存在", "data": {}}
        elapsed = time.monotonic() - task["started"]
        percent = min(100.0, elapsed / max(self.simulate_seconds, 0.01) * 100)
        done = percent >= 100
        return {
            "code": CODE_SUCCESS,
            "success": True,
            "msg": "",
            "data": {
                "code": code,
                "status": STATUS_SUCCESS if done else STATUS_RUN,
                "progress": percent,
                "result": task["result"] if done else "",
                "msg": "",
            },
        }
