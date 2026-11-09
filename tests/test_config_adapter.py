import json
import subprocess
import tomllib

import pytest

from atr.adapters import get_adapter
from atr.adapters.codex import CodexAdapter, managed_block, normalize_catalog
from atr.config import initialize, load, use_profile
from atr.core import ATRException
from atr.service import doctor, prepare
from atr.storage import apply, read


def init(root, **kwargs):
    return initialize(root, kwargs.pop("profile", "code-review"), "codex", kwargs.pop("models", None), kwargs.pop("catalog", None), **kwargs)


def test_comment_preserving_merge_and_existing_agents(project, fake_codex):
    (project / ".codex").mkdir()
    original = '# personal comment\napproval_policy = "on-request"\n[agents.personal]\ndescription="my role"\n'
    (project / ".codex/config.toml").write_text(original)
    (project / "AGENTS.md").write_text("# Personal instructions\nKeep this.\n")
    init(project)
    changes, before = prepare(project)
    result = changes[".codex/config.toml"].decode()
    doc = tomllib.loads(result)
    assert result.startswith("# personal comment")
    assert doc["approval_policy"] == "on-request"
    assert doc["agents"]["personal"]["description"] == "my role"
    assert doc["agents"]["atr_explorer"]["config_file"] == "agents/atr_explorer.toml"
    assert changes["AGENTS.md"].startswith(b"# Personal instructions\nKeep this.\n")
    for path, data in changes.items():
        if path.startswith(".codex/agents/"):
            assert tomllib.loads(data.decode())["sandbox_mode"] == "read-only"
    assert not (project / ".atr/state.json").exists()  # pure preparation
    apply(project, changes, before)
    assert doctor(project)["ok"]


def test_repeat_apply_and_profile_transition_removes_stale_roles(project, fake_codex):
    init(project)
    changes, before = prepare(project)
    apply(project, changes, before)
    state = read(project, ".atr/state.json")
    changes, before = prepare(project)
    assert "无需修改" in apply(project, changes, before)
    assert read(project, ".atr/state.json") == state
    use_profile(project, "economical")
    changes, before = prepare(project)
    apply(project, changes, before)
    assert not (project / ".codex/agents/atr_explorer.toml").exists()
    doc = tomllib.loads((project / ".codex/config.toml").read_text())
    assert "atr_explorer" not in doc["agents"]
    assert "atr_worker" in doc["agents"]


@pytest.mark.parametrize("original", ["", "personal", "personal\n", "personal\n\n"])
def test_agents_block_idempotence(original):
    first = managed_block(original, "test")
    assert managed_block(first, "test") == first
    assert first.startswith(original)
    assert managed_block(first + "\nTail\n", "changed").endswith("\nTail\n")


@pytest.mark.parametrize("text", ["<!-- ATR:BEGIN -->", "<!-- ATR:END --><!-- ATR:BEGIN -->", "<!-- ATR:BEGIN --><!-- ATR:BEGIN --><!-- ATR:END --><!-- ATR:END -->"])
def test_corrupt_boundaries_rejected(text):
    with pytest.raises(ATRException):
        managed_block(text, "x")


@pytest.mark.parametrize("native", [
    '[agents.atr_explorer]\ndescription="existing"\n',
    '[agents]\nmax_threads=9\n',
    '[features]\nmulti_agent=false\n',
    '[agents]\nenabled=false\n',
    'features=true\n',
    'not valid TOML \n',
])
def test_native_conflicts(project, fake_codex, native):
    init(project)
    (project / ".codex").mkdir()
    (project / ".codex/config.toml").write_text(native)
    with pytest.raises(ATRException):
        prepare(project)
    assert (project / ".codex/config.toml").read_text() == native


def test_reject_unowned_role_file(project, fake_codex):
    init(project)
    path = project / ".codex/agents/atr_explorer.toml"
    path.parent.mkdir(parents=True)
    path.write_text("personal file")
    with pytest.raises(ATRException):
        prepare(project)


def test_missing_codex(monkeypatch):
    monkeypatch.setattr("atr.adapters.codex.shutil.which", lambda _: None)
    with pytest.raises(ATRException, match="未检测到 Codex"):
        CodexAdapter().version()


def test_incompatible_codex_fields(project, fake_codex, monkeypatch):
    init(project)
    original = CodexAdapter._run
    def run(self, arguments, home, timeout=20):
        if arguments[0] == "--strict-config":
            return subprocess.CompletedProcess([], 1, "unknown field", "")
        return original(self, arguments, home, timeout)
    monkeypatch.setattr(CodexAdapter, "_run", run)
    with pytest.raises(ATRException, match="不支持"):
        prepare(project)
    assert not (project / ".codex").exists()


def test_catalog_disappears_before_apply(project, fake_codex):
    init(project)
    fake_codex["catalog"]["models"] = []
    with pytest.raises(ATRException, match="不在本机目录"):
        prepare(project)


def test_unavailable_models_and_duplicate_init(project, fake_codex):
    with pytest.raises(ATRException, match="模型不在目录"):
        init(project, models="made-up")
    init(project, models="gpt-6-luna")
    assert len(load(project)[3]) == 1
    with pytest.raises(ATRException, match="已初始化"):
        init(project)


def test_custom_model_mapping(project, fake_codex):
    path = project / "catalog.json"
    path.write_text(json.dumps({"models": [{"id": "custom-v1", "efforts": ["medium"]}]}))
    init(project, catalog=str(path), model_map=["expert=custom-v1"])
    assert load(project)[3][0].id == "custom-v1"
    prepare(project)


def test_unknown_platform():
    with pytest.raises(ATRException, match="尚未支持"):
        get_adapter("claude")


def test_explicit_unsupported_effort(project, fake_codex):
    init(project)
    path = project / ".atr/config.toml"
    path.write_text(path.read_text() + '\n[profiles.code-review.roles.explorer]\neffort="ultra"\n')
    with pytest.raises(ATRException, match="不支持 ultra"):
        prepare(project)


def test_normalize_invalid_catalog():
    with pytest.raises(ATRException):
        normalize_catalog([{"id": "bad", "efforts": []}])


@pytest.mark.parametrize("models", [None, ["bad"], [{"id": "x", "efforts": "high"}], [{"id": "x", "efforts": [{}]}]])
def test_malformed_catalog_is_actionable_error(models):
    with pytest.raises(ATRException):
        normalize_catalog(models)


def test_instructions_cannot_break_managed_block():
    with pytest.raises(ATRException, match="边界标记"):
        managed_block("", "include <!-- ATR:BEGIN -->")


def test_profile_change_during_native_validation_cannot_apply_stale_plan(project, fake_codex):
    init(project)
    changes, before = prepare(project)
    use_profile(project, "economical")
    with pytest.raises(ATRException, match="预览期间"):
        apply(project, changes, before)
    assert not (project / ".codex").exists()


def test_profile_overrides_from_real_toml(project, fake_codex):
    init(project)
    path = project / ".atr/config.toml"
    path.write_text(path.read_text(encoding="utf-8") + '''
[profiles.code-review]
concurrency = 1
[profiles.code-review.roles.explorer]
enabled = false
[profiles.code-review.roles.logic_tracer]
model = "gpt-6-astra"
effort = "high"
''', encoding="utf-8")
    _, profile, _, _ = load(project)
    assert type(profile.concurrency) is int
    assert profile.concurrency == 1
    changes, before = prepare(project)
    parsed = tomllib.loads(changes[".codex/config.toml"].decode())
    assert parsed["agents"]["max_concurrent_threads_per_session"] == 1
    assert "atr_explorer" not in parsed["agents"]
    tracer = tomllib.loads(changes[".codex/agents/atr_logic_tracer.toml"].decode())
    assert tracer["model"] == "gpt-6-astra"
    apply(project, changes, before)
    assert doctor(project)["ok"]


def test_older_codex_concurrency_alias(project, fake_codex, monkeypatch):
    original = CodexAdapter._run
    def old_run(self, arguments, home, timeout=20):
        if arguments[0] == "--strict-config":
            config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            if "max_concurrent_threads_per_session" in config.get("agents", {}):
                return subprocess.CompletedProcess(arguments, 1, "unsupported concurrency field", "")
        return original(self, arguments, home, timeout)
    monkeypatch.setattr(CodexAdapter, "_run", old_run)
    init(project)
    changes, _ = prepare(project)
    agents = tomllib.loads(changes[".codex/config.toml"].decode())["agents"]
    assert agents["max_threads"] == 2
    assert "max_concurrent_threads_per_session" not in agents
