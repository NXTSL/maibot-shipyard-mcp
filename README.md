# maibot-shipyard-mcp

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Docker](https://img.shields.io/badge/docker-ready-2496ed)
![MCP](https://img.shields.io/badge/MCP-streamable--http-orange)
![Stars](https://img.shields.io/github/stars/NXTSL/maibot-shipyard-mcp)

**给麦麦(MaiBot)接上 AstrBot Shipyard 沙盒的 MCP 桥。** 让麦麦在群里聊着天,随手就能在隔离沙盒里跑 Python / Shell,安全、干净、用完即弃。

## 🎯 它能做什么

- 🐍 沙盒里跑 Python / Shell,结果直接回到麦麦
- 🔒 非 root 运行 + 5G 磁盘封顶 + `/tmp` 重定向隔离,碰不到宿主机
- 🔁 沙盒按 TTL 滑动续期,闲置自动回收
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

```bash
docker run -d --name shipyard-mcp-bridge \
  --network astrbot_network \
  -e SHIPYARD_URL=http://shipyard:8156 \
  -e "SHIPYARD_TOKEN=**<你的 Bay ACCESS_TOKEN>**" \
  -p 127.0.0.1:8124:8124 \
  --restart unless-stopped \
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
    { name = "shipyard", enabled = true, transport = "streamable_http", url = "http://shipyard-mcp-bridge:8124/mcp" },
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
桥容器重启过,麦麦还拿着旧会话 ID。重启桥(`docker restart shipyard-mcp-bridge`)或等麦麦自动重连。

**Q: 沙盒能提权到 root 吗?**
不能。uid=10000 非 root,`sudo`/`su` 都要密码,进程 CapEff=0,容器里也没有 docker socket。

**Q: 沙盒写大文件会不会塞爆系统盘?**
不会。`$HOME`、`/tmp`、`/var/tmp` 全在宿主机 5G loopback 盘上,写满即拒;`/dev/shm` 只有 64M。

**Q: 沙盒多久超时?**
默认 TTL 3600s,滑动续期——每次被调用自动延长,等于"最多空闲 1 小时"。

## ⚠️ 风险警示

- **禁止把 MCP 端口(8124)暴露到公网** —— 那等于把沙盒控制权交给陌生人。
- **不要让不可信用户随意触发沙盒执行** —— 代码执行始终有风险,请配合麦麦的聊天白名单使用。
- 沙盒是隔离的,但不是魔法:永远别在沙盒里放敏感凭据。

## 🤝 贡献

有想法就提 Issue,会写代码就提 PR。改动请保持单文件极简风格,并说明安全影响。

## 📄 许可证

[MIT](./LICENSE)

## 🙏 致谢

感谢 AstrBot 的 Shipyard、麦麦社区和 MCP 标准,让这一切能拼在一起。
