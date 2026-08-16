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
import uuid
from typing import Any

import httpx
from fastmcp import FastMCP

BAY_URL = os.environ.get("SHIPYARD_URL", "http://shipyard:8156").rstrip("/")
TOKEN = os.environ.get("SHIPYARD_TOKEN", "")
PORT = int(os.environ.get("BRIDGE_PORT", "8124"))

mcp = FastMCP("shipyard-bridge")

# 每个沙箱绑定一个稳定会话 ID(Bay 要求 exec 用创建时的会话)
SHIP_SESSIONS: dict[str, str] = {}


def _ship_session(ship_id: str) -> str:
    if ship_id not in SHIP_SESSIONS:
        SHIP_SESSIONS[ship_id] = str(uuid.uuid4())
    return SHIP_SESSIONS[ship_id]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


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
        return resp.json()


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
        return "\n".join(lines)
    return json.dumps(data, ensure_ascii=False)


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
    payload: dict[str, Any] = {"ttl": int(ttl_seconds), "max_session_num": 1}
    spec: dict[str, Any] = {}
    if cpus:
        spec["cpus"] = float(cpus)
    if memory:
        spec["memory"] = str(memory)
    if disk:
        spec["disk"] = str(disk)
    payload["spec"] = spec
    session_id = str(uuid.uuid4())
    data = await _request("POST", "/ship", json_body=payload, timeout=180,
                          extra_headers={"X-SESSION-ID": session_id})
    if isinstance(data, dict) and data.get("id"):
        SHIP_SESSIONS[data["id"]] = session_id
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(
        {k: data.get(k) for k in ("id", "status", "container_id", "ttl")},
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
    payload = {
        "command": command, "cwd": cwd or None, "env": None,
        "timeout": int(timeout), "shell": True, "background": False,
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
    payload = {"code": code, "kernel_id": None, "timeout": int(timeout), "silent": False}
    data = await _request(
        "POST", f"/ship/{ship_id}/exec", json_body={"type": "ipython/exec", "payload": payload},
        timeout=float(timeout) + 30,
        extra_headers={"X-SESSION-ID": _ship_session(ship_id)},
    )
    return _fmt(data) if isinstance(data, dict) else json.dumps(data, ensure_ascii=False)


@mcp.tool()
async def list_sandboxes() -> str:
    """列出当前所有沙箱及其状态。"""
    data = await _request("GET", "/ships", timeout=30)
    if isinstance(data, list):
        return json.dumps(data, ensure_ascii=False, indent=2)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def sandbox_info(ship_id: str) -> str:
    """查看指定沙箱的详情。"""
    data = await _request("GET", f"/ship/{ship_id}", timeout=30)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def delete_sandbox(ship_id: str) -> str:
    """删除指定沙箱(不可恢复)。"""
    data = await _request("DELETE", f"/ship/{ship_id}", timeout=60)
    return json.dumps(data, ensure_ascii=False, indent=2) if data else f"已删除沙箱 {ship_id}"


if __name__ == "__main__":
    print(f"shipyard-mcp-bridge 启动: {BAY_URL} → 0.0.0.0:{PORT}/mcp", flush=True)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=PORT)
