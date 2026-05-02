"""Unit tests for the convert agent."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


class TestBuildPrompt:
    def test_includes_app_name(self):
        from kamiwaza_extensions.app_analyzer import AnalysisResult, ServiceInfo
        from kamiwaza_extensions.convert_agent import build_prompt

        result = AnalysisResult(
            app_dir=Path("/tmp/myapp"),
            app_name="myapp",
            services=[ServiceInfo(name="backend", language="python", ports=[8000])],
            file_contents={"main.py": "print('hello')"},
        )
        prompt = build_prompt(result)
        assert "myapp" in prompt
        assert "backend" in prompt
        assert "python" in prompt
        assert "kamiwaza-extensions-lib" in prompt

    def test_includes_repo_inventory_hints(self):
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import build_prompt

        result = AnalysisResult(
            app_dir=Path("/tmp/myapp"),
            app_name="myapp",
            repo_tree=["index.html", "assets/", "package.json"],
            candidate_entrypoints=["index.html"],
            runtime_hints=["static-html", "node-package"],
        )
        prompt = build_prompt(result)
        # The per-call prompt still carries the per-app inventory data.
        assert "index.html" in prompt
        assert "static-html" in prompt

    def test_runtime_rules_live_in_agent_guidance(self):
        """Standing rules (non-root, port 8080, distroless) belong in the
        guidance file shipped as CLAUDE.md/AGENTS.md/system prompt — not
        repeated per-call."""
        from kamiwaza_extensions.convert_agent import _AGENT_GUIDANCE

        assert _AGENT_GUIDANCE, "agent_guidance.md must be loadable"
        lowered = _AGENT_GUIDANCE.lower()
        assert "non-root" in lowered
        assert "8080" in lowered
        assert "distroless" in lowered or "chainguard" in lowered
        assert "/bin/sh" in lowered  # distroless gotcha


class TestParseResponse:
    def test_parse_valid_json(self):
        from kamiwaza_extensions.convert_agent import parse_response

        response = json.dumps({
            "modifications": [
                {
                    "path": "requirements.txt",
                    "action": "modify",
                    "content": "fastapi\nkamiwaza-extensions-lib>=0.1.0\n",
                    "description": "Added runtime lib",
                }
            ],
            "manual_items": ["Check auth placement"],
            "summary": "Added SDK integration",
        })
        plan = parse_response(response)
        assert len(plan.modifications) == 1
        assert plan.modifications[0].path == "requirements.txt"
        assert plan.modifications[0].action == "modify"
        assert len(plan.manual_items) == 1
        assert plan.summary == "Added SDK integration"
        assert plan.success is True

    def test_parse_json_in_code_block(self):
        from kamiwaza_extensions.convert_agent import parse_response

        response = '```json\n{"modifications": [], "manual_items": [], "summary": "ok"}\n```'
        plan = parse_response(response)
        assert plan.summary == "ok"
        assert plan.success is True

    def test_parse_invalid_json(self):
        from kamiwaza_extensions.convert_agent import parse_response

        plan = parse_response("this is not json")
        assert plan.success is False
        assert len(plan.manual_items) > 0
        assert "could not be parsed" in plan.summary.lower() or "could not be parsed" in plan.manual_items[0].lower()


class TestApplyPlan:
    def test_create_file(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="kamiwaza.json",
                    action="create",
                    content='{"name": "test"}',
                    description="generated metadata",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 1
        assert (tmp_path / "kamiwaza.json").exists()
        assert json.loads((tmp_path / "kamiwaza.json").read_text()) == {"name": "test"}

    def test_modify_file(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        (tmp_path / "requirements.txt").write_text("fastapi\n")
        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="requirements.txt",
                    action="modify",
                    content="fastapi\nkamiwaza-extensions-lib>=0.1.0\n",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 1
        assert "kamiwaza-extensions-lib" in (tmp_path / "requirements.txt").read_text()

    def test_append_to_file(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        (tmp_path / "main.py").write_text("# existing\n")
        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="main.py",
                    action="append",
                    content="# new stuff\n",
                )
            ]
        )
        apply_plan(plan, tmp_path)
        content = (tmp_path / "main.py").read_text()
        assert "existing" in content
        assert "new stuff" in content

    def test_dry_run_does_not_write(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        plan = ConversionPlan(
            modifications=[
                FileModification(path="new_file.py", action="create", content="# new")
            ]
        )
        applied = apply_plan(plan, tmp_path, dry_run=True)
        assert len(applied) == 1
        assert "[dry-run]" in applied[0]
        assert not (tmp_path / "new_file.py").exists()

    def test_creates_parent_dirs(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        plan = ConversionPlan(
            modifications=[
                FileModification(path="deep/nested/file.py", action="create", content="# hi")
            ]
        )
        apply_plan(plan, tmp_path)
        assert (tmp_path / "deep" / "nested" / "file.py").exists()

    def test_skips_empty_content(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        plan = ConversionPlan(
            modifications=[FileModification(path="", action="create", content="")]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 0

    def test_rejects_path_traversal(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="../../../etc/evil.conf",
                    action="create",
                    content="malicious",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 0
        assert not (tmp_path.parent / "etc" / "evil.conf").exists()

    def test_rejects_absolute_path(self, tmp_path):
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification, apply_plan

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="/tmp/evil.py",
                    action="create",
                    content="malicious",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 0

    def test_copy_action_vendors_from_source_root(self, tmp_path):
        from kamiwaza_extensions.convert_agent import (
            ConversionPlan,
            FileModification,
            apply_plan,
        )

        # Source tree: monorepo root with shared/ artifact + extension subdir.
        source_root = tmp_path
        ext = tmp_path / "apps" / "my-ext"
        ext.mkdir(parents=True)
        shared = source_root / "shared" / "python" / "dist"
        shared.mkdir(parents=True)
        wheel = shared / "auth-1.0.whl"
        wheel.write_bytes(b"BINARY-WHEEL-BYTES")

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="vendor/auth-1.0.whl",
                    action="copy",
                    source_path="shared/python/dist/auth-1.0.whl",
                    description="vendor wheel",
                )
            ]
        )

        applied = apply_plan(plan, ext, source_root=source_root)

        assert len(applied) == 1
        copied = ext / "vendor" / "auth-1.0.whl"
        assert copied.exists()
        assert copied.read_bytes() == b"BINARY-WHEEL-BYTES"

    def test_copy_action_skips_when_source_missing(self, tmp_path):
        from kamiwaza_extensions.convert_agent import (
            ConversionPlan,
            FileModification,
            apply_plan,
        )

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="vendor/missing.whl",
                    action="copy",
                    source_path="shared/missing.whl",
                )
            ]
        )

        applied = apply_plan(plan, tmp_path, source_root=tmp_path)

        assert applied == []
        assert not (tmp_path / "vendor" / "missing.whl").exists()

    def test_copy_action_rejects_source_outside_source_root(self, tmp_path):
        from kamiwaza_extensions.convert_agent import (
            ConversionPlan,
            FileModification,
            apply_plan,
        )

        ext = tmp_path / "ext"
        ext.mkdir()
        outside = tmp_path.parent / "outside.whl"
        # Don't actually create it; the path-traversal check should fire first.

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="vendor/x.whl",
                    action="copy",
                    source_path="../outside.whl",
                )
            ]
        )

        applied = apply_plan(plan, ext, source_root=ext)

        assert applied == []
        assert not (ext / "vendor" / "x.whl").exists()
        assert not outside.exists()

    def test_dedupe_strips_manual_items_for_handled_paths(self):
        from kamiwaza_extensions.convert_agent import (
            ConversionPlan,
            FileModification,
            _dedupe_manual_items_against_modifications,
        )

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="vendor/auth-1.0.whl",
                    action="copy",
                    source_path="shared/python/dist/auth-1.0.whl",
                ),
                FileModification(
                    path="backend/Dockerfile",
                    action="modify",
                    content="FROM python\n",
                ),
            ],
            manual_items=[
                "Vendor shared/python/dist/auth-1.0.whl into the extension repo.",
                "Modify backend/Dockerfile to drop monorepo-relative paths.",
                "Confirm the read-only root filesystem assumption holds at runtime.",
                "Decide whether to keep ports 8000/3000 or remap to 8080.",
            ],
        )

        _dedupe_manual_items_against_modifications(plan)

        # Items naming handled paths are dropped.
        assert all("auth-1.0.whl" not in item for item in plan.manual_items)
        assert all("backend/Dockerfile" not in item for item in plan.manual_items)
        # Items that don't reference a handled path are preserved.
        assert any("read-only root" in item for item in plan.manual_items)
        assert any("8080" in item for item in plan.manual_items)

    def test_dedupe_keeps_items_without_action_verbs(self):
        from kamiwaza_extensions.convert_agent import (
            ConversionPlan,
            FileModification,
            _dedupe_manual_items_against_modifications,
        )

        plan = ConversionPlan(
            modifications=[
                FileModification(path="docker-compose.yml", action="modify", content="x")
            ],
            # Mentions a handled path but is purely informational — keep it.
            manual_items=["Note: docker-compose.yml now uses build context '.'."],
        )

        _dedupe_manual_items_against_modifications(plan)

        assert len(plan.manual_items) == 1

    def test_copy_action_missing_source_path_skipped(self, tmp_path):
        from kamiwaza_extensions.convert_agent import (
            ConversionPlan,
            FileModification,
            apply_plan,
        )

        plan = ConversionPlan(
            modifications=[
                FileModification(path="vendor/x.whl", action="copy", source_path=None)
            ]
        )

        applied = apply_plan(plan, tmp_path, source_root=tmp_path)
        assert applied == []


class TestRunAgent:
    def test_fallback_without_llm_creates_basic_scaffold(self, monkeypatch, tmp_path):
        from kamiwaza_extensions import convert_agent
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import run_agent

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # Also disable CLI provider discovery so the fallback path is reached
        # even on dev machines that have `claude` / `codex` installed.
        monkeypatch.setattr(convert_agent.shutil, "which", lambda _name: None)

        result = AnalysisResult(
            app_dir=tmp_path,
            app_name="test",
        )
        plan = run_agent(result)
        assert plan.success is True
        assert "basic" in plan.summary.lower()
        assert len(plan.manual_items) > 0
        assert (tmp_path / "kamiwaza.json").exists()
        assert (tmp_path / "CONVERT_NOTES.md").exists()

    def test_existing_kamiwaza_json_is_not_overwritten(self, monkeypatch, tmp_path):
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import run_agent

        existing = {
            "name": "custom-name",
            "version": "9.9.9",
            "source_type": "user_repo",
            "visibility": "private",
            "description": "custom",
            "risk_tier": 1,
            "verified": False,
            "kz_ext_version": ">=0.1.0,<1.0.0",
        }
        (tmp_path / "kamiwaza.json").write_text(json.dumps(existing) + "\n")
        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        analysis = AnalysisResult(
            app_dir=tmp_path,
            app_name="demo",
            extension_type="app",
            conversion_mode="generic",
            file_contents={"index.html": "<html><body>Hello</body></html>"},
            candidate_entrypoints=["index.html"],
            runtime_hints=["static-html"],
        )

        responses = iter([
            json.dumps({
                "extension_type": "app",
                "conversion_mode": "add_minimal_wrapper",
                "primary_service": "web",
                "required_files": ["docker-compose.yml", "CONVERT_NOTES.md"],
                "runtime_summary": "Static HTML site served by nginx",
                "manual_items": [],
            }),
            json.dumps({
                "modifications": [
                    {
                        "path": "kamiwaza.json",
                        "action": "modify",
                        "content": json.dumps({"name": "llm-name", "version": "0.1.0"}),
                        "description": "Overwrite manifest",
                    },
                    {
                        "path": "docker-compose.yml",
                        "action": "create",
                        "content": (
                            "services:\n"
                            "  web:\n"
                            "    image: nginxinc/nginx-unprivileged:stable-alpine\n"
                            "    ports:\n"
                            "      - \"8080:8080\"\n"
                            "    deploy:\n"
                            "      resources:\n"
                            "        limits:\n"
                            "          cpus: \"1.0\"\n"
                            "          memory: \"1G\"\n"
                        ),
                        "description": "Compose only",
                    },
                ],
                "manual_items": [],
                "summary": "Attempted overwrite",
            }),
        ])

        monkeypatch.setattr(
            "kamiwaza_extensions.convert_agent.call_llm",
            lambda prompt: next(responses, None),
        )

        plan = run_agent(analysis, dry_run=False)

        assert plan.success is True
        persisted = json.loads((tmp_path / "kamiwaza.json").read_text())
        assert persisted["name"] == "custom-name"
        assert persisted["version"] == "9.9.9"
        # The "preserved kamiwaza.json" notice now lands in the summary (a
        # status note, not a user follow-up), keeping manual_items focused
        # on actionable items.
        assert "Preserved existing kamiwaza.json" in plan.summary

    def test_invalid_existing_kamiwaza_json_can_be_repaired(self, monkeypatch, tmp_path):
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import run_agent

        existing = {
            "name": "demo",
            "version": "not-semver",
            "source_type": "user_repo",
            "visibility": "private",
            "description": "broken",
            "risk_tier": 1,
            "verified": False,
        }
        (tmp_path / "kamiwaza.json").write_text(json.dumps(existing) + "\n")
        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        analysis = AnalysisResult(
            app_dir=tmp_path,
            app_name="demo",
            extension_type="app",
            conversion_mode="generic",
            file_contents={"index.html": "<html><body>Hello</body></html>"},
            candidate_entrypoints=["index.html"],
            runtime_hints=["static-html"],
        )

        responses = iter([
            json.dumps({
                "extension_type": "app",
                "conversion_mode": "add_minimal_wrapper",
                "primary_service": "web",
                "required_files": ["docker-compose.yml", "CONVERT_NOTES.md", "kamiwaza.json"],
                "runtime_summary": "Static HTML site served by nginx",
                "manual_items": [],
            }),
            json.dumps({
                "modifications": [
                    {
                        "path": "kamiwaza.json",
                        "action": "modify",
                        "content": json.dumps({
                            "name": "demo",
                            "version": "0.1.0",
                            "type": "app",
                            "source_type": "user_repo",
                            "visibility": "private",
                            "description": "repaired manifest",
                            "risk_tier": 1,
                            "verified": False,
                            "kz_ext_version": ">=0.12.0,<1.0.0",
                        }, indent=4)
                        + "\n",
                        "description": "Repair invalid manifest",
                    },
                    {
                        "path": "docker-compose.yml",
                        "action": "create",
                        "content": (
                            "services:\n"
                            "  web:\n"
                            "    image: nginxinc/nginx-unprivileged:stable-alpine\n"
                            "    ports:\n"
                            "      - \"8080:8080\"\n"
                            "    deploy:\n"
                            "      resources:\n"
                            "        limits:\n"
                            "          cpus: \"1.0\"\n"
                            "          memory: \"1G\"\n"
                        ),
                        "description": "Use an unprivileged static web runtime",
                    },
                ],
                "manual_items": [],
                "summary": "Repair existing manifest and add runtime wrapper",
            }),
        ])

        monkeypatch.setattr(
            "kamiwaza_extensions.convert_agent.call_llm",
            lambda prompt: next(responses, None),
        )

        plan = run_agent(analysis, dry_run=False)

        assert plan.success is True
        persisted = json.loads((tmp_path / "kamiwaza.json").read_text())
        assert persisted["version"] == "0.1.0"
        assert persisted["description"] == "repaired manifest"
        assert any("keeping AI-proposed metadata repairs" in item for item in plan.manual_items)

    def test_validation_repair_loop_applies_fixed_plan(self, monkeypatch, tmp_path):
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import run_agent

        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        analysis = AnalysisResult(
            app_dir=tmp_path,
            app_name="demo",
            extension_type="app",
            conversion_mode="generic",
            file_contents={"index.html": "<html><body>Hello</body></html>"},
            candidate_entrypoints=["index.html"],
            runtime_hints=["static-html"],
        )

        responses = iter([
            json.dumps({
                "extension_type": "app",
                "conversion_mode": "add_minimal_wrapper",
                "primary_service": "web",
                "required_files": ["Dockerfile", "docker-compose.yml", "kamiwaza.json", "CONVERT_NOTES.md"],
                "runtime_summary": "Static HTML site served by nginx",
                "manual_items": [],
            }),
            json.dumps({
                "modifications": [
                    {
                        "path": "Dockerfile",
                        "action": "create",
                        "content": (
                            "FROM nginx:alpine\n"
                            "COPY index.html /usr/share/nginx/html/index.html\n"
                            "RUN printf 'ok' > /usr/share/nginx/html/health\n"
                            "EXPOSE 80\n"
                        ),
                        "description": "Initial rootful nginx wrapper",
                    },
                    {
                        "path": "docker-compose.yml",
                        "action": "create",
                        "content": (
                            "services:\n"
                            "  web:\n"
                            "    build: .\n"
                            "    ports:\n"
                            "      - \"8080:80\"\n"
                            "    deploy:\n"
                            "      resources:\n"
                            "        limits:\n"
                            "          cpus: \"1.0\"\n"
                            "          memory: \"1G\"\n"
                        ),
                        "description": "Initial compose that should fail platform validation",
                    },
                ],
                "manual_items": [],
                "summary": "Created initial static-site conversion",
            }),
            json.dumps({
                "modifications": [
                    {
                        "path": "Dockerfile",
                        "action": "create",
                        "content": (
                            "FROM nginxinc/nginx-unprivileged:stable-alpine\n"
                            "COPY index.html /usr/share/nginx/html/index.html\n"
                            "COPY nginx.conf /etc/nginx/conf.d/default.conf\n"
                            "EXPOSE 8080\n"
                        ),
                        "description": "Serve the static site with a non-root nginx runtime",
                    },
                    {
                        "path": "nginx.conf",
                        "action": "create",
                        "content": (
                            "server {\n"
                            "    listen 8080;\n"
                            "    server_name localhost;\n"
                            "    root /usr/share/nginx/html;\n"
                            "    index index.html;\n"
                            "    location = /health {\n"
                            "        access_log off;\n"
                            "        return 200 'ok';\n"
                            "    }\n"
                            "    location / {\n"
                            "        try_files $uri $uri/ /index.html;\n"
                            "    }\n"
                            "    client_body_temp_path /tmp/client_temp;\n"
                            "    proxy_temp_path /tmp/proxy_temp;\n"
                            "    fastcgi_temp_path /tmp/fastcgi_temp;\n"
                            "    uwsgi_temp_path /tmp/uwsgi_temp;\n"
                            "    scgi_temp_path /tmp/scgi_temp;\n"
                            "}\n"
                        ),
                        "description": "Configure nginx for port 8080 and writable temp paths",
                    },
                    {
                        "path": "docker-compose.yml",
                        "action": "create",
                        "content": (
                            "services:\n"
                            "  web:\n"
                            "    build: .\n"
                            "    ports:\n"
                            "      - \"8080:8080\"\n"
                            "    deploy:\n"
                            "      resources:\n"
                            "        limits:\n"
                            "          cpus: \"1.0\"\n"
                            "          memory: \"1G\"\n"
                        ),
                        "description": "Compose with resource limits",
                    },
                ],
                "manual_items": [],
                "summary": "Created validated static-site conversion",
            }),
        ])

        monkeypatch.setattr(
            "kamiwaza_extensions.convert_agent.call_llm",
            lambda prompt: next(responses, None),
        )

        plan = run_agent(analysis, dry_run=False)

        assert plan.success is True
        assert (tmp_path / "kamiwaza.json").exists()
        assert (tmp_path / "docker-compose.yml").exists()
        assert (tmp_path / "Dockerfile").exists()
        assert (tmp_path / "nginx.conf").exists()
        assert (tmp_path / "CONVERT_NOTES.md").exists()
        assert "limits" in (tmp_path / "docker-compose.yml").read_text()
        assert "nginxinc/nginx-unprivileged" in (tmp_path / "Dockerfile").read_text()
        assert "listen 8080;" in (tmp_path / "nginx.conf").read_text()

    def test_failed_validation_returns_errors_without_writing(self, monkeypatch, tmp_path):
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import run_agent

        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        analysis = AnalysisResult(
            app_dir=tmp_path,
            app_name="demo",
            extension_type="app",
            conversion_mode="generic",
            file_contents={"index.html": "<html><body>Hello</body></html>"},
        )

        responses = iter([
            json.dumps({
                "extension_type": "app",
                "conversion_mode": "containerize_repo_root",
                "primary_service": "web",
                "required_files": ["docker-compose.yml"],
                "runtime_summary": "Static HTML site",
                "manual_items": [],
            }),
            json.dumps({
                "modifications": [],
                "manual_items": [],
                "summary": "No-op response",
            }),
            json.dumps({
                "modifications": [],
                "manual_items": [],
                "summary": "No-op response",
            }),
            json.dumps({
                "modifications": [],
                "manual_items": [],
                "summary": "No-op response",
            }),
        ])

        monkeypatch.setattr(
            "kamiwaza_extensions.convert_agent.call_llm",
            lambda prompt: next(responses, None),
        )

        plan = run_agent(analysis, dry_run=False)

        assert plan.success is False
        assert plan.errors
        assert not (tmp_path / "kamiwaza.json").exists()
        assert not (tmp_path / "CONVERT_NOTES.md").exists()


class TestCallLLMProviderSelection:
    """``call_llm`` should honor KZ_CONVERT_PROVIDER and the auto-mode order."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for var in (
            "KZ_CONVERT_PROVIDER",
            "KZ_CONVERT_MODEL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

    @staticmethod
    def _completed(stdout="", stderr="", returncode=0):
        from subprocess import CompletedProcess

        return CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)

    def test_returns_none_when_no_provider_available(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(convert_agent.shutil, "which", lambda _name: None)

        assert convert_agent.call_llm("hi") is None

    def test_auto_prefers_claude_cli_when_available(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        which_calls = []
        run_calls = []

        def fake_which(name):
            which_calls.append(name)
            return "/usr/local/bin/claude" if name == "claude" else None

        def fake_run(cmd, **kwargs):
            run_calls.append((cmd, kwargs))
            return self._completed(stdout="claude-response\n")

        monkeypatch.setattr(convert_agent.shutil, "which", fake_which)
        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        result = convert_agent.call_llm("prompt-text")

        assert result == "claude-response"
        assert "claude" in which_calls
        # Claude won — codex was never probed.
        assert "codex" not in which_calls
        assert run_calls[0][0][0] == "/usr/local/bin/claude"
        assert "--print" in run_calls[0][0]
        # Prompt is piped via stdin, not the argv.
        assert run_calls[0][1]["input"] == "prompt-text"
        # Subprocess runs in an isolated cwd to avoid pulling in CLAUDE.md
        # / AGENTS.md from the user's repo.
        assert run_calls[0][1]["cwd"]

    def test_auto_falls_through_to_codex_when_claude_missing(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        def fake_which(name):
            return "/usr/local/bin/codex" if name == "codex" else None

        captured: list = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return self._completed(stdout="codex-response")

        monkeypatch.setattr(convert_agent.shutil, "which", fake_which)
        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        assert convert_agent.call_llm("p") == "codex-response"
        assert captured[0][:2] == ["/usr/local/bin/codex", "exec"]
        # Tmp cwd is not a git repo, so codex needs the override flag.
        assert "--skip-git-repo-check" in captured[0]
        assert captured[0][-1] == "-"

    def test_auto_falls_through_to_openai_when_clis_unavailable(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(convert_agent.shutil, "which", lambda _name: None)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        called = {}

        def fake_openai(prompt, **kwargs):
            called["prompt"] = prompt
            called["kwargs"] = kwargs
            return "openai-response"

        monkeypatch.setattr(convert_agent, "_call_openai_compatible", fake_openai)
        # Pretend openai package is installed.
        monkeypatch.setattr(
            convert_agent.importlib.util, "find_spec", lambda _name: object()
        )

        assert convert_agent.call_llm("p") == "openai-response"
        assert called["kwargs"]["api_key"] == "sk-test"

    def test_kz_convert_provider_anthropic_skips_clis(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        which_calls = []
        monkeypatch.setattr(
            convert_agent.shutil,
            "which",
            lambda name: which_calls.append(name) or "/bin/" + name,
        )
        monkeypatch.setenv("KZ_CONVERT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
        monkeypatch.setattr(
            convert_agent.importlib.util, "find_spec", lambda _name: object()
        )
        monkeypatch.setattr(
            convert_agent, "_call_anthropic", lambda prompt, **kw: "anthropic-response"
        )

        assert convert_agent.call_llm("p") == "anthropic-response"
        # CLIs should not have been probed at all under anthropic-only mode.
        assert "claude" not in which_calls
        assert "codex" not in which_calls

    def test_kz_convert_provider_claude_cli_warns_when_missing(self, monkeypatch, capsys):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(convert_agent.shutil, "which", lambda _n: None)
        monkeypatch.setenv("KZ_CONVERT_PROVIDER", "claude-cli")

        assert convert_agent.call_llm("p") is None
        # Warning lands on stderr via Rich Console — capture across both.
        captured = capsys.readouterr()
        assert "claude" in (captured.out + captured.err).lower()

    def test_unknown_provider_falls_back_to_auto(self, monkeypatch, capsys):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(convert_agent.shutil, "which", lambda _n: None)
        monkeypatch.setenv("KZ_CONVERT_PROVIDER", "bogus")

        assert convert_agent.call_llm("p") is None
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "bogus" in combined.lower() or "unknown" in combined.lower()

    def test_cli_nonzero_exit_falls_through(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        # Both CLIs present, but claude exits 1 — should fall through to codex.
        monkeypatch.setattr(
            convert_agent.shutil, "which", lambda name: "/bin/" + name
        )

        run_calls = []

        def fake_run(cmd, **kwargs):
            run_calls.append(cmd[0])
            if cmd[0].endswith("claude"):
                return self._completed(returncode=1, stderr="not authenticated")
            return self._completed(stdout="codex-response")

        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        assert convert_agent.call_llm("p") == "codex-response"
        assert run_calls[0].endswith("claude")
        assert run_calls[1].endswith("codex")

    def test_cli_timeout_falls_through(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(
            convert_agent.shutil, "which", lambda name: "/bin/" + name
        )

        def fake_run(cmd, **kwargs):
            if cmd[0].endswith("claude"):
                from subprocess import TimeoutExpired

                raise TimeoutExpired(cmd, timeout=120)
            return self._completed(stdout="codex-response")

        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        assert convert_agent.call_llm("p") == "codex-response"

    def test_cli_empty_output_falls_through(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(
            convert_agent.shutil, "which", lambda name: "/bin/" + name
        )

        def fake_run(cmd, **kwargs):
            if cmd[0].endswith("claude"):
                return self._completed(stdout="   \n")
            return self._completed(stdout="codex-response")

        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        assert convert_agent.call_llm("p") == "codex-response"

    def test_claude_cli_writes_guidance_into_temp_cwd(self, monkeypatch, tmp_path):
        """claude CLI should see CLAUDE.md (and AGENTS.md) with Kamiwaza rules."""
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(
            convert_agent.shutil, "which", lambda name: "/bin/claude" if name == "claude" else None
        )

        captured = {}

        def fake_run(cmd, **kwargs):
            cwd = Path(kwargs["cwd"])
            captured["claude_md"] = (cwd / "CLAUDE.md").read_text(encoding="utf-8")
            captured["agents_md"] = (cwd / "AGENTS.md").read_text(encoding="utf-8")
            return self._completed(stdout="ok")

        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        convert_agent.call_llm("p")

        assert "Kamiwaza Extension Authoring Guidance" in captured["claude_md"]
        assert captured["claude_md"] == captured["agents_md"]

    def test_codex_cli_writes_guidance_into_temp_cwd(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(
            convert_agent.shutil, "which", lambda name: "/bin/codex" if name == "codex" else None
        )

        captured = {}

        def fake_run(cmd, **kwargs):
            cwd = Path(kwargs["cwd"])
            captured["agents_md"] = (cwd / "AGENTS.md").read_text(encoding="utf-8")
            return self._completed(stdout="ok")

        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        convert_agent.call_llm("p")

        assert "Kamiwaza Extension Authoring Guidance" in captured["agents_md"]

    def test_kz_convert_model_passes_through_to_claude(self, monkeypatch):
        from kamiwaza_extensions import convert_agent

        monkeypatch.setattr(
            convert_agent.shutil, "which", lambda name: "/bin/claude" if name == "claude" else None
        )
        monkeypatch.setenv("KZ_CONVERT_MODEL", "opus")

        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return self._completed(stdout="response")

        monkeypatch.setattr(convert_agent.subprocess, "run", fake_run)

        convert_agent.call_llm("p")

        assert "--model" in captured[0]
        assert "opus" in captured[0]
