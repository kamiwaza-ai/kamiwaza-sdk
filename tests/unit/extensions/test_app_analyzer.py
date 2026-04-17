"""Unit tests for AppAnalyzer."""

import json

import pytest
import yaml

pytestmark = pytest.mark.unit


class TestAnalyze:
    """Test the full analysis pipeline."""

    def test_analyze_basic_app(self, tmp_path):
        """Analyze a minimal app with compose and Dockerfiles."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        # Create compose
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "./backend", "dockerfile": "Dockerfile"},
                    "ports": ["8000:8000"],
                    "volumes": ["./backend/app:/app/app"],
                },
                "frontend": {
                    "build": "./frontend",
                    "ports": ["3000:3000"],
                },
            }
        }
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))

        # Create Dockerfiles
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "Dockerfile").write_text("FROM python:3.11\n")
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "Dockerfile").write_text("FROM node:20\n")

        analyzer = AppAnalyzer()
        result = analyzer.analyze(tmp_path)

        # tmp_path names have underscores which get sanitized to hyphens
        import re
        expected = re.sub(r"[^a-z0-9-]", "-", tmp_path.name.lower()).strip("-")
        expected = re.sub(r"-+", "-", expected)
        assert result.app_name == expected
        assert len(result.services) == 2
        assert result.compose_path is not None

        backend = next(s for s in result.services if s.name == "backend")
        assert backend.language == "python"
        assert 8000 in backend.ports

        frontend = next(s for s in result.services if s.name == "frontend")
        assert frontend.language == "node"

    def test_detect_host_ports(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        compose = {"services": {"app": {"ports": ["8080:8000"]}}}
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))

        result = AppAnalyzer().analyze(tmp_path)
        assert len(result.has_host_ports) == 1
        assert "8080:8000" in result.has_host_ports[0]

    def test_detect_bind_mounts(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        compose = {"services": {"app": {"volumes": ["./src:/app/src"]}}}
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))

        result = AppAnalyzer().analyze(tmp_path)
        assert len(result.has_bind_mounts) == 1

    def test_detect_missing_resource_limits(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        compose = {"services": {"app": {"image": "myapp:latest"}}}
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))

        result = AppAnalyzer().analyze(tmp_path)
        assert "app" in result.missing_resource_limits

    def test_detect_health_endpoint(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(yaml.dump({"services": {"app": {}}}))
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text(
            '@app.get("/health")\nasync def health():\n    return {"status": "ok"}\n'
        )

        result = AppAnalyzer().analyze(tmp_path)
        assert result.has_health_endpoint is True

    def test_no_health_endpoint(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(yaml.dump({"services": {"app": {}}}))

        result = AppAnalyzer().analyze(tmp_path)
        assert result.has_health_endpoint is False

    def test_detect_python_runtime_lib(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(yaml.dump({"services": {"app": {}}}))
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "requirements.txt").write_text(
            "fastapi\nkamiwaza-extensions-lib>=0.1.0\n"
        )

        result = AppAnalyzer().analyze(tmp_path)
        assert result.has_python_runtime_lib is True

    def test_detect_ts_runtime_lib(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(yaml.dump({"services": {"app": {}}}))
        (tmp_path / "frontend").mkdir()
        pkg = {"dependencies": {"@kamiwaza-ai/extensions-lib": "^0.2.0"}}
        (tmp_path / "frontend" / "package.json").write_text(json.dumps(pkg))

        result = AppAnalyzer().analyze(tmp_path)
        assert result.has_ts_runtime_lib is True

    def test_infer_tool_type_from_name(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        tool_dir = tmp_path / "tool-webscraper"
        tool_dir.mkdir()
        (tool_dir / "docker-compose.yml").write_text(yaml.dump({"services": {"app": {}}}))

        result = AppAnalyzer().analyze(tool_dir)
        assert result.extension_type == "tool"

    def test_infer_description_from_readme(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(yaml.dump({"services": {"app": {}}}))
        (tmp_path / "README.md").write_text("# My Cool App\nThis is a cool app.\n")

        result = AppAnalyzer().analyze(tmp_path)
        assert result.description == "My Cool App"

    def test_nonexistent_directory(self):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        with pytest.raises(FileNotFoundError):
            AppAnalyzer().analyze("/nonexistent/path")

    def test_no_compose_file(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        result = AppAnalyzer().analyze(tmp_path)
        assert result.compose_path is None
        assert result.compose_data is None

    def test_collects_html_context_for_generic_repo(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        (tmp_path / "styles.css").write_text("body { color: red; }")

        result = AppAnalyzer().analyze(tmp_path)

        assert result.conversion_mode == "generic"
        assert "index.html" in result.file_contents
        assert "static-html" in result.runtime_hints
        assert "index.html" in result.candidate_entrypoints

    def test_collects_root_package_json_for_generic_repo(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        pkg = {"name": "demo", "scripts": {"start": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("console.log('hi');")

        result = AppAnalyzer().analyze(tmp_path)

        assert result.conversion_mode == "generic"
        assert "package.json" in result.file_contents
        assert "package.json" in result.detected_manifests
        assert "node-package" in result.runtime_hints

    def test_excludes_common_secret_bearing_files_from_context(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        (tmp_path / ".env").write_text("API_KEY=secret\n")
        (tmp_path / "credentials.json").write_text("{\"token\": \"secret\"}\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert "index.html" in result.file_contents
        assert ".env" not in result.file_contents
        assert "credentials.json" not in result.file_contents

    def test_excludes_common_secret_bearing_files_from_repo_inventory(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        (tmp_path / ".env").write_text("API_KEY=secret\n")
        (tmp_path / "credentials.json").write_text("{\"token\": \"secret\"}\n")
        (tmp_path / "id_rsa").write_text("private-key\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert "index.html" in result.repo_tree
        assert ".env" not in result.repo_tree
        assert "credentials.json" not in result.repo_tree
        assert "id_rsa" not in result.repo_tree

    def test_keeps_legit_source_with_secret_like_names(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "secret_manager.py").write_text("def load_secret(): pass\n")
        (tmp_path / "credential_store.py").write_text("def load_credentials(): pass\n")
        (tmp_path / "secrets_test.py").write_text("def test_secret(): pass\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert "secret_manager.py" in result.file_contents
        assert "credential_store.py" in result.file_contents
        assert "secrets_test.py" in result.file_contents

    def test_detects_additional_language_manifests(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "go.mod").write_text("module demo\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert "go.mod" in result.detected_manifests


class TestGenerateKamiwazaJson:
    def test_basic_generation(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(yaml.dump({"services": {"app": {}}}))

        analyzer = AppAnalyzer()
        result = analyzer.analyze(tmp_path)
        kamiwaza = analyzer.generate_kamiwaza_json(result)

        assert kamiwaza["name"] == result.app_name
        assert kamiwaza["version"] == "0.1.0"
        assert kamiwaza["source_type"] == "user_repo"
        assert kamiwaza["risk_tier"] == 0
        assert kamiwaza["verified"] is False
        assert "kz_ext_version" in kamiwaza


class TestSanitizeName:
    def test_lowercase(self):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        assert AppAnalyzer._sanitize_name("MyApp") == "myapp"

    def test_special_chars(self):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        assert AppAnalyzer._sanitize_name("my_app@v2") == "my-app-v2"

    def test_empty(self):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        assert AppAnalyzer._sanitize_name("") == "my-extension"
