"""Compatibility bundle tests.

Issue: ENG-3897 / D210 M2 / Task T2.18.
Scenarios: TS-M2-37 (bundled resource), TS-M2-38..40 (Doctor probes).

The CompatibilityBundle is a per-CLI-version map of supported runtime-lib
ranges (Python + TypeScript). The intent is to surface silent drift between
CLI and runtime libs early — at ``kz-ext doctor`` time, before deploy.

Out-of-range combinations **warn**, never **fail**, so an extension author
who pinned an older runtime lib gets a heads-up without their build
breaking. This is the design call from §4.2.10.
"""

from __future__ import annotations

import importlib.resources
import json

import pytest

from kamiwaza_extensions.doctor import DoctorChecker


# ---------------------------------------------------------------------------
# TS-M2-37: bundle is shipped with the package and is well-formed.
# ---------------------------------------------------------------------------

class TestCompatibilityBundleResource:
    """The bundle is imported as a package resource so it works whether the
    CLI is installed via wheel, sdist, or editable mode."""

    @pytest.fixture(scope="class")
    def bundle(self) -> dict:
        return json.loads(
            importlib.resources.files("kamiwaza_extensions")
            .joinpath("compatibility.json")
            .read_text()
        )

    def test_bundle_has_cli_version(self, bundle):
        assert "cli_version" in bundle
        assert isinstance(bundle["cli_version"], str)

    def test_bundle_has_runtime_lib_compat(self, bundle):
        compat = bundle["runtime_lib_compat"]
        assert "python" in compat
        assert "typescript" in compat
        assert "kamiwaza-extensions-lib" in compat["python"]
        assert "@kamiwaza-ai/extensions-lib" in compat["typescript"]

    def test_python_range_is_pep440_specifier(self, bundle):
        from packaging.specifiers import SpecifierSet

        # Must parse cleanly — invalid specifier here would break Doctor.
        SpecifierSet(bundle["runtime_lib_compat"]["python"]["kamiwaza-extensions-lib"])

    def test_typescript_range_is_npm_semver(self, bundle):
        # Loose check — npm semver is too permissive to validate strictly here,
        # but it must at least be a non-empty string.
        ts_range = bundle["runtime_lib_compat"]["typescript"]["@kamiwaza-ai/extensions-lib"]
        assert isinstance(ts_range, str) and len(ts_range) > 0

    def test_cli_version_matches_package_version(self, bundle):
        """Review iteration-1 I9: the bundled cli_version must track
        kamiwaza_extensions.__version__. Hard-coding makes them drift on
        every release; this test catches it before publish."""
        import kamiwaza_extensions

        assert bundle["cli_version"] == kamiwaza_extensions.__version__, (
            f"compatibility.json declares cli_version={bundle['cli_version']!r} "
            f"but kamiwaza_extensions.__version__={kamiwaza_extensions.__version__!r}. "
            "Update kamiwaza_extensions/compatibility.json (or auto-generate it at "
            "build time) so the doctor probe reports the correct CLI version."
        )


# ---------------------------------------------------------------------------
# TS-M2-38: Python runtime-lib version probe + warn on out-of-range.
# ---------------------------------------------------------------------------

class TestPythonRuntimeLibCheck:
    @pytest.fixture
    def checker(self, tmp_path):
        return DoctorChecker(config_dir=tmp_path / ".kamiwaza")

    def test_in_range_version_passes(self, checker, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("kamiwaza-extensions-lib>=0.3,<0.4\nfastapi>=0.100\n")
        result = checker._check_python_runtime_lib(req)
        assert result.status == "pass"

    def test_out_of_range_pinned_version_warns(self, checker, tmp_path):
        # 0.1.0 is below the bundle's >= 0.2 floor — a project pinned this far
        # back will get the runtime-lib equivalent of "your toolchain is stale".
        req = tmp_path / "requirements.txt"
        req.write_text("kamiwaza-extensions-lib==0.1.0\n")
        result = checker._check_python_runtime_lib(req)
        assert result.status == "warn"
        assert "0.1.0" in result.message

    def test_missing_runtime_lib_warns(self, checker, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("fastapi>=0.100\n")
        result = checker._check_python_runtime_lib(req)
        assert result.status == "warn"


# ---------------------------------------------------------------------------
# TS-M2-39: TypeScript runtime-lib version probe + warn on out-of-range.
# ---------------------------------------------------------------------------

class TestTypeScriptRuntimeLibCheck:
    @pytest.fixture
    def checker(self, tmp_path):
        return DoctorChecker(config_dir=tmp_path / ".kamiwaza")

    def test_in_range_dependency_passes(self, checker, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {"@kamiwaza-ai/extensions-lib": "^0.3.0"},
        }))
        result = checker._check_ts_runtime_lib(pkg)
        assert result.status == "pass"

    def test_out_of_range_dependency_warns(self, checker, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {"@kamiwaza-ai/extensions-lib": "0.1.5"},
        }))
        result = checker._check_ts_runtime_lib(pkg)
        assert result.status == "warn"

    def test_missing_dependency_warns(self, checker, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"dependencies": {"react": "^18"}}))
        result = checker._check_ts_runtime_lib(pkg)
        assert result.status == "warn"


# ---------------------------------------------------------------------------
# TS-M2-40: Doctor doesn't generate false positives when neither lib is
# detected (e.g. a Go-only extension).
# ---------------------------------------------------------------------------

class TestNeitherRuntimeLibPresent:
    @pytest.fixture
    def checker(self, tmp_path):
        return DoctorChecker(config_dir=tmp_path / ".kamiwaza")

    def test_runtime_lib_check_skipped_when_no_files(self, checker, tmp_path, monkeypatch):
        # No requirements.txt, no package.json — a Go reference, say.
        # The bundle-aware check should produce zero results, not phantom
        # warnings.
        metadata = tmp_path / "kamiwaza.json"
        metadata.write_text(json.dumps({
            "name": "go-ext", "version": "0.1.0", "type": "tool",
            "kz_ext_version": ">=0.11.0",
        }))
        monkeypatch.chdir(tmp_path)
        results = checker._check_extension_context()
        # _check_cli_version always runs; runtime-lib checks should be absent.
        names = [r.name for r in results]
        assert "Runtime lib (Python)" not in names
        assert "Runtime lib (TypeScript)" not in names
