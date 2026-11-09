# Changelog

## 0.1.0 — 2026-11-09

首个 Codex 静态配置与规则路由 MVP：五种 Profile、模型能力映射、可解释规划、原生严格配置验证、保留注释的 TOML 合并、AGENTS.md 托管区块、项目 Skill、事务备份、幂等更新与回滚恢复。

发布前加固：修复真实 TOML 并发覆写的整数类型兼容问题；拒绝无效 Profile 覆写名称；加强 Windows 路径与 junction 越界检查；拒绝损坏的事务日志和备份状态；增加旧 Codex 并发字段回退测试。

提供可复现的完整配置演示、Codex 原生项目层/Skill 发现探针，以及全新虚拟环境中的 wheel 安装验证。配置 macOS、Linux、Windows 与 Python 3.11/3.13 的六组 CI。

当前没有自动任务执行、动态路由、模型调用拦截或节省比例评测。通过 GitHub Release 分发源码和 Python 构建产物，尚未上传 PyPI。实际验证范围见 [docs/validation.md](docs/validation.md)。
