#!/usr/bin/env python3
"""Shipyard Bay → MCP 桥接器
把 AstrBot 的 Shipyard 沙盒(Bay REST API)包装成 MCP 服务器,
让 MaiBot(或任何 MCP 客户端)能调用沙盒执行 Python/Shell。

环境变量:
  SHIPYARD_URL    Bay 地址, 默认 http://shipyard:8156
  SHIPYARD_TOKEN  Bay ACCESS_TOKEN
监听: 0.0.0.0:8124 (streamable-http, 路径 /mcp)
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs
from typing import Any
import hmac

import httpx
from fastmcp import FastMCP
import uvicorn

BAY_URL = os.environ.get("SHIPYARD_URL", "http://shipyard:8156").rstrip("/")
TOKEN = os.environ.get("SHIPYARD_TOKEN", "")
REQUIRE_SHIPYARD_TOKEN = os.environ.get("REQUIRE_SHIPYARD_TOKEN", "true").lower() not in {"0", "false", "no"}
HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8124"))
MCP_PATH = os.environ.get("BRIDGE_PATH", "/mcp")
if not MCP_PATH.startswith("/"):
    MCP_PATH = "/" + MCP_PATH
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "").strip()
REQUIRE_BRIDGE_TOKEN = os.environ.get("REQUIRE_BRIDGE_TOKEN", "true").lower() not in {"0", "false", "no"}
ALLOW_QUERY_TOKEN = os.environ.get("BRIDGE_ALLOW_QUERY_TOKEN", "true").lower() not in {"0", "false", "no"}
ENABLE_SHELL = os.environ.get("BRIDGE_ENABLE_SHELL", "false").lower() in {"1", "true", "yes"}
STATE_FILE = Path(os.environ.get("BRIDGE_STATE_FILE", "/app/state/sessions.json"))
MAX_TTL_SECONDS = int(os.environ.get("BRIDGE_MAX_TTL_SECONDS", "3600"))
MAX_CPUS = float(os.environ.get("BRIDGE_MAX_CPUS", "1.0"))
MAX_MEMORY_BYTES = None
MAX_DISK_BYTES = None
MAX_TIMEOUT_SECONDS = int(os.environ.get("BRIDGE_MAX_TIMEOUT_SECONDS", "60"))
MAX_COMMAND_CHARS = int(os.environ.get("BRIDGE_MAX_COMMAND_CHARS", "4000"))
MAX_CODE_CHARS = int(os.environ.get("BRIDGE_MAX_CODE_CHARS", "20000"))
MAX_OUTPUT_CHARS = int(os.environ.get("BRIDGE_MAX_OUTPUT_CHARS", "16000"))
REQUIRE_KNOWN_SHIP = os.environ.get("BRIDGE_REQUIRE_KNOWN_SHIP", "true").lower() not in {"0", "false", "no"}
LIST_OWNED_ONLY = os.environ.get("BRIDGE_LIST_OWNED_ONLY", "true").lower() not in {"0", "false", "no"}

SHIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
INACTIVE_STATUSES = {"deleted", "deleting", "exited", "failed", "stopped", "terminated", "dead", "error"}

mcp = FastMCP("shipyard-bridge")

# 每个沙箱绑定一个稳定会话 ID(Bay 要求 exec 用创建时的会话)
SHIP_SESSIONS: dict[str, str] = {}


def _parse_size(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?|b)?\s*", str(value).lower())
    if not match:
        raise ValueError(f"非法容量格式: {value}")
    number = float(match.group(1))
    unit = (match.group(2) or "b").rstrip("b")
    factors = {
        "": 1,
        "k": 1000,
        "ki": 1024,
        "m": 1000**2,
        "mi": 1024**2,
        "g": 1000**3,
        "gi": 1024**3,
        "t": 1000**4,
        "ti": 1024**4,
    }
    return int(number * factors[unit])


def _format_size(size: int) -> str:
    for suffix, factor in (("G", 1000**3), ("M", 1000**2), ("K", 1000)):
        if size >= factor and size % factor == 0:
            return f"{size // factor}{suffix}"
    return str(size)


MAX_MEMORY_BYTES = _parse_size(os.environ.get("BRIDGE_MAX_MEMORY", "512m"))
MAX_DISK_BYTES = _parse_size(os.environ.get("BRIDGE_MAX_DISK", "5G"))


def _load_sessions() -> None:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception:
        return
    if not isinstance(data, dict):
        return
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return
    for ship_id, session_id in sessions.items():
        sid = str(ship_id).strip()
        sess = str(session_id).strip()
        if _valid_ship_id(sid) and sess:
            SHIP_SESSIONS[sid] = sess


def _save_sessions() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"sessions": SHIP_SESSIONS}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(STATE_FILE)
    except Exception:
        pass


def _valid_ship_id(ship_id: str) -> bool:
    return bool(SHIP_ID_RE.fullmatch(str(ship_id or "").strip()))


def _require_ship_id(ship_id: str) -> str:
    value = str(ship_id or "").strip()
    if not _valid_ship_id(value):
        raise ValueError("ship_id 格式非法")
    if REQUIRE_KNOWN_SHIP and value not in SHIP_SESSIONS:
        raise ValueError("未知沙盒：只允许操作本 bridge 创建并记录的沙盒")
    return value


def _ship_session(ship_id: str) -> str:
    if ship_id not in SHIP_SESSIONS:
        SHIP_SESSIONS[ship_id] = str(uuid.uuid4())
    return SHIP_SESSIONS[ship_id]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n...[已截断 {omitted} 字符]"


def _limit_int(value: int, minimum: int, maximum: int, name: str) -> int:
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    return max(minimum, min(parsed, maximum))


def _limit_float(value: float, minimum: float, maximum: float, name: str) -> float:
    try:
        parsed = float(value)
    except Exception as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    return max(minimum, min(parsed, maximum))


def _limit_size(value: str, maximum: int, name: str) -> str:
    size = _parse_size(value)
    if size <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return _format_size(min(size, maximum))


def _is_active_ship(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    status = str(item.get("status") or item.get("state") or "").strip().lower()
    if status in INACTIVE_STATUSES:
        return False
    ship_id = str(item.get("id") or item.get("ship_id") or "").strip()
    if LIST_OWNED_ONLY and ship_id not in SHIP_SESSIONS:
        return False
    return True


def _filter_active_ships(data: dict | list) -> dict | list:
    if isinstance(data, list):
        return [item for item in data if _is_active_ship(item)]
    if isinstance(data, dict):
        copied = dict(data)
        for key in ("ships", "data", "items"):
            value = copied.get(key)
            if isinstance(value, list):
                copied[key] = [item for item in value if _is_active_ship(item)]
                return copied
    return data


async def _request(method: str, path: str, *, json_body: dict | None = None,
                   timeout: float = 300.0, extra_headers: dict | None = None) -> dict | list:
    headers = _headers()
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, f"{BAY_URL}{path}", json=json_body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Bay API {method} {path} → {resp.status_code}: {resp.text[:300]}")
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Bay API {method} {path} 返回了非 JSON 内容: {resp.text[:300]}") from exc


def _fmt(data: dict) -> str:
    """把 exec 结果整理成可读文本(兼容 ipython/shell 两种返回结构)。"""
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    lines: list[str] = []
    top_err = data.get("error")
    if top_err:
        lines.append(f"error: {top_err}")

    # ipython 风格: output: {text, images}
    out = inner.get("output")
    if isinstance(out, dict):
        text = out.get("text") or ""
        images = out.get("images") or []
        if text:
            lines.append(f"stdout:\n{text}")
        if images:
            lines.append(f"images: {len(images)} 张")
        err = inner.get("error")
        if err:
            lines.append(f"stderr/error:\n{err}")
        ec = inner.get("execution_count")
        if ec is not None:
            lines.append(f"execution_count: {ec}")
    else:
        # shell 风格: stdout/stderr/return_code
        stdout = inner.get("stdout") or ""
        stderr = inner.get("stderr") or ""
        rc = inner.get("return_code", inner.get("exit_code"))
        if stdout:
            lines.append(f"stdout:\n{stdout}")
        if stderr:
            lines.append(f"stderr:\n{stderr}")
        if rc is not None:
            lines.append(f"exit_code: {rc}")
    if lines:
        return _clip("\n".join(lines))
    return _clip(json.dumps(data, ensure_ascii=False))


class TokenAuthMiddleware:
    """Tiny ASGI auth wrapper for FastMCP's HTTP app."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("path") == "/healthz":
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        if REQUIRE_BRIDGE_TOKEN and not BRIDGE_TOKEN:
            await self._reject(send, 503, b"bridge token is required")
            return
        if BRIDGE_TOKEN and not self._authorized(scope):
            await self._reject(send, 401, b"unauthorized")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Any, status: int, body: bytes) -> None:
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _authorized(scope: dict[str, Any]) -> bool:
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        candidates: list[str] = []
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            candidates.append(auth[7:].strip())
        if headers.get("x-bridge-token"):
            candidates.append(headers["x-bridge-token"].strip())
        if ALLOW_QUERY_TOKEN:
            query = parse_qs(scope.get("query_string", b"").decode("latin1"), keep_blank_values=True)
            candidates.extend(query.get("bridge_token", []))
            candidates.extend(query.get("token", []))
        return any(hmac.compare_digest(candidate, BRIDGE_TOKEN) for candidate in candidates)


# ---------------- MCP 工具 ----------------

@mcp.tool()
async def create_sandbox(ttl_seconds: int = 3600, cpus: float = 1.0,
                         memory: str = "512m", disk: str = "5G") -> str:
    """在 Shipyard 中创建一个沙箱(ship)。
    Args:
        ttl_seconds: 沙箱存活秒数(默认 3600)。
        cpus: CPU 上限(默认 1.0)。
        memory: 内存上限,如 "512m" 或 "1g"(默认 512m)。
        disk: 磁盘上限,如 "5G" / "10G" / "2Gi"(默认 5G;需存储驱动支持配额,不支持时被忽略)。
    Returns: 沙箱信息(id/状态/容器ID),调用方需记住 ship_id 用于后续执行。
    """
    ttl = _limit_int(ttl_seconds, 60, MAX_TTL_SECONDS, "ttl_seconds")
    cpu_limit = _limit_float(cpus, 0.1, MAX_CPUS, "cpus")
    memory_limit = _limit_size(str(memory or "512m"), MAX_MEMORY_BYTES, "memory")
    disk_limit = _limit_size(str(disk or "5G"), MAX_DISK_BYTES, "disk")
    payload: dict[str, Any] = {"ttl": ttl, "max_session_num": 1}
    spec: dict[str, Any] = {}
    if cpu_limit:
        spec["cpus"] = cpu_limit
    if memory_limit:
        spec["memory"] = memory_limit
    if disk_limit:
        spec["disk"] = disk_limit
    payload["spec"] = spec
    session_id = str(uuid.uuid4())
    data = await _request("POST", "/ship", json_body=payload, timeout=180,
                          extra_headers={"X-SESSION-ID": session_id})
    if isinstance(data, dict) and data.get("id"):
        SHIP_SESSIONS[data["id"]] = session_id
        _save_sessions()
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(
        {**{k: data.get(k) for k in ("id", "status", "container_id", "ttl")}, "limits": spec},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
async def run_shell(ship_id: str, command: str, timeout: int = 60,
                    cwd: str = "") -> str:
    """在指定沙箱中执行 shell 命令。
    Args:
        ship_id: 沙箱 ID(由 create_sandbox 返回)。
        command: 要执行的命令。
        timeout: 超时秒数。
        cwd: 工作目录(可选)。
    Returns: stdout/stderr/exit_code。
    """
    if not ENABLE_SHELL:
        return "run_shell 已禁用。长期开放给群友时建议只开放 run_python；如确需 Shell，请设置 BRIDGE_ENABLE_SHELL=true。"
    ship_id = _require_ship_id(ship_id)
    command = str(command or "")
    if not command.strip():
        return "command 不能为空。"
    if len(command) > MAX_COMMAND_CHARS:
        return f"command 太长，当前上限 {MAX_COMMAND_CHARS} 字符。"
    timeout = _limit_int(timeout, 1, MAX_TIMEOUT_SECONDS, "timeout")
    cwd = str(cwd or "")
    if "\x00" in cwd or len(cwd) > 512:
        return "cwd 非法。"
    payload = {
        "command": command, "cwd": cwd or None, "env": None,
        "timeout": timeout, "shell": True, "background": False,
    }
    data = await _request(
        "POST", f"/ship/{ship_id}/exec", json_body={"type": "shell/exec", "payload": payload},
        timeout=float(timeout) + 30,
        extra_headers={"X-SESSION-ID": _ship_session(ship_id)},
    )
    return _fmt(data) if isinstance(data, dict) else json.dumps(data, ensure_ascii=False)


@mcp.tool()
async def run_python(ship_id: str, code: str, timeout: int = 60) -> str:
    """在指定沙箱中执行 Python 代码。
    Args:
        ship_id: 沙箱 ID。
        code: Python 代码(如 "print(1+1)")。
        timeout: 超时秒数。
    Returns: stdout/stderr/exit_code。
    """
    ship_id = _require_ship_id(ship_id)
    code = str(code or "")
    if not code.strip():
        return "code 不能为空。"
    if len(code) > MAX_CODE_CHARS:
        return f"code 太长，当前上限 {MAX_CODE_CHARS} 字符。"
    timeout = _limit_int(timeout, 1, MAX_TIMEOUT_SECONDS, "timeout")
    payload = {"code": code, "kernel_id": None, "timeout": timeout, "silent": False}
    data = await _request(
        "POST", f"/ship/{ship_id}/exec", json_body={"type": "ipython/exec", "payload": payload},
        timeout=float(timeout) + 30,
        extra_headers={"X-SESSION-ID": _ship_session(ship_id)},
    )
    return _fmt(data) if isinstance(data, dict) else json.dumps(data, ensure_ascii=False)


@mcp.tool()
async def list_sandboxes() -> str:
    """列出当前仍活跃且可由本 bridge 管理的沙箱。"""
    data = await _request("GET", "/ships", timeout=30)
    data = _filter_active_ships(data)
    if isinstance(data, list):
        return json.dumps(data, ensure_ascii=False, indent=2)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def sandbox_info(ship_id: str) -> str:
    """查看指定沙箱的详情。"""
    ship_id = _require_ship_id(ship_id)
    data = await _request("GET", f"/ship/{ship_id}", timeout=30)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def delete_sandbox(ship_id: str) -> str:
    """删除指定沙箱(不可恢复)。"""
    ship_id = _require_ship_id(ship_id)
    data = await _request("DELETE", f"/ship/{ship_id}", timeout=60)
    SHIP_SESSIONS.pop(ship_id, None)
    _save_sessions()
    return json.dumps(data, ensure_ascii=False, indent=2) if data else f"已删除沙箱 {ship_id}"


if __name__ == "__main__":
    _load_sessions()
    if REQUIRE_BRIDGE_TOKEN and not BRIDGE_TOKEN:
        raise SystemExit("BRIDGE_TOKEN 未设置；长期开放时必须配置 bridge 访问令牌。")
    if REQUIRE_SHIPYARD_TOKEN and not TOKEN:
        raise SystemExit("SHIPYARD_TOKEN 未设置；请配置 Bay ACCESS_TOKEN。")
    app = TokenAuthMiddleware(mcp.http_app(path=MCP_PATH))
    print(
        f"shipyard-mcp-bridge 启动: {BAY_URL} → {HOST}:{PORT}{MCP_PATH} "
        f"auth={'on' if BRIDGE_TOKEN else 'off'} shell={'on' if ENABLE_SHELL else 'off'}",
        flush=True,
    )
    uvicorn.run(app, host=HOST, port=PORT)
