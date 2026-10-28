# 客户端安装包 COS 存储

从机仍从服务端获取 RSA 签名的版本清单，清单里的下载路径继续使用 `jiankong.maipi.top/api/v1/updates/download/slave/Vx.y.z`。服务端为 COS 中存在的包返回临时 HTTPS 地址，安装包流量由客户端直接从 COS 下载。现有从机的同源路径校验、清单签名、ZIP 大小和 SHA-256 校验保持有效。已发布旧包留在服务器，COS 无对应对象时沿用本地下载。

安装包使用现有私有桶 `mh-1257029073` 的 `releases/slave/Vx.y.z/<sha256>/<filename>.zip` 前缀。当前 30 天自动删除规则只匹配 `screenshots/`，不会删除 `releases/`。服务器为每次下载签发有效期 1 小时的 COS GET 地址；COS 下载密钥不会交给从机。安装包密钥应为独立 CAM 编程用户，仅授予 [专用策略](cos-release-policy.json) 中的 PutObject、GetObject、HeadObject，不授予截图、删改权限或公开读取。

服务器 `.env` 增加以下四项，密钥仅保存在服务器：

```dotenv
COS_RELEASE_SECRET_ID=...
COS_RELEASE_SECRET_KEY=...
COS_RELEASE_BUCKET=mh-1257029073
COS_RELEASE_REGION=ap-hongkong
```

新版本在 Windows 本地用 `scripts/build_windows.ps1` 构建并验证签名，将 `release-manifest-slave.json` 和对应 ZIP 上传到服务器的 `/home/ubuntu/`，再用 `sudo install -o 10001 -g 10001 -m 0640` 放入 `/opt/hoyo-relay/releases/staging/`。服务器执行：

首次部署时，将服务器的 `/opt/hoyo-relay/releases` 目录交给容器内 UID 10001 的 `relay` 用户写入；发布脚本需要在该目录更新清单和历史记录。

```bash
docker compose exec -T relay python -m app.release_cli /releases/staging/release-manifest-slave.json /releases/staging/从机安装包.zip
```

`app.release_cli` 会验证 RSA 签名、版本、下载路径、文件名、大小和 SHA-256；先上传并 HEAD 检查 COS 对象，再原子更新从机清单。执行成功后由发布者清理 `staging/` 中的临时文件。发布完成后检查 `/api/v1/updates/latest/slave` 和下载接口的 302 `Location`，确认链接为 COS 域名，并通过客户端下载及校验一次。已发布历史清单保存在 `history/slave/版本号/`，旧的服务器安装包可继续下载。
