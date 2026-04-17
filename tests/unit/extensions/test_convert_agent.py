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
        assert "index.html" in prompt
        assert "static-html" in prompt
        assert "non-root" in prompt.lower()
        assert "8080" in prompt


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


class TestRunAgent:
    def test_fallback_without_llm(self, monkeypatch):
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import run_agent

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = AnalysisResult(
            app_dir=Path("/tmp/test"),
            app_name="test",
        )
        plan = run_agent(result)
        assert plan.success is False
        assert "unavailable" in plan.summary.lower()
        assert len(plan.manual_items) > 0

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
