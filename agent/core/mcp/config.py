"""MCP 连接配置加载与工作目录归一化。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


_AGENT_DIR = Path(__file__).resolve().parents[2]
_CONFIG_FILE = _AGENT_DIR / "config" / "mcp_servers.json"


def load_mcp_connections() -> dict[str, dict[str, Any]]:
    """加载 MCP 连接，并把相对 cwd 统一解析到 agent 目录。

    这样无论从项目根目录、app 目录还是 IDE 启动，stdio 子进程都能找到
    ``mcp_servers`` 包，不依赖调用者当前工作目录。
    """
    with _CONFIG_FILE.open("r", encoding="utf-8") as file:
        connections = json.load(file).get("mcpServers", {})

    normalized: dict[str, dict[str, Any]] = {}
    for server_name, connection in connections.items():
        item = dict(connection)
        # MCP 子进程必须与主进程使用同一虚拟环境，否则新增数据库/向量库依赖
        # 可能在系统 Python 中不可见。
        if item.get("command") in {"python", "python3"}:
            item["command"] = sys.executable
        cwd = item.get("cwd")
        if cwd and not Path(cwd).is_absolute():
            item["cwd"] = str((_AGENT_DIR / cwd).resolve())
        normalized[server_name] = item
    return normalized
