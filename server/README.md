# 梦幻西游背包告警中转服务

当前云端版本 **2.32.0**，配套从机 V2.17.0；历史 Windows 主机包保持 V2.3.0，通信协议 1。当前发布与下载见 [项目说明](../README.md)。

用户工作台“偏好设置”已拆分为四个标签页：告警规则、通知方式、监控与截图、区服比例。告警阈值和通知渠道不再混在同一长表单中，并针对桌面与手机布局分别适配。

用户工作台增加「宝石合成」和「单服日利润」两个板块。设置页中的「区服比例」可新增、编辑、删除区服及其每 3000 万游戏币对应的人民币报价；宝石合成页可按宝石类型逐级编辑售卖比例。配置保存在当前账号的 `gem_calculator_settings` 中，跨设备同步；保存时按修订号检查，遇到其他页面先行保存会返回 409，防止覆盖。利润板块是按输入参数测算，不使用客户端游戏币观测结果。

首页 `/` 是 Vue 网页管理界面，源码位于 `../web-admin/`，构建文件位于 `app/web-admin/` 并随服务端镜像提供。旧 H5 保留 `/legacy` 与 `/static/`。网页与桌面主机共用现有账号、设备和 API；静态资源不含凭据。修改前端后需在 `web-admin/` 执行 `npm ci`、`npm run build`，再构建服务端镜像。

`/admin` 为只读项目总后台，使用与用户工作台相同的账号密码登录，但必须由服务端运维人员单独授予 `is_admin`。在服务容器中执行 `python -m app.admin_cli grant 用户名`，撤销执行 `revoke`；未提供任何公开的授权接口。`/api/v1/admin/me`、`overview`、`accounts`、`roles`、`devices` 只接受有效的管理员会话，旧主机令牌不能访问。所有列表支持搜索与分页，角色和设备列表可按账号筛选。普通用户接口继续按账号隔离。

后续 Windows 从机包发布到私有 COS；现有 `/api/v1/updates/download/slave/Vx.y.z` 路径继续有效，对 COS 中存在的包返回临时 HTTPS 重定向。截图密钥与安装包密钥分离，安装包策略仅覆盖 `releases/*`。发布命令及旧版本兼容方式见 [客户端安装包 COS 存储](../docs/48-client-packages-cos.md)。

这是从机与网页后台之间的多账号云端服务。所有连接均由从机或浏览器主动发起，家庭和公司路由器不需要开放端口。

## 身份与数据隔离

- 网页后台使用账号和密码注册、登录；密码只保存 PBKDF2 摘要。
- 登录成功后签发 30 天会话凭据。
- 每个账号只能读取、确认和管理自己名下的从机与告警。
- 新从机首次使用账号密码登录，自动取得设备编号和独立设备凭据；密码不保存在从机。
- 从机每次启动先验证已保存的设备凭据，验证失败时监控保持锁定。账号与安装标识相同的设备保留原名称。
- 禁用设备后无法验证或用相同安装标识再次登录；移除设备会撤销旧凭据，后续显式登录可作为新设备登记。
- 配对码接口保留给历史客户端兼容，新网页和新版从机不再使用。
- 旧版 `HOYO_MASTER_TOKEN` 和 `HOYO_DEVICE_TOKENS` 只作为升级兼容入口，可以在全部设备迁移后清除。

## API

- `POST /api/v1/slave/monitor-logs`：上报开始/暂停操作（`startup`、`resume`、`pause`、`close`），
  使用独立事件 ID 幂等保存，不触发告警推送。
- `GET /api/v1/master/monitor-logs?limit=100&before_id=0`：按当前账号查询监控日志，
  ID 倒序分页，响应包含 `logs`、`has_more`。更早一页传入上一页最后一条 ID。

最小化状态及符合账号遮挡规则的窗口在保存最新状态时写入监控日志。遮挡规则包含是否上报、比例与持续时间，默认开启、60%、5 秒；原始遮挡状态仍可正常上报。相同快照重传幂等，不同时间的异常快照分别保存。日志独立保存，设备移除或
状态快照被替换后历史仍保留；记录按账号隔离。现有数据库在服务启动时自动创建新表和索引。

- `GET /healthz`：健康检查。
- `POST /api/v1/auth/register`：注册主机账号。
- `POST /api/v1/auth/login`：登录主机账号。
- `POST /api/v1/slave/login`：从机使用账号密码首次登录，按同账号机器摘要找回原设备或自动登记，并取得设备凭据。
- `GET /api/v1/slave/auth-state`：兼容旧从机，验证已保存的设备凭据并读取账号及设备名称。
- `POST /api/v1/slave/auth-state`：新版从机验证凭据并提交本机摘要；可将重装后的重复记录归并到唯一的已改名旧设备。
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
- `GET/PUT /api/v1/master/live-screenshot-settings`：读取或设置账号自动截图周期（1～60 分钟）及事件截图面板开关；`event_screenshots` 包含 `give`、`trade`、`attributes`、`stall`、`stall_buy`、`stall_other_buy`，旧客户端省略该字段时保留原开关。
- `GET /api/v1/slave/live-screenshots/control`：从机获取周期与待执行的即时截图请求。
- `POST /api/v1/slave/live-screenshots/permission`：取得仅针对单张截图对象的 COS 上传链接；新版从机每张截图携带独立 `capture_id`，旧版从机仍可使用原固定对象链接。
- `POST /api/v1/slave/live-screenshots/complete`：直传后登记截图时间和窗口关联，服务端检查对象存在。
- `POST /api/v1/master/live-screenshots/{device_id}/request`：请求指定窗口的下一帧新截图。
- `GET /api/v1/master/live-screenshots/requests/{request_id}`：查询即时截图完成状态。
- `GET /api/v1/master/live-screenshots/{device_id}/view?window_id=...`：取得最近截图的短期读取链接。
- `GET /api/v1/updates/latest/{role}`：读取对应角色的 schema3 RSA 签名发布清单。
- `GET /api/v1/updates/latest`：旧客户端 V2.1.0 过渡清单，不能用新角色清单覆盖。
- `GET /api/v1/master/monitor-log-settings`、`PUT /api/v1/master/monitor-log-settings`：读取或修改账号遮挡日志规则。
- `PUT /api/v1/slave/monitor-logs/{event_id}/screenshot`：设备认证上传给予、交易日志 JPEG。
- `GET /api/v1/master/monitor-logs/{log_id}/screenshot`：账号认证取得 5 分钟有效的图片链接。
- `GET /api/v1/updates/download/{role}/{version}`：下载清单中当前版本对应的主机或从机 ZIP。

已登录的主机请求携带：

```text
Authorization: Bearer <账号登录后签发的会话凭据>
```

已登录的从机请求携带：

```text
X-Device-ID: <服务端自动分配的设备编号>
Authorization: Bearer <从机登录后签发的设备凭据>
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

桌面自动更新文件从宿主机 `releases/` 只读挂载到容器 `/releases`。发布需保存两个角色的签名清单、相应版本 ZIP、旧 `legacy-release-manifest.json` 和历史包目录。先部署服务端并检查健康，再上传安装包，最后原子切换被更新角色的清单；未改动端复用原包。旧共享过渡清单必须保持可用。

发布目录绝不能包含签名私钥或客户端运行配置。客户端验证 RSA-3072 签名、角色、版本、大小及 SHA-256；不匹配的包不会安装。当前发布流程及验证见 [发布记录](../docs/30-screenshot-production-release.md)。

## 私有 COS 截图配置

仅服务端配置以下环境变量；不要将真实值写入本文件或发送给客户端：

```dotenv
COS_SECRET_ID=<截图专用账号的 SecretId>
COS_SECRET_KEY=<截图专用账号的 SecretKey>
COS_BUCKET=<桶名称>
COS_REGION=<地域>
COS_SCREENSHOT_DOMAIN=https://<已配置 HTTPS 的截图域名>
```

可将配置直接加入现有 `.env`；若分开保存在 `.env.cos`，需在 Compose 的 relay 服务 `env_file` 中追加该文件后重建容器。仓库默认 Compose 只加载 `.env`，不会自动读取 `.env.cos`。线上已单独配置加载，密钥文件保持 root 所有、600 权限。

桶保持私有，专用账号仅授予 `screenshots/` 前缀的 PutObject、GetObject 和 HeadObject 权限。该前缀设置 30 天生命周期；自定义域名需配置 CNAME、HTTPS 证书和续期维护。单张图片最大 2MB，服务端验证 JPEG 内容、尺寸和日志归属。每条日志唯一关联截图，上传租约处理并发及失败恢复。

主机需登录才能取得 5 分钟 HTTPS 签名链接，响应禁止缓存；不要将签名链接写入公开日志。截图保留 30 天，过期后文字日志继续保留。缺少存储配置时返回可重试错误，原有文字日志不受影响。详情见 [截图说明](../docs/29-panel-screenshots.md)。

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

页面使用账号密码登录，只在浏览器保存登录会话，不保存密码。页面每 5 秒刷新，包含监控、告警记录、从机管理、监控日志及手机通知功能。监控日志支持类型筛选和截图查看。告警可在手机端确认；新机器在从机登录后自动出现在管理列表，可编辑名称和备注、启用/禁用或移除。手机布局采用紧凑状态卡，便于单屏查看更多角色。

“手机通知”页面可绑定 iPhone Bark 设备密钥。新告警由云服务器直接推送，不依赖 Windows 主机在线；支持测试通知、约 30 秒连续呼叫，以及未确认告警按 5/10/30/60 分钟重复提醒。手机或主机确认告警后停止重复推送。Bark 密钥只存储在服务器数据库中，接口仅返回掩码。

项目根目录的 `portal/` 是 `www.maipi.top` 的“麦皮网”工具入口，服务器挂载到 `/opt/portal`；`portal/gem/` 是 `gem.maipi.top` 的站点目录。

部署代码变更后重新构建并检查：

```bash
cd server
docker compose up -d --build
curl https://<服务器地址>/healthz
```
