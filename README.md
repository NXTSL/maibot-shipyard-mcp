# maibot-shipyard-mcp

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Docker](https://img.shields.io/badge/docker-ready-2496ed)
![MCP](https://img.shields.io/badge/MCP-streamable--http-orange)
![Stars](https://img.shields.io/github/stars/NXTSL/maibot-shipyard-mcp)

**给麦麦(MaiBot)接上 AstrBot Shipyard 沙盒的 MCP 桥。** 让麦麦在群里聊着天,随手就能在隔离沙盒里跑 Python / Shell。项目默认按长期开放给群友的场景加了访问令牌、资源上限和输出截断。

## 🎯 它能做什么

- 🐍 沙盒里跑 Python,必要时可显式打开 Shell
- 🔒 Bridge 访问令牌 + 非 root 运行 + 资源上限 + `/tmp` 重定向隔离
- 🔁 沙盒按 TTL 滑动续期,闲置自动回收
- 🧯 默认只列出本 bridge 创建并仍活跃的沙盒,避免旧会话炸成 `Session terminated`
- 🔌 标准 MCP 协议,以后换机器人也不用换桥

## 🧱 架构

```
麦麦(MCP 客户端) ⇄ 本桥(streamable-http :8124) ⇄ Bay(REST :8156) ⇄ Ship 容器(Docker)
```

桥只做一件事:把 Bay 的 REST API 包成 MCP 工具。约 150 行,单文件。

## ✅ 前置条件

- Docker + Docker Compose
- 已部署的 AstrBot + Shipyard(Bay 在 `shipyard:8156`)
- 已部署的 MaiBot(v4.12.0+,支持 MCP)

> 国内网络:GitHub 拉取加 `https://gh-proxy.com/` 前缀;Docker 镜像换 `docker.1ms.run` 等源;pip 用清华镜像 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

## 🚀 快速开始

**1. 构建桥镜像**

```bash
cd bridge
docker build -t shipyard-mcp-bridge .
```

**2. 构建加固沙盒镜像**(强烈推荐,磁盘隔离靠它)

```bash
cd hardened-ship
docker build -t shipyard-ship-hardened .
```

然后改 AstrBot 的 `compose-with-shipyard.yml`,把 shipyard 服务的 `DOCKER_IMAGE` 换成 `shipyard-ship-hardened:latest`。

**3. 起桥容器(双网络)**

先生成 bridge 令牌:

```bash
openssl rand -hex 32
```

```bash
docker run -d --name shipyard-mcp-bridge \
  --network astrbot_network \
  -e SHIPYARD_URL=http://shipyard:8156 \
  -e "SHIPYARD_TOKEN=<你的 Bay ACCESS_TOKEN>" \
  -e "BRIDGE_TOKEN=<上一步生成的随机长令牌>" \
  -e REQUIRE_BRIDGE_TOKEN=true \
  -e BRIDGE_ENABLE_SHELL=false \
  -v shipyard_mcp_state:/app/state \
  -p 127.0.0.1:8124:8124 \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:size=16m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  shipyard-mcp-bridge

docker network connect maibot_maim_bot shipyard-mcp-bridge
```

> 网络名以你实际部署为准(`docker network ls` 查看)。compose 写的项目,网络名通常带项目前缀。

**4. 麦麦接入 MCP**

`bot_config.toml` 的 `[mcp]` 段加一条:

```toml
[mcp]
enable = true
servers = [
    { name = "shipyard", enabled = true, transport = "streamable_http", url = "http://shipyard-mcp-bridge:8124/mcp?bridge_token=<你的 BRIDGE_TOKEN>" },
]
```

保存即热重载,日志出现 `MCP 服务器 'shipyard' 已连接` 就通了。

## 🧰 工具列表

| 工具 | 作用 |
|------|------|
| `create_sandbox` | 建沙盒,默认 TTL 3600s、磁盘 5G |
| `run_shell` | 跑 Shell |
| `run_python` | 跑 Python |
| `list_sandboxes` | 列出沙盒 |
| `sandbox_info` | 看详情 |
| `delete_sandbox` | 删沙盒 |

`run_shell` 默认关闭。长期给群友开放时建议只保留 Python 执行；如确需 Shell,设置 `BRIDGE_ENABLE_SHELL=true`。

## 🔐 长期开放建议

默认限制:

| 环境变量 | 默认值 | 作用 |
|------|------|------|
| `REQUIRE_BRIDGE_TOKEN` | `true` | 没有 `BRIDGE_TOKEN` 时拒绝启动 |
| `BRIDGE_ALLOW_QUERY_TOKEN` | `true` | 允许在 MCP URL 上使用 `?bridge_token=...` |
| `BRIDGE_ENABLE_SHELL` | `false` | 默认禁用 Shell 工具 |
| `BRIDGE_MAX_TTL_SECONDS` | `3600` | 单个沙盒最大 TTL |
| `BRIDGE_MAX_CPUS` | `1.0` | 单个沙盒最大 CPU |
| `BRIDGE_MAX_MEMORY` | `512m` | 单个沙盒最大内存 |
| `BRIDGE_MAX_DISK` | `5G` | 单个沙盒最大磁盘参数 |
| `BRIDGE_MAX_TIMEOUT_SECONDS` | `60` | 单次执行最大超时 |
| `BRIDGE_MAX_OUTPUT_CHARS` | `16000` | 返回给麦麦的最大文本长度 |
| `BRIDGE_REQUIRE_KNOWN_SHIP` | `true` | 只允许操作本 bridge 创建并记录的沙盒 |
| `BRIDGE_LIST_OWNED_ONLY` | `true` | `list_sandboxes` 只列本 bridge 管理的活跃沙盒 |

注意:

- 不要把 bridge 端口暴露到公网。
- 不要把 `BRIDGE_TOKEN` 写进群消息或公开仓库。
- 给群友开放时,建议在 MaiBot 侧再做白名单/权限控制；bridge 只知道 MCP 请求,不知道真实 QQ 调用者。
- 沙盒隔离依赖 Docker、Shipyard 和宿主机配置共同生效；不要在沙盒内放任何真实凭据。

## 📁 目录结构

```
maibot-shipyard-mcp/
├── bridge/            # MCP 桥(核心,bridge.py 单文件)
├── hardened-ship/     # 加固沙盒镜像(/tmp 重定向 + 启动包装)
├── deploy/            # 双网络部署示例
├── README.md
└── LICENSE
```

## ❓ FAQ

**Q: 麦麦报 "Session terminated",桥日志全是 404?**
桥容器重启过或旧沙盒不是本 bridge 创建的。新版 bridge 会把 `ship_id -> session_id` 持久化到 `/app/state/sessions.json`,并让 `list_sandboxes` 只显示自己能继续管理的活跃沙盒。

**Q: 沙盒能提权到 root 吗?**
加固镜像会尽量按 Shipyard 的非 root 沙盒模型运行,但安全性最终取决于上游 ship 镜像、Docker 参数和宿主机配置。不要把它当成强安全边界。

**Q: 沙盒写大文件会不会塞爆系统盘?**
本项目会把 `/tmp`、`/var/tmp` 指向 `/home/tmp`,并给 Shipyard 传递磁盘上限参数。实际是否硬限制成功,取决于 Shipyard 和 Docker 存储驱动/挂载方式。

**Q: 沙盒多久超时?**
默认 TTL 3600s,滑动续期——每次被调用自动延长,等于"最多空闲 1 小时"。

## ⚠️ 风险警示

- **禁止把 MCP 端口(8124)暴露到公网** —— 那等于把沙盒控制权交给陌生人。
- **不要让不可信用户随意触发沙盒执行** —— 代码执行始终有风险,请配合麦麦的聊天白名单使用。
- 沙盒是隔离的,但不是魔法:永远别在沙盒里放敏感凭据。
- Shell 工具默认关闭;长期开放时先用 Python 工具观察一段时间,确认权限策略稳定后再考虑开启。

## 🤝 贡献

有想法就提 Issue,会写代码就提 PR。改动请保持单文件极简风格,并说明安全影响。

## 📄 许可证

[MIT](./LICENSE)

## 🙏 致谢

感谢 AstrBot Shipyard、麦麦社区以及 MCP 标准，让本项目得以实现。

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Shipyard Neo](https://github.com/AstrBotDevs/shipyard-neo)
- [MaiBot（麦麦）](https://github.com/Mai-with-u/MaiBot)


