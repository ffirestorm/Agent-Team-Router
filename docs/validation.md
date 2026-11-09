# V0.1 验证记录

日期：2026-11-09（Asia/Shanghai）。以下记录本地验证证据；远程构建状态见 [GitHub Actions](https://github.com/ffirestorm/Agent-Team-Router/actions)。本地验证未修改全局 Codex 配置或认证信息，未进行模型推理。

## 环境

- macOS / arm64
- Python 3.12.15（系统默认 Python 3.9 不满足要求，使用已安装的 Python 3.12 创建项目虚拟环境）
- Codex CLI `0.167.0-alpha.3.2`
- tomlkit `0.15.2`；pytest `9.2.0`
- 首次直接访问 OpenAI 文档站返回 Forbidden；从官方 GitHub 获取完整 `config.schema.json`，并使用实际 CLI 严格静态检查核实字段。发布准备阶段已通过文档工具读取当前官方配置、Subagents、Skills 和 app-server 文档。

## 自动化测试

```text
$ .venv/bin/python -m pytest -q
85 passed, 1 skipped
```

覆盖模型档位与缺失档位回退、任务风险优先级、Profile/角色覆写、只读团队、推理强度校验、非默认模型显式映射、原生 TOML 注释与已有角色保留、AGENTS.md 边界、重复应用、角色集合迁移、Codex 缺失/字段不兼容/旧并发字段/目录变化、dry-run 不写文件、预览并发冲突、人工变更保护、原始字节备份回滚、多次回滚、磁盘异常恢复、中断事务恢复、损坏日志及备份保护、符号链接/越界路径拒绝、CLI 端到端及公开规则回归用例。

发布审查发现并修复：tomlkit 的整数对象曾被 `type(value) is int` 误拒，导致真实 TOML 并发覆写不可用；新增从真实文件加载、渲染、应用、诊断的完整回归。另修复 Windows 驱动器相对路径及根相对路径逃逸；在所有系统上测试这些路径拼写，并增加 Windows 目录 junction 专属测试。本机跳过的 1 项就是该 Windows 测试。

大多数测试使用隔离临时目录、模拟 Codex 命令和独立 CODEX_HOME，不依赖账号或网络。

## 构建与安装

```text
$ python -m build --no-isolation
Successfully built agent_team_router-0.1.0.tar.gz and agent_team_router-0.1.0-py3-none-any.whl
```

验证 wheel 内包含 `atr/skill/SKILL.md`。首次在独立 `/tmp/atr-wheel-smoke` 虚拟环境安装 wheel 和唯一运行时依赖 tomlkit，从仓库外的临时工作目录启动。导入路径为该环境 `site-packages/atr/__init__.py`，`atr --version` 返回 `0.1.0`。

发布准备阶段新增跨平台 `examples/distribution_smoke.py`，每次创建全新虚拟环境，从仓库外检查 wheel 导入、真实控制台入口、五种 Profile、打包 Skill 的安装与回滚。实际离线运行通过，结果见 [distribution-smoke.json](distribution-smoke.json)。CI 的六个系统/Python 组合都配置了此验证，具体执行记录以 GitHub Actions 为准。

Skill 通过 skill-creator 的 `quick_validate.py`：`Skill is valid!`。`pip check` 无依赖冲突。

## 真实 Codex 元数据与配置演示

复现：安装后运行 `python examples/demo.py`。完整实际输出见 [demo-transcript.txt](demo-transcript.txt)。

演示顺序为 `init → profile use code-review → plan → apply --dry-run → apply → doctor → apply → skill install → rollback → rollback`。`doctor` 返回 `ok: true`，严格配置校验通过，无待应用文件，识别到三个项目角色；第二次 apply 无差异，不产生重复备份。Skill 安装成功后回滚，再回滚原生配置，已有 AGENTS.md 完整恢复。

该实际示例的任务是“分析项目中的 RAG 调用链”，输出分工如下：

| 角色 | 模型 | 推理强度 | sandbox |
|---|---|---|---|
| Explorer | gpt-6-luna | low | read-only |
| Logic Tracer | gpt-6.1-sol | high | read-only |
| Reviewer | gpt-6.1-sol | high | read-only |

并发上限为 2。实际生成 `.codex/config.toml`、三个角色 TOML 和 AGENTS.md 托管区块。本机确认模型候选及强度来自 bundled 目录；**账号是否能调用这些模型、有效额度及实际模型行为未验证**。

另保留仓库内 `demo-project/`（已 gitignore）作为可直接查看的应用结果，包含只读团队、Skill 和备份。临时演示项目在结束时自动删除。

## Codex 原生配置加载与 Skill 发现

复现：`python examples/native_probe.py`。实际结果见 [native-probe.json](native-probe.json)。

脚本创建临时项目和独立 CODEX_HOME，经真实 CLI 生成团队与 Skill，并将真实 TOML 并发覆写设置为 1。使用官方 [app-server 协议](https://learn.chatgpt.com/docs/app-server) 发送只读元数据请求：

1. 未信任时，`config/read` 不加载三个 ATR 角色，并报告项目配置层被禁用。
2. 仅在临时 CODEX_HOME 中信任这个临时项目，重启独立 app-server 后，`config/read` 返回三个角色、正确的绝对角色文件路径及并发覆写 1。
3. `skills/list` 发现并启用目标项目中的 `agent-team-router` Skill。

全过程只允许 `initialize`、`config/read`、`skills/list`，不创建线程，不发送 `turn/start`，不运行子 Agent。证明的是配置发现和加载，不是账号权限、实际角色执行或模型行为。用户全局信任设置未修改。

## 尚未验证与限制

- 本地原生 Codex 验证范围为 macOS/Python 3.12；Windows/Linux 和 Python 3.11/3.13 的模拟执行器测试及发行包验证由远程 CI 记录，不代表这些系统的真实 Codex 模型执行已验证。
- 未进行真实模型推理，因此没有成功率、节省比例、成本或延迟评测。
- 独立 CODEX_HOME 的严格验证证明原生配置可解析，不证明项目被信任、管理员要求得到满足或正在运行的宿主已加载新角色。
- 当前只支持静态配置和规划；自动执行、失败升级和自适应策略见 [architecture.md](architecture.md)。
