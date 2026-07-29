"""Tests for the runtime-lib publish gate.

The gate exists because bumping a runtime lib in-repo is not the same as
publishing it. A floor that runs ahead of the registry is invisible to anyone
developing with ``uv`` — the workspace source shadows the registry — but makes
the SDK unresolvable for external consumers and for CI lanes installing with
plain ``pip``/``npm``.

Registry access is stubbed throughout: the comparison logic is what needs
coverage, and a networked unit test would be both slow and flaky.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_runtime_lib_floors.py"
_MODULE_NAME = "check_runtime_lib_floors"


def _load_module():
    """Load the gate from ``scripts/``, which is not an importable package.

    The module is registered in ``sys.modules`` before execution because
    ``@dataclass`` resolves annotations through the module's own entry there.
    """
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()


class TestExtractFloor:
    """One extractor serves both ecosystems: PEP 440 and npm ranges."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("kamiwaza-extensions-lib>=0.4.4,<0.5", "0.4.4"),
            (">=0.4.3 <0.5", "0.4.3"),
            (">= 1.2.3", "1.2.3"),
            (">=v2.0.0", "2.0.0"),
            (">=1.0.0-rc.1", "1.0.0-rc.1"),
        ],
    )
    def test_extracts_lower_bound(self, spec, expected):
        assert gate.extract_floor(spec) == expected

    @pytest.mark.parametrize("spec", ["<0.5", "*", "", "^1.2.3"])
    def test_returns_none_without_lower_bound(self, spec):
        # An unbounded range cannot outrun the registry, so it is not a finding.
        assert gate.extract_floor(spec) is None


class TestCollectDeclarations:
    """Every floor a consumer resolves against a public registry is collected."""

    @pytest.fixture(scope="class")
    def declarations(self):
        return gate.collect_declarations(REPO_ROOT)

    def test_covers_both_ecosystems(self, declarations):
        assert {d.ecosystem for d in declarations} == {"pypi", "npm"}

    def test_includes_the_sdk_dependency_floor(self, declarations):
        sources = [d.source for d in declarations]
        assert any("[project].dependencies" in s for s in sources)

    def test_includes_both_compatibility_bundle_pins(self, declarations):
        sources = [d.source for d in declarations]
        assert any("runtime_lib_compat.python" in s for s in sources)
        assert any("runtime_lib_compat.typescript" in s for s in sources)

    def test_every_declaration_has_a_parseable_floor(self, declarations):
        assert declarations
        assert all(d.floor for d in declarations)


class TestFindViolations:
    @staticmethod
    def _resolver(version: str):
        return lambda ecosystem, package: version

    def test_floor_ahead_of_registry_is_a_violation(self):
        declaration = gate.Declaration("pypi", "demo", "0.4.4", "src")
        violations = gate.find_violations([declaration], self._resolver("0.4.2"))
        assert len(violations) == 1
        assert violations[0].published == "0.4.2"

    def test_floor_matching_registry_is_clean(self):
        declaration = gate.Declaration("pypi", "demo", "0.4.2", "src")
        assert gate.find_violations([declaration], self._resolver("0.4.2")) == []

    def test_floor_behind_registry_is_clean(self):
        declaration = gate.Declaration("pypi", "demo", "0.4.0", "src")
        assert gate.find_violations([declaration], self._resolver("0.4.2")) == []

    def test_unparseable_floor_is_not_reported_as_a_publish_gap(self):
        declaration = gate.Declaration("npm", "demo", "not-a-version", "src")
        assert gate.find_violations([declaration], self._resolver("0.4.2")) == []

    def test_reports_every_violating_declaration(self):
        declarations = [
            gate.Declaration("pypi", "demo", "0.4.4", "a"),
            gate.Declaration("npm", "demo", "0.4.3", "b"),
            gate.Declaration("pypi", "demo", "0.4.1", "c"),
        ]
        violations = gate.find_violations(declarations, self._resolver("0.4.2"))
        assert [v.declaration.source for v in violations] == ["a", "b"]


class TestRepoDeclarationsAgainstStubbedRegistry:
    """The end-to-end shape, wired to the repo's real manifests.

    Pinned to a synthetic registry version rather than the live one so the
    assertion stays true regardless of what has been published since.
    """

    def test_declared_floors_above_the_registry_are_caught(self):
        declarations = gate.collect_declarations(REPO_ROOT)
        violations = gate.find_violations(declarations, lambda e, p: "0.0.1")
        assert len(violations) == len(declarations)

    def test_declared_floors_below_the_registry_are_clean(self):
        declarations = gate.collect_declarations(REPO_ROOT)
        assert gate.find_violations(declarations, lambda e, p: "999.0.0") == []


class TestMainSkipsOnRegistryOutage:
    """A registry outage must not turn the repo red.

    Mirrors the npm-side version guard, which also passes on a registry
    hiccup; release.sh's preflight still hard-fails at publish time.
    """

    def test_returns_zero_when_the_registry_is_unreachable(self, monkeypatch, capsys):
        def _boom(ecosystem, package):
            raise TimeoutError("registry unreachable")

        monkeypatch.setattr(gate, "latest_published", _boom)
        assert gate.main(["--repo-root", str(REPO_ROOT)]) == 0
        assert "::warning::" in capsys.readouterr().out

    def test_returns_one_when_a_floor_is_ahead(self, monkeypatch, capsys):
        monkeypatch.setattr(gate, "latest_published", lambda e, p: "0.0.1")
        assert gate.main(["--repo-root", str(REPO_ROOT)]) == 1
        assert "::error::" in capsys.readouterr().out


class TestWarnOnlyMode:
    """Advisory mode for the staging window between a bump and its release.

    A floor ahead of the registry is a broken release on `main`/`release/**`,
    but on the integration branch it is the normal state while a bumped runtime
    lib waits to be published. Reporting it as a hard failure there would leave
    a required check red for the length of that window.
    """

    def test_reports_but_does_not_fail(self, monkeypatch, capsys):
        monkeypatch.setattr(gate, "latest_published", lambda e, p: "0.0.1")
        assert gate.main(["--repo-root", str(REPO_ROOT), "--warn-only"]) == 0

        out = capsys.readouterr().out
        # A warning, not an error — a green check must not carry error
        # annotations, or the run reads as failed in the Actions UI.
        assert "::warning::" in out
        assert "::error::" not in out

    def test_still_names_every_violating_floor(self, monkeypatch, capsys):
        monkeypatch.setattr(gate, "latest_published", lambda e, p: "0.0.1")
        gate.main(["--repo-root", str(REPO_ROOT), "--warn-only"])

        out = capsys.readouterr().out
        expected = len(gate.collect_declarations(REPO_ROOT))
        assert out.count("::warning::") == expected

    def test_is_silent_about_violations_when_there_are_none(self, monkeypatch, capsys):
        monkeypatch.setattr(gate, "latest_published", lambda e, p: "999.0.0")
        assert gate.main(["--repo-root", str(REPO_ROOT), "--warn-only"]) == 0

        out = capsys.readouterr().out
        assert "::warning::" not in out
        assert "OK:" in out


class TestBundleAndDependencyStayCoherent:
    """The two Python-side floors must not drift apart.

    They are rendered into different files for different consumers (the SDK's
    own install vs. a generated extension's requirements.txt), so nothing else
    forces them to agree.
    """

    def test_python_floors_match(self):
        declarations = gate.collect_declarations(REPO_ROOT)
        python_floors = {d.floor for d in declarations if d.ecosystem == "pypi"}
        assert len(python_floors) == 1

    def test_bundle_pins_the_packages_the_gate_checks(self):
        bundle = json.loads(
            (REPO_ROOT / "kamiwaza_extensions" / "compatibility.json").read_text()
        )
        compat = bundle["runtime_lib_compat"]
        assert gate.PYPI_LIB in compat["python"]
        assert gate.NPM_LIB in compat["typescript"]
