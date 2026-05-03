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
        (tmp_path / ".envrc").write_text("export API_KEY=secret\n")
        (tmp_path / "credentials.json").write_text("{\"token\": \"secret\"}\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert "index.html" in result.file_contents
        assert ".env" not in result.file_contents
        assert ".envrc" not in result.file_contents
        assert "credentials.json" not in result.file_contents

    def test_excludes_common_secret_bearing_files_from_repo_inventory(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        (tmp_path / ".env").write_text("API_KEY=secret\n")
        (tmp_path / ".envrc").write_text("export API_KEY=secret\n")
        (tmp_path / "credentials.json").write_text("{\"token\": \"secret\"}\n")
        (tmp_path / "id_rsa").write_text("private-key\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert "index.html" in result.repo_tree
        assert ".env" not in result.repo_tree
        assert ".envrc" not in result.repo_tree
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


class TestMonorepoDetection:
    """Test that ``analyze()`` rebases to monorepo subdirectories with compose."""

    @staticmethod
    def _write_compose(directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )

    @pytest.mark.parametrize(
        "subdir",
        [
            "apps/skills-library",
            "tools/my-tool",
            "services/api",
            "packages/my-pkg",
            "extensions/my-ext",
            "app",
            "extension",
        ],
    )
    def test_rebases_to_known_monorepo_subdir(self, tmp_path, subdir):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        target = tmp_path / subdir
        self._write_compose(target)

        result = AppAnalyzer().analyze(tmp_path)

        assert result.app_dir == target.resolve()
        assert result.rebased_from == tmp_path.resolve()
        assert result.app_name == AppAnalyzer._sanitize_name(target.name)
        assert result.compose_path is not None

    def test_no_rebase_when_compose_at_root(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )
        # A subdir compose should not trigger a rebase if root has its own.
        self._write_compose(tmp_path / "apps" / "decoy")

        result = AppAnalyzer().analyze(tmp_path)

        assert result.app_dir == tmp_path.resolve()
        assert result.rebased_from is None

    def test_no_rebase_when_no_compose_anywhere(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "index.html").write_text("<html></html>")

        result = AppAnalyzer().analyze(tmp_path)

        assert result.app_dir == tmp_path.resolve()
        assert result.rebased_from is None
        assert result.compose_path is None

    def test_raises_ambiguous_when_multiple_subdirs_match(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import (
            AmbiguousMonorepoError,
            AppAnalyzer,
        )

        self._write_compose(tmp_path / "apps" / "foo")
        self._write_compose(tmp_path / "apps" / "bar")

        with pytest.raises(AmbiguousMonorepoError) as exc_info:
            AppAnalyzer().analyze(tmp_path)

        candidate_names = sorted(p.name for p in exc_info.value.candidates)
        assert candidate_names == ["bar", "foo"]

    def test_raises_ambiguous_across_apps_and_tools(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import (
            AmbiguousMonorepoError,
            AppAnalyzer,
        )

        self._write_compose(tmp_path / "apps" / "foo")
        self._write_compose(tmp_path / "tools" / "bar")

        with pytest.raises(AmbiguousMonorepoError):
            AppAnalyzer().analyze(tmp_path)


class TestDockerfileDetection:
    """Regression guards for ``_is_dockerfile()``."""

    def test_excludes_template_suffix(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import _is_dockerfile

        assert not _is_dockerfile(tmp_path / "Dockerfile.python.template")
        assert not _is_dockerfile(tmp_path / "Dockerfile.nodejs.template")

    def test_excludes_backup_and_example_suffixes(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import _is_dockerfile

        assert not _is_dockerfile(tmp_path / "Dockerfile.bak")
        assert not _is_dockerfile(tmp_path / "Dockerfile.example")
        assert not _is_dockerfile(tmp_path / "Dockerfile.sample")
        assert not _is_dockerfile(tmp_path / "Dockerfile.tmpl")
        assert not _is_dockerfile(tmp_path / "Dockerfile.tpl")

    def test_buildkit_stage_variants_still_match(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import _is_dockerfile

        assert _is_dockerfile(tmp_path / "Dockerfile")
        assert _is_dockerfile(tmp_path / "Dockerfile.dev")
        assert _is_dockerfile(tmp_path / "Dockerfile.prod")

    def test_template_dockerfile_not_treated_as_service(self, tmp_path):
        """End-to-end: template Dockerfiles in tooling dirs don't become services."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        # Real service Dockerfile
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "Dockerfile").write_text("FROM python:3.11\n")
        # Template Dockerfile in a (non-skipped) tooling-shaped path
        templates_dir = tmp_path / "scripts" / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "Dockerfile.python.template").write_text("FROM python:3.11\n")

        result = AppAnalyzer().analyze(tmp_path)

        service_names = [s.name for s in result.services]
        assert "backend" in service_names
        assert "templates" not in service_names


class TestSkipDirs:
    """``.agents`` / ``.claude`` / IDE config dirs must not be inventoried."""

    def test_skips_agents_and_claude_dirs(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        # Real app file at root
        (tmp_path / "index.html").write_text("<html></html>")

        # Skill template files that previously polluted analysis
        for tooling_dir in (".agents", ".claude", ".cursor"):
            templates = tmp_path / tooling_dir / "skills" / "kz" / "templates"
            templates.mkdir(parents=True)
            (templates / "Dockerfile.python.template").write_text("FROM python\n")
            (templates / "package.json").write_text("{}")

        result = AppAnalyzer().analyze(tmp_path)

        assert "index.html" in result.repo_tree
        for tooling_dir in (".agents/", ".claude/", ".cursor/"):
            assert tooling_dir not in result.repo_tree
        # Manifests / entrypoints under those dirs should also be excluded.
        joined_manifests = " ".join(result.detected_manifests)
        assert ".agents" not in joined_manifests
        assert ".claude" not in joined_manifests
        assert ".cursor" not in joined_manifests


class TestGreenfieldMonorepoRebase:
    """Codex re-review finding: rebase must work for greenfield monorepos
    where the subdir doesn't have a compose file yet (the conversion is
    going to *generate* one). Detect via any extension-signal file —
    Dockerfile, kamiwaza.json, package.json, pyproject.toml, etc."""

    def test_rebases_when_subdir_has_dockerfile_only(self, tmp_path):
        """No compose anywhere; subdir has Dockerfile only. Rebase
        should still happen — the convert is about to *generate*
        compose, but the right place to put it is the subdir."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "apps" / "my-ext"
        ext.mkdir(parents=True)
        (ext / "Dockerfile").write_text("FROM python:3.11\n")
        # NO compose file anywhere

        result = AppAnalyzer().analyze(tmp_path)

        # Without the fix, app_dir stays at tmp_path (the monorepo root)
        # and convert would write kamiwaza.json/compose at the wrong place.
        assert result.app_dir == ext.resolve()
        assert result.rebased_from == tmp_path.resolve()

    def test_rebases_when_subdir_has_kamiwaza_json_only(self, tmp_path):
        """Re-conversion case: subdir already has a partial extension
        (kamiwaza.json) but is missing compose. Should rebase."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "apps" / "ext"
        ext.mkdir(parents=True)
        (ext / "kamiwaza.json").write_text('{"name": "ext"}')

        result = AppAnalyzer().analyze(tmp_path)
        assert result.app_dir == ext.resolve()
        assert result.rebased_from == tmp_path.resolve()

    def test_rebases_when_subdir_has_pyproject_toml(self, tmp_path):
        """Greenfield Python app: pyproject.toml present, nothing else."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "tools" / "py-tool"
        ext.mkdir(parents=True)
        (ext / "pyproject.toml").write_text('[project]\nname = "py-tool"\n')

        result = AppAnalyzer().analyze(tmp_path)
        assert result.app_dir == ext.resolve()
        assert result.rebased_from == tmp_path.resolve()

    def test_rebases_when_subdir_has_package_json(self, tmp_path):
        """Greenfield Node app: package.json present, nothing else."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "apps" / "node-app"
        ext.mkdir(parents=True)
        (ext / "package.json").write_text('{"name": "node-app"}')

        result = AppAnalyzer().analyze(tmp_path)
        assert result.app_dir == ext.resolve()
        assert result.rebased_from == tmp_path.resolve()

    def test_no_rebase_when_subdirs_lack_extension_signals(self, tmp_path):
        """An apps/ directory with only random docs / .gitkeep should
        NOT trigger a rebase — it's not actually an extension."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        random = tmp_path / "apps" / "random"
        random.mkdir(parents=True)
        (random / "README.md").write_text("# random notes")
        (random / ".gitkeep").write_text("")

        result = AppAnalyzer().analyze(tmp_path)
        # No extension signal — stay at the monorepo root.
        assert result.app_dir == tmp_path.resolve()
        assert result.rebased_from is None

    def test_ambiguous_when_multiple_subdirs_have_signals(self, tmp_path):
        """Two greenfield apps under apps/ — must error rather than
        silently picking one."""
        from kamiwaza_extensions.app_analyzer import (
            AmbiguousMonorepoError,
            AppAnalyzer,
        )

        for name in ("foo", "bar"):
            d = tmp_path / "apps" / name
            d.mkdir(parents=True)
            (d / "Dockerfile").write_text("FROM scratch\n")

        with pytest.raises(AmbiguousMonorepoError) as exc:
            AppAnalyzer().analyze(tmp_path)
        assert len(exc.value.candidates) == 2

    def test_workspace_root_with_package_json_still_rebases(self, tmp_path):
        """Codex iter-2 finding: an npm-workspaces monorepo has a
        ``package.json`` at the repo root (declaring ``workspaces:
        ['apps/*']``) AND a real extension at ``apps/web/Dockerfile``.
        The earlier broadened-signal commit treated the root
        ``package.json`` as a strong signal and short-circuited rebase
        — silently writing convert artifacts into the monorepo root
        instead of ``apps/web/``. The split signal sets fix this:
        ``package.json`` only counts for *subdir* discovery, not for
        the early-return."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        # Workspace-root manifest at the repo root (npm workspaces).
        (tmp_path / "package.json").write_text(
            '{"name": "monorepo", "workspaces": ["apps/*"]}'
        )
        # Real extension lives one level deep.
        ext = tmp_path / "apps" / "web"
        ext.mkdir(parents=True)
        (ext / "Dockerfile").write_text("FROM node:22\n")

        result = AppAnalyzer().analyze(tmp_path)

        # MUST rebase to apps/web/ — the root package.json is a
        # workspace marker, not a strong extension signal.
        assert result.app_dir == ext.resolve()
        assert result.rebased_from == tmp_path.resolve()

    def test_workspace_root_with_pyproject_toml_still_rebases(self, tmp_path):
        """Same regression for poetry/uv workspaces with a root
        ``pyproject.toml`` plus extensions under ``apps/<x>/``."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'monorepo'\n[tool.uv.workspace]\nmembers = ['apps/*']\n"
        )
        ext = tmp_path / "apps" / "api"
        ext.mkdir(parents=True)
        (ext / "Dockerfile").write_text("FROM python:3.11\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert result.app_dir == ext.resolve()
        assert result.rebased_from == tmp_path.resolve()

    def test_root_with_compose_still_short_circuits(self, tmp_path):
        """Confirm the early-return still fires for STRONG signals at
        the supplied path: a root ``docker-compose.yml`` means the
        user pointed directly at an extension, not a monorepo root.
        Should NOT rebase even if there's a coincidental signal under
        ``apps/<x>/``."""
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        # User points directly at a valid extension (compose present).
        (tmp_path / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )
        # A coincidental subdir that would otherwise look like a
        # candidate (e.g. examples/, archived apps/) should not steal
        # the rebase.
        (tmp_path / "apps" / "decoy").mkdir(parents=True)
        (tmp_path / "apps" / "decoy" / "Dockerfile").write_text("FROM scratch\n")

        result = AppAnalyzer().analyze(tmp_path)

        assert result.app_dir == tmp_path.resolve()
        assert result.rebased_from is None


class TestMonorepoInventory:
    """When rebased, the analyzer should surface the broader source tree."""

    def test_collects_files_outside_extension_root(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "apps" / "my-ext"
        ext.mkdir(parents=True)
        (ext / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )

        # Files outside the rebased extension root should be inventoried.
        (tmp_path / "shared").mkdir()
        (tmp_path / "shared" / "lib.py").write_text("def x(): pass")
        (tmp_path / "shared" / "auth-1.0.whl").write_bytes(b"\x00\x01\x02")

        result = AppAnalyzer().analyze(tmp_path)

        assert "shared/lib.py" in result.monorepo_inventory
        assert "shared/auth-1.0.whl" in result.monorepo_inventory
        assert "shared/auth-1.0.whl" in result.vendorable_artifacts

    def test_excludes_files_inside_extension_root(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "apps" / "my-ext"
        ext.mkdir(parents=True)
        (ext / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )
        (ext / "main.py").write_text("print('hi')")

        result = AppAnalyzer().analyze(tmp_path)

        assert "apps/my-ext/main.py" not in result.monorepo_inventory

    def test_empty_when_not_rebased(self, tmp_path):
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        (tmp_path / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )
        (tmp_path / "shared").mkdir()
        (tmp_path / "shared" / "lib.py").write_text("def x(): pass")

        result = AppAnalyzer().analyze(tmp_path)

        assert result.rebased_from is None
        assert result.monorepo_inventory == []
        assert result.vendorable_artifacts == []

    def test_surfaces_artifacts_in_dist_directory(self, tmp_path):
        """Codex re-review finding: shared publish artifacts (.whl,
        .tgz) typically live in dist/ — but the general _walk_files
        prunes dist/build/target. The monorepo-inventory pass must
        use a smaller skip list so these artifacts surface to the LLM
        for the copy action.
        """
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "apps" / "ext"
        ext.mkdir(parents=True)
        (ext / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )

        # The exact layout that broke for the user: shared wheels in
        # shared/python/dist/. Earlier code pruned dist/ and these
        # never reached vendorable_artifacts.
        wheel_dir = tmp_path / "shared" / "python" / "dist"
        wheel_dir.mkdir(parents=True)
        wheel = wheel_dir / "auth-1.0.whl"
        wheel.write_bytes(b"\x00\x01\x02")
        # Same for typescript tarballs (build/ is also in default skip list).
        tgz_dir = tmp_path / "shared" / "typescript" / "build"
        tgz_dir.mkdir(parents=True)
        tgz = tgz_dir / "auth-1.0.tgz"
        tgz.write_bytes(b"\x00\x01\x02")

        result = AppAnalyzer().analyze(tmp_path)

        assert "shared/python/dist/auth-1.0.whl" in result.vendorable_artifacts, (
            "Wheels in shared/python/dist/ must surface — they're "
            "the primary inputs the copy action is meant to vendor"
        )
        assert "shared/typescript/build/auth-1.0.tgz" in result.vendorable_artifacts

    def test_artifacts_surface_even_past_inventory_cap(self, tmp_path, monkeypatch):
        """Codex iter-2 finding: the 200-entry inventory cap was
        sharing a loop with vendorable artifacts. If the only .whl in
        the tree was discovered AFTER the cap, the LLM never saw it
        and couldn't emit a copy action.

        Decoupled artifact scanning means artifacts are recorded
        unconditionally even when inventory truncation kicks in.
        """
        from kamiwaza_extensions import app_analyzer
        from kamiwaza_extensions.app_analyzer import AppAnalyzer

        ext = tmp_path / "apps" / "ext"
        ext.mkdir(parents=True)
        (ext / "docker-compose.yml").write_text(
            yaml.dump({"services": {"app": {"image": "x"}}})
        )

        # Force the cap to a small number so the test is fast.
        monkeypatch.setattr(app_analyzer, "_MAX_MONOREPO_INVENTORY_ENTRIES", 5)

        # 10 ordinary files in shared/ — guaranteed to fill the
        # 5-entry cap before the .whl appears (which we put in a
        # later-sorted directory).
        shared = tmp_path / "shared"
        shared.mkdir()
        for i in range(10):
            (shared / f"a-doc-{i:02d}.md").write_text(f"# doc {i}")
        # Wheel in z-late-dir/ so os.walk hits it after the docs.
        late = tmp_path / "z-late-dir"
        late.mkdir()
        wheel = late / "needed.whl"
        wheel.write_bytes(b"x")

        result = AppAnalyzer().analyze(tmp_path)

        # Inventory was capped (only 5 entries).
        assert len(result.monorepo_inventory) == 5
        # But the wheel still surfaces — that's the whole point.
        assert "z-late-dir/needed.whl" in result.vendorable_artifacts, (
            "Vendorable artifacts must be discovered unconditionally — "
            "the inventory cap should not silently hide late-walked "
            "wheels/tarballs that the LLM needs for the copy action"
        )


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
