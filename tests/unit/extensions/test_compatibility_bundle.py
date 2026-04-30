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

    def test_bundle_is_in_pyproject_package_data(self):
        """PR-86 round-2 C1: compatibility.json must be declared in
        pyproject.toml's [tool.setuptools.package-data] for kamiwaza_extensions
        — otherwise wheel/sdist installs ship without it and `kz-ext doctor`
        crashes with FileNotFoundError on every runtime-lib check.

        The earlier coherence test caught content drift; this catches
        packaging drift."""
        from pathlib import Path

        pyproject_path = (
            Path(__file__).resolve().parents[3] / "pyproject.toml"
        )
        text = pyproject_path.read_text()
        # Find the package-data section for kamiwaza_extensions and verify
        # compatibility.json is listed. We do a coarse string check (no full
        # TOML parse) to keep this independent of the toml stdlib version.
        kw_line = next(
            (
                line for line in text.splitlines()
                if line.lstrip().startswith("kamiwaza_extensions ")
                and "=" in line
                and "templates" in line  # disambiguate from the "lib" entry
            ),
            None,
        )
        assert kw_line is not None, (
            "kamiwaza_extensions package-data entry not found in pyproject.toml"
        )
        assert "compatibility.json" in kw_line, (
            f"compatibility.json missing from kamiwaza_extensions package-data:\n"
            f"  {kw_line}\n"
            "Add it so wheel/sdist installs ship the bundle "
            "(otherwise kz-ext doctor crashes on FileNotFoundError)."
        )

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

    def test_prefix_alias_does_not_falsely_match(self, checker, tmp_path):
        """PR-86 H7 — `kamiwaza-extensions-lib-extras` must NOT match the
        `kamiwaza-extensions-lib` check (substring-prefix bug)."""
        req = tmp_path / "requirements.txt"
        req.write_text("kamiwaza-extensions-lib-extras>=1.0\n")
        result = checker._check_python_runtime_lib(req)
        assert result.status == "warn"
        assert "not found" in result.message

    def test_pep508_extras_are_handled(self, checker, tmp_path):
        """PR-86 H7 — `kamiwaza-extensions-lib[fastapi]>=0.3,<0.4` parses
        cleanly via packaging.requirements.Requirement."""
        req = tmp_path / "requirements.txt"
        req.write_text("kamiwaza-extensions-lib[fastapi]>=0.3,<0.4\n")
        result = checker._check_python_runtime_lib(req)
        assert result.status == "pass"

    def test_out_of_range_lower_bound_warns(self, checker, tmp_path):
        """PR-86 M6 — a range like `>=99.0,<100.0` is clearly outside the
        supported window, even though it's not a `==` pin."""
        req = tmp_path / "requirements.txt"
        req.write_text("kamiwaza-extensions-lib>=99.0,<100.0\n")
        result = checker._check_python_runtime_lib(req)
        assert result.status == "warn"

    def test_upper_bound_only_below_supported_warns(self, checker, tmp_path):
        """Round-3 H1 — an upper-bound-only pin like `<0.2` slips past
        the lower-bound probe (no >=/> in the spec) but every allowed
        version is below the supported floor of `>=0.2`. Must warn."""
        req = tmp_path / "requirements.txt"
        req.write_text("kamiwaza-extensions-lib<0.2\n")
        result = checker._check_python_runtime_lib(req)
        assert result.status == "warn"

    def test_fresh_scaffold_pin_falls_within_compat_window(self, checker, tmp_path):
        """PR-86 round-2 H3: a freshly scaffolded project's `requirements.txt`
        pin must already pass `kz-ext doctor`'s compatibility check.

        Otherwise every new extension trips a warning the moment it's
        created — a DX papercut for the headline ``kz-ext create`` flow.
        """
        from kamiwaza_extensions.scaffolder import build_render_context, substitute

        ctx = build_render_context(name="probe", type_="tool")
        # The tool template's requirements.txt uses the placeholder.
        rendered = substitute(
            "kamiwaza-extensions-lib{{python_runtime_lib_version}}\n", ctx
        )
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(rendered)
        result = checker._check_python_runtime_lib(req_file)
        assert result.status == "pass", (
            f"fresh scaffold pin {rendered.strip()!r} fails compat check; "
            f"build_render_context's runtime-lib version must align with "
            f"compatibility.json's supported window"
        )


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
