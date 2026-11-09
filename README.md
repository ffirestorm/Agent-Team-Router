# Agent Team Router (ATR)

面向 AI Coding Agent 的轻量配置与模型分工工具。**V0.1 完整实现 Codex 静态配置生成及规则路由建议，不执行任务。** Python 3.11+，面向 Windows、macOS、Linux；原生 Codex 本机实测版本为 0.167.0-alpha.3.2，跨平台自动化测试结果见 [GitHub Actions](https://github.com/ffirestorm/Agent-Team-Router/actions)。

## 安装与快速上手

```bash
git clone https://github.com/ffirestorm/Agent-Team-Router.git
cd Agent-Team-Router
python3.11 -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
atr --version

mkdir demo-project
atr init --project demo-project --profile code-review
atr profile list
atr plan --project demo-project "分析项目中的 RAG 调用链"
atr apply --project demo-project --dry-run
atr apply --project demo-project
atr doctor --project demo-project
atr skill install --project demo-project
```

需要安装 Codex CLI、放入 PATH，并使用具有 `debug models --bundled` 和 `--strict-config app-server --print-startup-config` 能力的版本。ATR 会实测本机字段支持，无法验证时拒绝应用，避免盲目写入。所有命令默认针对当前目录；也支持 `atr --project /path/to/repo COMMAND`。项目目录须已存在。不写全局配置或认证信息。项目原生配置只有在 **Codex 信任项目后**才会生效，通常需要新会话。

本项目尚未发布到 PyPI；可从源码安装，或从 [GitHub Release](https://github.com/ffirestorm/Agent-Team-Router/releases/tag/v0.1.0) 下载 wheel 后用 `python -m pip install /path/to/agent_team_router-0.1.0-py3-none-any.whl` 安装。无需 API key，不进行真实模型推理。

## 已实现

- 检测 Codex 版本；读取 bundled 模型目录及推理强度；识别项目和全局现有 Agent 定义。
- 五种统一 Profile；可解释的中英关键词任务分析、复杂度/风险判定、模型回退、审查升级建议。
- 模型名称与核心逻辑分离；按本机目录过滤示例候选，支持自定义模型映射。
- 角色专属模型、推理强度、指令和 sandbox；生成 `.codex/config.toml`、角色 TOML、AGENTS.md 托管区块。
- 保留 TOML 注释与已有设置；dry-run diff、冲突检测、备份、幂等更新、事务故障恢复与回滚。
- 项目级标准 Skill 安装至 `.agents/skills/agent-team-router/SKILL.md`，支持自然语言入口。

## 当前不支持

不提供自动任务执行、实时调度、模型调用拦截、失败重试、真实费用/Token/延迟统计或自适应路由。不支持 Claude Code、Kimi Code、CodeBuddy、WorkBuddy 的原生配置生成；不支持的平台明确报错。

**原生角色配置 ≠ 自动运行时路由。** `atr plan` 的任务建议不会自动改变已经生成的静态团队。`atr apply` 依据当前 Profile 生成可供宿主选择的角色，实际是否创建 Agent、使用哪个角色及是否并行由 Codex 决定。Profile 并发数是子 Agent 并发上限，不包括主 Agent。

Plus 额度优化是设计目标；模型档位和成本倾向是启发式规则，尚无真实评测，不能宣称节省比例或保证模型相对价格。

## Profile 与路由

| Profile | 默认角色 | 并发上限 | 用途 |
|---|---|---:|---|
| economical | Worker | 1 | 减少常驻角色，高风险建议增加 Reviewer |
| balanced | Worker、Reviewer | 2 | 日常开发 |
| quality | Architect、Worker、Reviewer | 3 | 架构和复杂问题 |
| code-review | Explorer、Logic Tracer、Reviewer | 2 | 只读理解、调用链、复盘 |
| development | Explorer、Worker、Reviewer | 3 | 探索、开发和验证 |

```bash
atr profile use economical --project demo-project
atr plan --project demo-project "修复支付认证中的并发 bug"
atr apply --project demo-project --dry-run
```

上述高风险任务会建议更强模型审查；简单拼写/格式任务减少到一个角色。高风险优先于简单关键词。输出包含角色、模型、推理强度、推荐原因、所有其他已配置候选及 `stronger_review`。替代模型列表只表示候选，不保证同等能力。可用模型低于目标档位时会明确说明降级。没有模型时拒绝路由。

默认候选（只在本机目录识别到时加入）：`fast → gpt-6-luna`、`balanced → gpt-6.1-terra`、`reasoning → gpt-6.1-sol`、`expert → gpt-6-astra`。这些映射属于适配器数据；核心不依赖实际模型名称。

**本机目录列出模型不等于你的账号能调用。** 请按账号实际权限限制候选：

```bash
atr init --models gpt-6-luna,gpt-6.1-sol --project /path/to/new-project
# 新/私有模型：显式声明能力，而不是按名称猜测
atr init --catalog examples/catalog.json --model-map fast=local-fast \
  --model-map expert=local-expert --project /path/to/new-project
```

`--catalog` 是用户显式声明的离线目录，不是账号验证结果。bundled 来源在 apply 时重新核对；手动来源由用户维护。`plan` 使用本地快照，因此可以离线运行；实时目录变化由 apply/doctor 发现。

编辑 `.atr/config.toml` 可修改档位映射、角色规则、模型、推理强度、指令和并发数，示例见 [examples/config.toml](examples/config.toml)。每个模型只映射一个档位；缺失档位可回退，推理强度显式覆写必须被对应模型支持。只读角色不会因覆写变为可写。

## 安全应用、冲突与回滚

```bash
atr apply --dry-run     # 仅预览，含本机原生严格静态验证
atr apply              # 展示 diff 后应用；已有授权的命令行操作无需二次问答
atr rollback           # 撤销最新一次 apply 或 Skill 安装
atr recover            # 恢复进程被中断时保留的事务日志
```

只操作所选项目内文件。角色以 `atr_` 命名；已有同名未托管角色/文件、不同并发设置、显式禁用多 Agent 都会报冲突。AGENTS.md 仅更新 `<!-- ATR:BEGIN -->` / `<!-- ATR:END -->` 区块，区块外内容保持原字节文本；拒绝破损或重复边界。TOML 使用 tomlkit 保留注释和其他设置。

每次变更先在 `.atr/backups/` 保存原始字节和修改后哈希，再写入预备事务日志，通过逐文件原子替换完成。异常时恢复整个文件批次；崩溃可 `recover`。`.atr/lock` 防止 ATR 并发写入，崩溃遗留锁须确认无 ATR 进程后手动删除。多文件写入不是文件系统级原子事务；提交期间不要由其他进程修改这些文件。

人工修改或删除 ATR 已托管文件后，apply/rollback 会拒绝覆盖，**包括这些文件中的区块外修改**。请先保留人工变更并手动协调，而不是删备份强制覆盖。回滚按整个文件原始字节恢复，不删除新建空目录，保留备份与 ATR 初始化设置。需要撤销多次应用时重复 rollback。保留 `.atr/` 才能继续管理、检测冲突和回滚；其备份可能包含原配置敏感信息，请勿提交。

原生严格检查在临时 CODEX_HOME 中进行：验证字段与类型，不加载用户全局/管理员约束，不发起模型请求，也不证明有效账号权限。全局 Agent 名称仅供 doctor 只读诊断。`code-review` 的所有子角色使用 read-only sandbox，AGENTS.md 要求主 Agent 只读，但 ATR 无法强制改变已运行主 Agent 的权限。

## Skill

`atr skill install --project /path/to/repo` 安装项目级 Skill，安装也有备份和回滚。新 Codex 会话可使用 `$agent-team-router`，或自然语言请求“帮我配置节省额度的团队”“生成只读 Review 团队”“分析这个任务应该使用哪个模型”。Skill 调用真实 CLI，不把 Prompt 当作运行时。

## 开发与验证

```bash
python -m pip install -e '.[dev]'
pytest -q
python -m build
python examples/demo.py   # 临时目录；需真实 Codex，本地静态检查，不调用模型
python examples/native_probe.py  # 原生验证项目信任、角色和 Skill 发现；不创建执行回合
python examples/distribution_smoke.py  # 在全新 venv 安装 wheel；无需 Codex
```

测试大部分使用临时项目与模拟 Codex，覆盖规则、真实 TOML 覆写、合并、冲突、备份、失败恢复、回滚和 CLI 全流程。原生探针仅发送 `initialize`、`config/read`、`skills/list`，会确认未信任项目被禁用、临时信任后角色及 Skill 正确发现，不创建线程或发起推理。发行包验证默认需要从包索引安装依赖，可用 `--wheelhouse /path/to/wheels` 完全离线运行。

实际机器的验证记录见 [docs/validation.md](docs/validation.md)，发布准备状态见 [docs/release.md](docs/release.md)。测试及演示不消费真实模型额度。

## 结构与路线

```text
src/atr/
  core.py                 # 平台无关任务分析、模型选择、团队规划、未来接口
  profiles.py             # 五种统一 Profile 与覆写规则
  config.py               # 项目设置与模型注册数据
  adapters/codex.py       # Codex 发现、渲染和原生验证
  storage.py              # 安全路径、事务、备份、回滚
  service.py / cli.py     # 用例编排与 CLI
  skill/SKILL.md          # 随 wheel 分发的标准 Skill
tests/  examples/  evals/  docs/
```

具体架构边界及后续执行/评测计划见 [docs/architecture.md](docs/architecture.md)。V0.2 首要目标是 [可控的模型自动升级](docs/v0.2-escalation.md)：先完成单任务执行、失败分类、有界升级与成本/成功率记录，再考虑复杂多 Agent 通信和并发。V1.0 将使用公开可复现任务集比较质量、总成本与延迟，再引入数据驱动选择。当前只预留接口与评测数据格式，没有伪造运行时实现。

## 配置规范来源

OpenAI 官方 [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[Skills](https://learn.chatgpt.com/docs/build-skills)、[app-server](https://learn.chatgpt.com/docs/app-server)。首次开发时直接访问文档站返回 Forbidden，先核对了 [OpenAI 官方 config.schema.json](https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json) 及本机 CLI；发布准备阶段已通过文档工具重新读取官方页面，并实测 app-server 的配置和 Skill 发现。本机支持 `max_concurrent_threads_per_session`，旧别名 `max_threads` 只在探测前者失败时尝试。不根据版本号臆测字段，也不会静默跳过检查。

MIT License.
