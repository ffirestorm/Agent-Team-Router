# Changelog

## 0.2.0.dev0 — 未发布

- 三档初始模型策略（simple/medium/complex），支持真实 TOML 覆写及手动指定难度。
- 单任务隔离快照执行、既有测试基线、固定 JSON 验证、失败分类、同模型一次修正和一次有界升级。
- 取消、超时、尝试预算、调用后 Token 门槛、候选补丁、执行记录恢复与用量/成功率汇总。
- 使用真实 ATR 工程任务试跑：保留环境故障，修复提供者传输适配后，Terra 一次实现策略模块与测试，经独立验收和回归后集成。未人为触发真实 Sol 升级。
- 原生执行仅在本机实测，跨平台模拟测试和打包状态见 GitHub Actions；未发布 V0.2，不宣称实际升级收益或 Plus 节省比例。详见 [运行说明](docs/v0.2-runtime.md)。

## 0.1.0 — 2026-11-09

首个 Codex 静态配置与规则路由 MVP：五种 Profile、模型能力映射、可解释规划、原生严格配置验证、保留注释的 TOML 合并、AGENTS.md 托管区块、项目 Skill、事务备份、幂等更新与回滚恢复。

发布前加固：修复真实 TOML 并发覆写的整数类型兼容问题；拒绝无效 Profile 覆写名称；加强 Windows 路径与 junction 越界检查；拒绝损坏的事务日志和备份状态；增加旧 Codex 并发字段回退测试。

提供可复现的完整配置演示、Codex 原生项目层/Skill 发现探针，以及全新虚拟环境中的 wheel 安装验证。配置 macOS、Linux、Windows 与 Python 3.11/3.13 的六组 CI。

当前没有自动任务执行、动态路由、模型调用拦截或节省比例评测。通过 GitHub Release 分发源码和 Python 构建产物，尚未上传 PyPI。实际验证范围见 [docs/validation.md](docs/validation.md)。
