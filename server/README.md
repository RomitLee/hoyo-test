# 梦幻西游背包告警中转服务

这是从机与主机之间的多账号云端中转服务。所有连接均由从机或主机主动发起，家庭和公司路由器不需要开放端口。

## 身份与数据隔离

- 主机使用账号和密码注册、登录；密码只保存 PBKDF2 摘要。
- 登录成功后签发 30 天会话凭据。
- 每个账号只能读取、确认和管理自己名下的从机与告警。
- 主机生成 6 位一次性配对码，5 分钟过期且只能使用一次。
- 从机用配对码换取自动分配的设备编号和独立设备凭据。
- 禁用、移除或重新配对设备后，旧设备凭据不能继续上报。
- 旧版 `HOYO_MASTER_TOKEN` 和 `HOYO_DEVICE_TOKENS` 只作为升级兼容入口，可以在全部设备迁移后清除。

## API

- `POST /api/v1/slave/monitor-logs`：上报开始/暂停操作（`startup`、`resume`、`pause`、`close`），
  使用独立事件 ID 幂等保存，不触发告警推送。
- `GET /api/v1/master/monitor-logs?limit=100&before_id=0`：按当前账号查询监控日志，
  ID 倒序分页，响应包含 `logs`、`has_more`。更早一页传入上一页最后一条 ID。

从机状态快照中的每个最小化窗口、每个遮挡比例大于 0 的窗口，均在保存最新状态的同一事务中
写入监控日志。相同快照重传幂等，不同时间的异常快照分别保存。日志独立保存，设备移除或
状态快照被替换后历史仍保留；记录按账号隔离。现有数据库在服务启动时自动创建新表和索引。

- `GET /healthz`：健康检查。
- `POST /api/v1/auth/register`：注册主机账号。
- `POST /api/v1/auth/login`：登录主机账号。
- `GET /api/v1/master/account`：读取当前账号。
- `POST /api/v1/master/pairing-codes`：生成新从机配对码。
- `POST /api/v1/slave/pair`：从机使用配对码换取设备身份。
- `POST /api/v1/slave/heartbeat`：从机心跳。
- `POST /api/v1/slave/events`：提交告警；`device_id + event_id` 保证幂等。
- `POST /api/v1/slave/status`：提交当前最多 8 个游戏窗口的状态快照。
- `GET /api/v1/master/events`：读取当前账号的告警记录。
- `POST /api/v1/master/events/{id}/ack`：确认告警。
- `GET /api/v1/master/devices`：读取当前账号的从机列表。
- `PATCH /api/v1/master/devices/{device_id}`：修改名称、备注或禁用状态。
- `DELETE /api/v1/master/devices/{device_id}`：移除从机并撤销凭据。
- `POST /api/v1/master/devices/{device_id}/reissue`：撤销旧凭据并生成重新配对码。
- `GET /api/v1/master/status`：读取当前账号的从机和全部游戏窗口状态。
- `GET /api/v1/updates/latest`：读取当前 RSA 签名的桌面客户端发布清单。
- `GET /api/v1/updates/download/{role}/{version}`：下载清单中当前版本对应的主机或从机 ZIP。

已登录的主机请求携带：

```text
Authorization: Bearer <账号登录后签发的会话凭据>
```

已配对的从机请求携带：

```text
X-Device-ID: <服务端自动分配的设备编号>
Authorization: Bearer <配对成功后签发的设备凭据>
```

## 运行测试

```bash
cd server
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/pytest -q
```

## Docker 部署

```bash
docker compose up -d --build
curl http://127.0.0.1/healthz
```

无域名 HTTPS 由 Certbot 为公网 IP 申请短期证书，Caddy 使用该证书提供 HTTPS，systemd 定时器每天检查续期。SQLite 数据保存在 Docker 命名卷 `relay_data`；已确认告警默认保留 30 天，未确认告警不会自动删除。

桌面自动更新文件从宿主机 `server/releases/` 只读挂载到容器 `/releases`。发布新版本前，在项目根目录完成签名构建，然后把 `dist/release-manifest.json` 和两个 ZIP 复制到该目录：

```bash
mkdir -p server/releases
cp dist/release-manifest.json dist/*.zip server/releases/
cd server
docker compose up -d --build
curl --fail https://jiankong.maipi.top/api/v1/updates/latest
```

发布目录不需要、也绝不能包含 `runtime/update-signing-private.json`。客户端会验证清单的 RSA-3072 签名及安装包 SHA-256，未签名、签名错误或文件不匹配的版本不会被安装。

全新的 Ubuntu 24.04 服务器也可以使用一键部署脚本：

```bash
chmod 700 deploy.sh
./deploy.sh <服务器公网 IP>
```

历史版本部署脚本可能生成迁移用主机密钥和从机密钥。这些内容仅保存在服务器 `.env` 与 `deployment-credentials.json`，不要提交到 Git；升级后的通用客户端不需要用户输入它们。

## 手机 H5 状态页面

服务端首页提供适配手机浏览器的状态页：

```text
https://<服务器地址>/
```

页面使用与 Windows 主机程序相同的账号密码登录，只在浏览器保存登录会话，不保存密码。页面每 5 秒刷新，包含实时状态、告警记录、从机管理和手机通知四个页面。告警可在手机端确认；从机管理支持生成一次性配对码、编辑名称和备注、启用/禁用、重新配对与移除设备。手机布局采用紧凑状态卡，便于单屏查看更多角色。

“手机通知”页面可绑定 iPhone Bark 设备密钥。新告警由云服务器直接推送，不依赖 Windows 主机在线；支持测试通知、约 30 秒连续呼叫，以及未确认告警按 5/10/30/60 分钟重复提醒。手机或主机确认告警后停止重复推送。Bark 密钥只存储在服务器数据库中，接口仅返回掩码。

项目根目录的 `portal/` 是 `www.maipi.top` 的“麦皮网”工具入口，服务器挂载到 `/opt/portal`；`portal/gem/` 是 `gem.maipi.top` 的站点目录。

部署代码变更后重新构建并检查：

```bash
cd server
docker compose up -d --build
curl https://<服务器地址>/healthz
```
