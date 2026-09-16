# 销售总台升级包

目录中的两个 `.tar.gz` 是可直接上传到 `sales-console` 升级管理页的验证包：

- `agentscope-app-v2.0.1.tar.gz`：`app`，龙信业务应用包；
- `agentscope-core-v2.0.8.tar.gz`：`core`，AgentScope 平台核心包。

两包都包含根目录 `manifest.json`。`app` 包包含 `server.js` 和 `public/index.html`；`core` 包包含 `agentscope/__init__.py`。版本号必须与升级管理页填写的版本号一致。
