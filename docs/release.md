# V0.1.0 发布与验收

本地实现和验收已完成。v0.1.0 通过 GitHub 分发，发布流程要求目标提交的六组 CI 全部通过后再创建标签与 Release。实际发布状态及下载见 [v0.1.0 Release](https://github.com/ffirestorm/Agent-Team-Router/releases/tag/v0.1.0)，对应构建记录见 [GitHub Actions](https://github.com/ffirestorm/Agent-Team-Router/actions)。本项目尚未上传 PyPI。

| 项目 | 当前证据 |
|---|---|
| 核心规则与配置安全 | macOS / Python 3.12：86 passed，1 个 Windows 专属用例跳过；显式覆盖 LF/CRLF 保留 |
| 真实 Codex 原生字段检查 | 0.167.0-alpha.3.2 本地通过 |
| 实际配置层加载 | 未信任项目禁用，临时信任后三个角色及并发覆写正确加载 |
| 实际 Skill 发现 | app-server skills/list 返回目标项目 Skill，enabled=true |
| 独立发行包安装 | 全新 venv、仓库外导入、控制台入口、五种 Profile、Skill 安装和回滚均通过 |
| 依赖与 Skill 格式 | pip check 与 Skill 验证通过 |
| 跨系统 CI | Linux/macOS/Windows × Python 3.11/3.13；执行状态及日志以 GitHub Actions 为准 |
| 真实模型权限、推理和额度优化 | 未测试；不作为 V0.1 已支持或已验证能力 |

## 本地复验

```bash
python -m pip install -e '.[dev]'
pytest -q
python -m build
python examples/distribution_smoke.py
python examples/demo.py
python examples/native_probe.py
```

前四项不要求安装 Codex。最后两项要求 Codex CLI，在临时目录内完成验证，不发起推理。发行包安装验证可用 `--wheelhouse /path/to/dependency-wheels` 离线运行。当前示例输出在 [validation.md](validation.md) 中汇总。

## 发布流程

提交并推送源码后，在远程运行六组 CI，处理真实跨系统差异，确认通过后再创建 v0.1.0 标签及 GitHub Release。附件提供 wheel、源码包及 SHA256SUMS；可用 `shasum -a 256 -c SHA256SUMS` 校验下载内容。上传 PyPI 是单独的后续发布操作，包名及发布身份需届时核实。

发布后 V0.2 优先实现 [可控的模型自动升级](v0.2-escalation.md)：单任务串行执行、独立验证、失败分类、一次有界升级，以及成本/成功率报告。复杂多 Agent 通信和并发调度后置。`examples/native_probe.py` 只是发布验收探针，不是运行时调度器；其元数据协议验证不能替代真实执行验收。完整路线见 [architecture.md](architecture.md)。
