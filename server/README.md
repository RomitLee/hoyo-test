# 梦幻西游背包告警中转服务

这是从机与主机之间的云端消息中转服务。所有连接均由从机或主机主动发起，家庭和公司路由器不需要开放端口。

## API

- `GET /healthz`：服务健康检查，不需要身份验证。
- `POST /api/v1/slave/heartbeat`：从机心跳。
- `POST /api/v1/slave/events`：从机提交告警，`device_id + event_id` 重复提交不会产生重复事件。
- `POST /api/v1/slave/status`：从机每 5 秒提交当前所有游戏窗口的状态快照；同一从机的快照会覆盖旧窗口列表。
- `GET /api/v1/master/events`：主机领取事件，默认只返回尚未确认的事件。
- `POST /api/v1/master/events/{server_event_id}/ack`：主机确认已处理事件。
- `GET /api/v1/master/devices`：主机读取从机在线信息。
- `GET /api/v1/master/status`：主机读取所有从机及其全部游戏窗口的最新状态。

从机请求必须同时携带：

```text
X-Device-ID: slave-01
Authorization: Bearer <该从机独立密钥>
```

主机请求携带：

```text
Authorization: Bearer <主机密钥>
```

## 运行测试

```bash
cd server
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/pytest -q
```

## Docker 部署

复制 `.env.example` 为 `.env`，生成主机密钥和每台从机的独立密钥，然后运行：

```bash
docker compose up -d --build
curl http://127.0.0.1/healthz
```

当前部署支持无域名 HTTPS：Certbot 为公网 IP 申请短期证书，Caddy 使用该证书提供 HTTPS，systemd 定时器每天检查续期。设备密钥只允许经 HTTPS 传输。

SQLite 数据保存在 Docker 命名卷 `relay_data`。已确认告警默认保留 30 天，未确认告警不会由定期清理逻辑删除。

在全新的 Ubuntu 24.04 服务器上，也可以使用仓库提供的一键部署脚本。脚本会安装 Docker、生成一个主机密钥和 10 个从机独立密钥、构建服务并执行健康检查：

```bash
chmod 700 deploy.sh
./deploy.sh <服务器公网 IP>
```

脚本不会在重复执行时重新生成密钥。密钥保存在服务器的 `.env` 和 `deployment-credentials.json` 中，两个文件权限均为仅 root 可读，且不会被 Git 或 Docker 构建上下文收录。
