# 梦幻西游 Web 管理界面

沿用现有 FastAPI、账号会话与 `/api/v1` 接口，服务端仍使用 SQLite。界面参考并复用 [scaffold-master](https://github.com/wbr664393425-lgtm/scaffold-master) 的 Vue 3 前端结构、浅色主题令牌与 Panel 组件；其代码遵循随附 MIT 许可。没有引入该项目的 Spring Boot、MySQL、Redis、JWT 或 GSAP。

开发：在本目录运行 `npm ci`，再运行 `npm run dev`。本地 API 默认代理到 `http://127.0.0.1:8000`，可设置 `VITE_API_PROXY_TARGET`。

构建：运行 `npm run build`，产物输出到 `server/app/web-admin/`，随服务端镜像部署。服务端仍在原域名提供页面和 API，旧版 H5 可通过 `/legacy` 临时访问。

浏览器必须保持页面打开才会持续刷新。手机 Bark 告警由服务端发送，独立于网页；浏览器本身不保证关闭或休眠后仍持续响铃。
