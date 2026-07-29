"""Tests for the runtime-lib publish gate.

The gate exists because bumping a runtime lib in-repo is not the same as
publishing it. A floor that runs ahead of the registry is invisible to anyone
developing with ``uv`` — the workspace source shadows the registry — but makes
the SDK unresolvable for external consumers and for CI lanes installing with
plain ``pip``/``npm``.

The failure mode that matters for a guard rail is not "it breaks" but "it
reports green when it could not actually check". Several tests below exist
specifically to pin paths where an earlier revision did exactly that: a 404
misread as an outage, an npm-idiomatic ``^0.4.3`` silently dropping its
declaration, and an unparseable published version comparing clean.

Registry access is stubbed throughout: the comparison logic is what needs
coverage, and a networked unit test would be both slow and flaky.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

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
            # npm's idiomatic caret/tilde ranges are lower bounds. Reading them
            # as unbounded silently dropped the whole npm half of the gate.
            ("^0.4.3", "0.4.3"),
            ("~0.4.3", "0.4.3"),
            ("^1.2.3 <2", "1.2.3"),
            # PEP 440 pins and compatible-release clauses bound below too.
            ("==0.4.4", "0.4.4"),
            ("~=0.4.4", "0.4.4"),
            (">0.4.4", "0.4.4"),
            ("0.4.4", "0.4.4"),
        ],
    )
    def test_extracts_lower_bound(self, spec, expected):
        floor = gate.extract_floor(spec)
        assert floor is not None
        assert floor.version == expected

    def test_upper_bound_is_not_mistaken_for_a_floor(self):
        # `<0.5` must not read as a floor of 0.5 — the reason clauses are
        # scanned with their operators rather than searching for a version.
        assert gate.extract_floor("<0.5") is None

    @pytest.mark.parametrize("spec", ["<0.5", "<=1.0", "*", ""])
    def test_returns_none_when_genuinely_unbounded(self, spec):
        # An unbounded range cannot outrun the registry, so it is not a finding.
        assert gate.extract_floor(spec) is None

    def test_strictness_is_preserved(self):
        assert gate.extract_floor(">0.4.4").strict is True
        assert gate.extract_floor(">=0.4.4").strict is False

    def test_highest_lower_bound_wins(self):
        assert gate.extract_floor(">=0.4.0,>=0.4.9,<0.5").version == "0.4.9"


class TestCollectDeclarations:
    """Every floor a consumer resolves against a public registry is collected."""

    @pytest.fixture(scope="class")
    def declarations(self):
        return gate.collect_declarations(REPO_ROOT)

    def test_covers_both_ecosystems(self, declarations):
        assert {d.ecosystem for d in declarations} == {"pypi", "npm"}

    def test_includes_the_sdk_dependency_floor(self, declarations):
        assert any("[project].dependencies" in d.source for d in declarations)

    def test_includes_both_compatibility_bundle_pins(self, declarations):
        sources = [d.source for d in declarations]
        assert any("runtime_lib_compat.python" in s for s in sources)
        assert any("runtime_lib_compat.typescript" in s for s in sources)

    def test_includes_the_example_app_manifests(self, declarations):
        # Users copy these verbatim, so stale floors there mislead too.
        sources = [d.source for d in declarations]
        assert any("examples/chatbot-app/backend" in s for s in sources)
        assert any("examples/chatbot-app/frontend" in s for s in sources)

    def test_every_declaration_has_a_parseable_floor(self, declarations):
        assert declarations
        assert all(d.floor is not None for d in declarations)


class TestFindViolations:
    @staticmethod
    def _resolver(version: str):
        return lambda ecosystem, package: version

    @staticmethod
    def _declaration(floor_spec: str, source: str = "src"):
        return gate._declaration("pypi", "demo", floor_spec, source)

    def test_floor_ahead_of_registry_is_a_violation(self):
        violations, unchecked = gate.find_violations(
            [self._declaration(">=0.4.4")], self._resolver("0.4.2")
        )
        assert len(violations) == 1
        assert unchecked == []
        assert "0.4.2" in violations[0].detail

    def test_floor_matching_registry_is_clean(self):
        violations, _ = gate.find_violations(
            [self._declaration(">=0.4.2")], self._resolver("0.4.2")
        )
        assert violations == []

    def test_strict_floor_is_not_satisfied_by_an_equal_version(self):
        # `>0.4.2` genuinely cannot resolve against a newest of 0.4.2.
        violations, _ = gate.find_violations(
            [self._declaration(">0.4.2")], self._resolver("0.4.2")
        )
        assert len(violations) == 1

    def test_floor_behind_registry_is_clean(self):
        violations, _ = gate.find_violations(
            [self._declaration(">=0.4.0")], self._resolver("0.4.2")
        )
        assert violations == []

    def test_unparseable_published_version_is_reported_not_swallowed(self):
        violations, _ = gate.find_violations(
            [self._declaration(">=0.4.4")], self._resolver("not-a-version")
        )
        assert len(violations) == 1
        assert "unparseable" in violations[0].detail

    def test_missing_package_is_a_violation_not_an_outage(self):
        def _absent(ecosystem, package):
            raise gate.RegistryError("registry returned HTTP 404", hard=True)

        violations, unchecked = gate.find_violations(
            [self._declaration(">=0.4.4")], _absent
        )
        assert len(violations) == 1
        assert unchecked == []

    def test_unreachable_registry_is_unchecked_not_a_violation(self):
        def _down(ecosystem, package):
            raise gate.RegistryError("registry unreachable", hard=False)

        violations, unchecked = gate.find_violations(
            [self._declaration(">=0.4.4")], _down
        )
        assert violations == []
        assert len(unchecked) == 1

    def test_one_registry_failing_does_not_skip_the_other(self):
        def _only_npm_down(ecosystem, package):
            if ecosystem == "npm":
                raise gate.RegistryError("registry unreachable", hard=False)
            return "0.0.1"

        declarations = [
            gate._declaration("pypi", "demo", ">=0.4.4", "py"),
            gate._declaration("npm", "demo", ">=0.4.3", "ts"),
        ]
        violations, unchecked = gate.find_violations(declarations, _only_npm_down)
        assert [v.declaration.source for v in violations] == ["py"]
        assert [u.declaration.source for u in unchecked] == ["ts"]


class TestLatestPublished:
    """The registry payload shapes, pinned without network."""

    def test_pypi_uses_the_newest_unyanked_release(self, monkeypatch):
        payload = {
            "releases": {
                "0.4.0": [{"yanked": False}],
                "0.4.2": [{"yanked": False}],
                # A yanked newest would be refused by pip, so it must not count.
                "0.4.9": [{"yanked": True}],
            }
        }
        seen = {}

        def _fake(url):
            seen["url"] = url
            return payload

        monkeypatch.setattr(gate, "_fetch_json", _fake)
        assert gate.latest_published("pypi", "demo-pkg") == "0.4.2"
        assert seen["url"] == "https://pypi.org/pypi/demo-pkg/json"

    def test_pypi_ignores_releases_with_no_files(self, monkeypatch):
        monkeypatch.setattr(
            gate, "_fetch_json", lambda url: {"releases": {"0.4.2": [], "0.4.0": [{}]}}
        )
        assert gate.latest_published("pypi", "demo") == "0.4.0"

    def test_npm_uses_all_published_versions_not_just_the_latest_tag(self, monkeypatch):
        payload = {
            "dist-tags": {"latest": "0.4.0"},
            "versions": {"0.4.0": {}, "0.4.2": {}},
        }
        seen = {}

        def _fake(url):
            seen["url"] = url
            return payload

        monkeypatch.setattr(gate, "_fetch_json", _fake)
        # A version published under a non-latest tag is still resolvable by a
        # range, so dist-tags.latest would understate what exists.
        assert gate.latest_published("npm", "@scope/pkg") == "0.4.2"
        assert seen["url"] == "https://registry.npmjs.org/@scope/pkg"

    def test_semver_ordering_not_string_ordering(self, monkeypatch):
        monkeypatch.setattr(
            gate,
            "_fetch_json",
            lambda url: {"versions": {"0.4.9": {}, "0.4.10": {}}},
        )
        assert gate.latest_published("npm", "demo") == "0.4.10"

    def test_no_publishable_versions_is_a_hard_error(self, monkeypatch):
        monkeypatch.setattr(gate, "_fetch_json", lambda url: {"versions": {}})
        with pytest.raises(gate.RegistryError) as excinfo:
            gate.latest_published("npm", "demo")
        assert excinfo.value.hard is True


class TestFetchJsonErrorClassification:
    """A 404 is the registry answering, not the registry being down.

    ``HTTPError`` subclasses ``URLError``, so catching the latter first made a
    404 — the package does not exist under that name, precisely what this gate
    is for — pass silently as an outage.
    """

    @staticmethod
    def _raise(monkeypatch, exc):
        def _boom(url, timeout=None):
            raise exc

        monkeypatch.setattr(gate.urllib.request, "urlopen", _boom)

    def test_404_is_hard(self, monkeypatch):
        self._raise(
            monkeypatch,
            urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        )
        with pytest.raises(gate.RegistryError) as excinfo:
            gate._fetch_json("https://example.invalid")
        assert excinfo.value.hard is True

    def test_403_is_hard(self, monkeypatch):
        self._raise(monkeypatch, urllib.error.HTTPError("u", 403, "no", {}, None))
        with pytest.raises(gate.RegistryError) as excinfo:
            gate._fetch_json("https://example.invalid")
        assert excinfo.value.hard is True

    @pytest.mark.parametrize("code", [429, 500, 503])
    def test_rate_limit_and_server_errors_are_soft(self, monkeypatch, code):
        self._raise(monkeypatch, urllib.error.HTTPError("u", code, "x", {}, None))
        with pytest.raises(gate.RegistryError) as excinfo:
            gate._fetch_json("https://example.invalid")
        assert excinfo.value.hard is False

    def test_transport_failure_is_soft(self, monkeypatch):
        self._raise(monkeypatch, urllib.error.URLError("connection refused"))
        with pytest.raises(gate.RegistryError) as excinfo:
            gate._fetch_json("https://example.invalid")
        assert excinfo.value.hard is False


class TestRepoDeclarationsAgainstStubbedRegistry:
    """The end-to-end shape, wired to the repo's real manifests.

    Pinned to a synthetic registry version rather than the live one so the
    assertion stays true regardless of what has been published since.
    """

    def test_declared_floors_above_the_registry_are_caught(self):
        declarations = gate.collect_declarations(REPO_ROOT)
        violations, _ = gate.find_violations(declarations, lambda e, p: "0.0.1")
        assert len(violations) == len(declarations)

    def test_declared_floors_below_the_registry_are_clean(self):
        declarations = gate.collect_declarations(REPO_ROOT)
        violations, _ = gate.find_violations(declarations, lambda e, p: "999.0.0")
        assert violations == []


class TestMainNeverPassesSilently:
    def test_registry_outage_passes_but_says_so(self, monkeypatch, capsys):
        def _boom(ecosystem, package):
            raise gate.RegistryError("registry unreachable", hard=False)

        monkeypatch.setattr(gate, "latest_published", _boom)
        assert gate.main(["--repo-root", str(REPO_ROOT)]) == 0
        assert "unreachable" in capsys.readouterr().out

    def test_returns_one_when_a_floor_is_ahead(self, monkeypatch, capsys):
        monkeypatch.setattr(gate, "latest_published", lambda e, p: "0.0.1")
        assert gate.main(["--repo-root", str(REPO_ROOT)]) == 1
        assert "::error::" in capsys.readouterr().out

    def test_missing_package_fails_in_blocking_mode(self, monkeypatch, capsys):
        def _absent(ecosystem, package):
            raise gate.RegistryError("registry returned HTTP 404", hard=True)

        monkeypatch.setattr(gate, "latest_published", _absent)
        assert gate.main(["--repo-root", str(REPO_ROOT)]) == 1
        assert "::error::" in capsys.readouterr().out

    def test_every_declaration_is_accounted_for_on_a_clean_run(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(gate, "latest_published", lambda e, p: "999.0.0")
        assert gate.main(["--repo-root", str(REPO_ROOT)]) == 0

        out = capsys.readouterr().out
        expected = len(gate.collect_declarations(REPO_ROOT))
        assert out.count("OK:") + out.count("SKIP:") == expected


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
    """The Python-side floors must not drift apart.

    They are rendered into different files for different consumers (the SDK's
    own install vs. a generated extension's requirements.txt), so nothing else
    forces them to agree.
    """

    def test_python_floors_match(self):
        declarations = gate.collect_declarations(REPO_ROOT)
        floors = {d.floor.version for d in declarations if d.ecosystem == "pypi"}
        assert len(floors) == 1

    def test_bundle_pins_the_packages_the_gate_checks(self):
        bundle = json.loads(
            (REPO_ROOT / "kamiwaza_extensions" / "compatibility.json").read_text()
        )
        compat = bundle["runtime_lib_compat"]
        assert gate.PYPI_LIB in compat["python"]
        assert gate.NPM_LIB in compat["typescript"]
