"""Unit tests for the scenario-evidence.v2 record contract.

Covers build identity, method, capability_ids, evidence provenance, status
derivation, and structural validation of the versioned run-record schema
(ENG-9748; sales-developer-release-kit design §3.6/§4.6). Split out from
``test_harness.py``, which pins the harness's step-dispatch/record/sign-off
contract independent of the evidence-record schema.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from tests.e2e.scenarios import build_identity, harness
from tests.e2e.scenarios.harness import (
    CAPABILITY_ID_RE,
    EVIDENCE_METHODS,
    EVIDENCE_PROVENANCES,
    EVIDENCE_SCHEMA_ID,
    EVIDENCE_SCHEMA_PATH,
    SCENARIO_STATUSES,
    STEP_STATUSES,
    ScenarioResult,
    StepResult,
    _validate_runbook,
    derive_status,
    record_run,
    resolve_build_identity,
    run_scenario,
    validate_evidence_record,
)

# Version-first: the leading segment is the release a question asks by, and
# the rest is producer annotation (ENG-10715, build_identity.py). A fixture
# leading with anything else would be refused by resolve_build_identity.
TEST_BUILD = "0.99.0; core@sha256:abc1234; test-fixture"


@pytest.fixture(autouse=True)
def _build_identity_env(monkeypatch):
    """Every harness run needs a build identity (scenario-evidence.v2, G1).

    Both build-identity env vars are controlled here, not inherited: a
    KAMIWAZA_RELEASE exported in the ambient shell (which is exactly what
    ENG-10715 asks operators and CI to do) would otherwise satisfy the
    refusal tests and silently stop them testing refusal.
    """
    monkeypatch.delenv("KAMIWAZA_RELEASE", raising=False)
    monkeypatch.setenv("KAMIWAZA_BUILD", TEST_BUILD)


def _runbook(steps, *, scenario_id="S1", **extra):
    return {
        "id": scenario_id,
        "name": f"Test scenario {scenario_id}",
        "sign_off_actor": "SDK team",
        "uacs": ["UAC-16"],
        "expected_outcomes": ["something demonstrable"],
        "steps": steps,
        **extra,
    }


def _one_step_runbook(**extra):
    return _runbook([{"name": "x", "description": "..."}], **extra)


def _synthetic_v2_record(**overrides):
    """A conforming v2 record built by hand, with no harness involvement.

    Shaped the way a non-harness producer (e.g. the UI journey runner,
    T3.1) would emit it — ``method: manual``-style variance, a step with
    no ``detail``, dot-namespaced and kebab-case capability ids — to pin
    that the schema stays free of SDK-harness-specific assumptions.
    """
    record = {
        "schema": "scenario-evidence.v2",
        "scenario_id": "S2",
        "scenario_name": "App launched from Workroom Manager",
        "started_at": "2026-08-06T12:00:00+00:00",
        "finished_at": "2026-08-06T12:00:42+00:00",
        "duration_s": 42.0,
        "sign_off_actor": "SDK team",
        "ci_job_url": None,
        "build": "kamiwaza-1.2.3+build.777",
        "method": "manual",
        "capability_ids": ["workrooms.create", "workroom-app-launch"],
        "evidence_provenance": "pre-existing",
        "status": "passed_with_notes",
        "steps": [
            {"name": "a", "status": "passed", "duration_s": 1.5, "detail": "ok"},
            {"name": "b", "status": "skipped", "duration_s": 0.1},
        ],
    }
    record.update(overrides)
    return record


@pytest.mark.unit
class TestBuildIdentity:
    """G1: evidence without build identity is refused, not silently emitted."""

    def test_refuses_to_run_without_build_identity(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_BUILD", raising=False)
        calls: list[int] = []

        def handler():
            calls.append(1)
            return "ran"

        with pytest.raises(ValueError, match="build identity"):
            run_scenario(_one_step_runbook(), {"x": handler})
        assert calls == [], "harness must refuse BEFORE executing any step"

    def test_whitespace_only_build_is_refused(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_BUILD", "   ")
        with pytest.raises(ValueError, match="build identity"):
            run_scenario(_one_step_runbook(), {"x": lambda: "ok"})

    def test_env_var_supplies_build(self):
        result = run_scenario(_one_step_runbook(), {"x": lambda: "ok"})
        assert result.build == TEST_BUILD

    def test_explicit_build_arg_overrides_env(self):
        result = run_scenario(
            _one_step_runbook(), {"x": lambda: "ok"}, build="0.1.0; explicit"
        )
        assert result.build == "0.1.0; explicit"

    def test_resolve_build_identity_strips_whitespace(self):
        assert resolve_build_identity("  1.2.3  ") == "1.2.3"


@pytest.mark.unit
class TestVersionFirstBuildIdentity:
    """ENG-10715: a stamp no version query can reach is refused at capture.

    Cycle 1 stamped the image digest and nothing else. Every record was
    schema-valid, nothing failed, and the whole corpus turned out to be
    unanswerable months later -- a question about a release matched none of
    it. These assert the failure now happens where the identity is known.
    """

    def test_a_release_version_is_accepted(self):
        assert resolve_build_identity("1.3.0") == "1.3.0"
        assert (
            resolve_build_identity("1.3.0; core@sha256:abc; uat")
            == "1.3.0; core@sha256:abc; uat"
        )

    def test_a_prerelease_is_a_release_identity(self):
        assert resolve_build_identity("1.3.0-rc3; core@sha256:abc") == (
            "1.3.0-rc3; core@sha256:abc"
        )

    def test_a_dev_build_is_accepted(self):
        assert resolve_build_identity("develop@8d21d43; core@sha256:abc") == (
            "develop@8d21d43; core@sha256:abc"
        )

    def test_a_digest_first_stamp_is_refused(self, monkeypatch):
        """The exact shape cycle 1 emitted."""
        monkeypatch.delenv("KAMIWAZA_RELEASE", raising=False)
        legacy = (
            "ghcr.io/kamiwaza-internal/kamiwaza/images/core@sha256:"
            + "a" * 64
            + " @ kamiwaza.test (local k0s)"
        )
        with pytest.raises(ValueError, match="version-first"):
            resolve_build_identity(legacy)

    def test_the_refusal_names_both_ways_out(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_RELEASE", raising=False)
        with pytest.raises(ValueError) as excinfo:
            resolve_build_identity("kamiwaza-1.0.0-rc2")
        message = str(excinfo.value)
        assert "KAMIWAZA_RELEASE" in message
        assert "KAMIWAZA_BUILD" in message

    def test_release_env_composes_in_front_of_an_annotation(self, monkeypatch):
        """The migration path: keep exporting the digest, add the release."""
        monkeypatch.setenv("KAMIWAZA_RELEASE", "1.3.0")
        assert resolve_build_identity("core@sha256:abc123") == (
            "1.3.0; core@sha256:abc123"
        )

    def test_release_env_composes_in_front_of_several_annotations(
        self, monkeypatch
    ):
        """The migration path for the shape cycle 1 actually stamped.

        The operator's existing KAMIWAZA_BUILD is ``digest; environment`` --
        the shape the --build help text advertises -- so composing must fold
        it in as its own segments rather than treating the whole string as
        one annotation.
        """
        monkeypatch.setenv("KAMIWAZA_RELEASE", "1.3.0")
        assert resolve_build_identity(
            "core@sha256:abc123; kamiwaza.test (local k0s)"
        ) == "1.3.0; core@sha256:abc123; kamiwaza.test (local k0s)"

    def test_a_stamp_written_without_the_separator_space_is_version_first(self):
        """``;`` alone is the same identity -- the space is presentation."""
        assert resolve_build_identity("1.3.0;core@sha256:abc") == (
            "1.3.0; core@sha256:abc"
        )

    def test_release_env_does_not_second_guess_a_correct_stamp(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_RELEASE", "1.3.0")
        assert resolve_build_identity("1.2.0; core@sha256:abc") == (
            "1.2.0; core@sha256:abc"
        )

    def test_release_env_alone_is_a_complete_identity(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_RELEASE", "1.3.0")
        monkeypatch.delenv("KAMIWAZA_BUILD", raising=False)
        assert resolve_build_identity() == "1.3.0"

    def test_a_malformed_release_env_is_refused(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_RELEASE", "develop@8d21d43")
        with pytest.raises(ValueError, match="semver release version"):
            resolve_build_identity("core@sha256:abc123")

    def test_refusal_happens_before_any_step_runs(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_RELEASE", raising=False)
        monkeypatch.setenv("KAMIWAZA_BUILD", "core@sha256:abc123")
        calls: list[int] = []

        with pytest.raises(ValueError, match="version-first"):
            run_scenario(_one_step_runbook(), {"x": lambda: calls.append(1)})
        assert calls == []


@pytest.mark.unit
class TestEvidenceV2Fields:
    def test_harness_emits_v2_envelope_defaults(self):
        result = run_scenario(_one_step_runbook(), {"x": lambda: "ok"})
        assert result.schema == EVIDENCE_SCHEMA_ID
        assert result.method == "automated"
        assert result.evidence_provenance == "cycle-authored"
        assert result.capability_ids == []
        assert result.status == "passed"

    def test_capability_ids_copied_from_runbook(self):
        runbook = _one_step_runbook(
            capability_ids=["workrooms.create", "workroom-app-launch"]
        )
        result = run_scenario(runbook, {"x": lambda: "ok"})
        assert result.capability_ids == ["workrooms.create", "workroom-app-launch"]

    def test_evidence_provenance_overridable_via_arg(self):
        result = run_scenario(
            _one_step_runbook(),
            {"x": lambda: "ok"},
            evidence_provenance="pre-existing",
        )
        assert result.evidence_provenance == "pre-existing"

    def test_evidence_provenance_overridable_via_env(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_EVIDENCE_PROVENANCE", "pre-existing")
        result = run_scenario(_one_step_runbook(), {"x": lambda: "ok"})
        assert result.evidence_provenance == "pre-existing"

    def test_invalid_evidence_provenance_raises(self):
        with pytest.raises(ValueError, match="evidence_provenance"):
            run_scenario(
                _one_step_runbook(),
                {"x": lambda: "ok"},
                evidence_provenance="from-the-future",
            )


@pytest.mark.unit
class TestStatusDerivation:
    """Three-valued status maps to the sign-off template's PASS /
    PASS WITH NOTES / FAIL decision."""

    def _two_step_runbook(self):
        return _runbook(
            [
                {"name": "a", "description": "..."},
                {"name": "b", "description": "..."},
            ]
        )

    def test_all_passed_is_passed(self):
        result = run_scenario(
            self._two_step_runbook(), {"a": lambda: "ok", "b": lambda: "ok"}
        )
        assert result.status == "passed"

    def test_any_failed_is_failed(self):
        def boom():
            raise RuntimeError("kaboom")

        result = run_scenario(self._two_step_runbook(), {"a": boom, "b": lambda: "ok"})
        # Includes a not_reached step — still "failed", not "with notes".
        assert [s.status for s in result.steps] == ["failed", "not_reached"]
        assert result.status == "failed"

    def test_skipped_step_is_passed_with_notes(self):
        def skip_b():
            pytest.skip("does not apply")

        result = run_scenario(
            self._two_step_runbook(), {"a": lambda: "ok", "b": skip_b}
        )
        assert result.status == "passed_with_notes"

    def test_pending_step_is_passed_with_notes(self):
        result = run_scenario(self._two_step_runbook(), {"a": lambda: "ok"})
        assert result.status == "passed_with_notes"

    def test_derive_status_empty_steps_is_failed(self):
        assert derive_status([]) == "failed"

    def test_derive_status_direct_vocabulary(self):
        step = lambda st: StepResult(name="x", status=st, duration_s=0.0)  # noqa: E731
        assert derive_status([step("passed")]) == "passed"
        assert derive_status([step("passed"), step("failed")]) == "failed"
        assert derive_status([step("passed"), step("skipped")]) == "passed_with_notes"
        assert derive_status([step("pending")]) == "passed_with_notes"


@pytest.mark.unit
class TestEvidenceValidation:
    def test_synthetic_v2_record_is_valid(self):
        validate_evidence_record(_synthetic_v2_record())

    def test_record_run_emits_a_valid_v2_record(self, monkeypatch, tmp_path):
        monkeypatch.setattr(harness, "RUNS_DIR", tmp_path / "runs")
        result = run_scenario(
            _one_step_runbook(capability_ids=["workrooms.create"]),
            {"x": lambda: "ok"},
        )
        path = record_run(result)
        record = json.loads(path.read_text())
        assert record["schema"] == EVIDENCE_SCHEMA_ID
        assert record["build"] == TEST_BUILD
        assert record["method"] == "automated"
        assert record["capability_ids"] == ["workrooms.create"]
        assert record["evidence_provenance"] == "cycle-authored"
        assert record["status"] == "passed"
        validate_evidence_record(record)

    @pytest.mark.parametrize(
        ("mutation", "expected_msg"),
        [
            ({"build": ""}, "build"),
            ({"started_at": "unknown"}, "started_at"),
            ({"finished_at": "unknown"}, "finished_at"),
            ({"started_at": "2026-08-06"}, "started_at"),
            ({"started_at": "2026-08-06T12:00:00"}, "started_at"),
            ({"schema": "scenario-evidence.v1"}, "schema"),
            ({"method": "auto"}, "method"),
            ({"status": "green"}, "status"),
            ({"evidence_provenance": "unknown"}, "evidence_provenance"),
            ({"capability_ids": ["Not_Kebab"]}, "capability_ids"),
            ({"capability_ids": "workrooms.create"}, "capability_ids"),
            ({"steps": []}, "steps"),
            ({"duration_s": -1}, "duration_s"),
            (
                {"steps": [{"name": "a", "status": "purple", "duration_s": 0.1}]},
                r"steps\[0\].status",
            ),
        ],
    )
    def test_invalid_records_are_rejected(self, mutation, expected_msg):
        record = _synthetic_v2_record(**mutation)
        with pytest.raises(ValueError, match=expected_msg):
            validate_evidence_record(record)

    def test_z_suffix_timestamp_is_accepted(self):
        """RFC 3339 permits a bare ``Z`` UTC suffix; the schema/harness are
        documented as evidence-surface-neutral, so an external producer
        (e.g. the UI journey runner) emitting ``Z`` must validate cleanly
        regardless of the Python version the validator runs on."""
        record = _synthetic_v2_record(
            started_at="2026-08-06T12:00:00Z", finished_at="2026-08-06T12:00:42Z"
        )
        validate_evidence_record(record)

    def test_record_run_persists_a_z_suffixed_result(self, monkeypatch, tmp_path):
        """Regression: a validated record with a bare-``Z`` ``finished_at``
        must not crash filename generation (``_timestamp_suffix`` used to
        call ``fromisoformat`` without the ``Z``-normalization applied by
        the validator)."""
        monkeypatch.setattr(harness, "RUNS_DIR", tmp_path / "runs")
        result = ScenarioResult(
            scenario_id="S1",
            scenario_name="t",
            started_at="2026-08-06T17:00:00Z",
            finished_at="2026-08-06T17:00:05Z",
            duration_s=5.0,
            sign_off_actor="SDK team",
            ci_job_url=None,
            build=TEST_BUILD,
            status="passed",
            steps=[StepResult(name="x", status="passed", duration_s=0.1)],
        )
        path = record_run(result)
        assert path.exists()

    @pytest.mark.parametrize("field", ["method", "evidence_provenance", "status"])
    def test_malformed_enum_field_raises_value_error_not_type_error(self, field):
        """Regression: set-membership on an unhashable value (e.g. a list)
        raised TypeError, escaping the ``ValueError`` this module documents
        as its sole error type for invalid records."""
        record = _synthetic_v2_record(**{field: ["not", "a", "string"]})
        with pytest.raises(ValueError, match=field):
            validate_evidence_record(record)

    def test_malformed_step_status_raises_value_error_not_type_error(self):
        record = _synthetic_v2_record(
            steps=[{"name": "a", "status": ["bad"], "duration_s": 0.1}]
        )
        with pytest.raises(ValueError, match=r"steps\[0\].status"):
            validate_evidence_record(record)

    def test_missing_required_field_is_rejected(self):
        record = _synthetic_v2_record()
        del record["build"]
        with pytest.raises(ValueError, match="missing required field 'build'"):
            validate_evidence_record(record)

    def test_record_run_refuses_to_persist_invalid_record(self, monkeypatch, tmp_path):
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr(harness, "RUNS_DIR", runs_dir)
        result = ScenarioResult(
            scenario_id="S1",
            scenario_name="t",
            started_at="2026-08-06T17:00:00+00:00",
            finished_at="2026-08-06T17:00:05+00:00",
            duration_s=5.0,
            sign_off_actor="SDK team",
            ci_job_url=None,
            build="",  # the G1 violation
            status="passed",
            steps=[StepResult(name="x", status="passed", duration_s=0.1)],
        )
        with pytest.raises(ValueError, match="build"):
            record_run(result)
        assert not runs_dir.exists() or not list(
            runs_dir.iterdir()
        ), "an invalid record must not be persisted"

    def test_v1_artifact_is_untouched_and_not_v2(self):
        """A pre-existing v1 run record — no ``schema`` field — is readable,
        unmigrated, and intentionally rejected as v2.

        Built inline rather than read from ``runs/`` on disk: that directory
        is gitignored (per-run JSON artifacts, not committed — see
        ``tests/e2e/scenarios/.gitignore``), so a real v1 artifact is never
        present in a clean checkout or CI.
        """
        v1_record = {
            "scenario_id": "S1",
            "scenario_name": "User-facing app with forced login",
            "started_at": "2026-05-01T12:00:00+00:00",
            "finished_at": "2026-05-01T12:00:05+00:00",
            "duration_s": 5.0,
            "sign_off_actor": "Preston McGowan",
            "ci_job_url": None,
            "steps": [
                {"name": "x", "status": "passed", "duration_s": 0.1, "detail": "ok"}
            ],
        }
        assert "schema" not in v1_record
        assert "build" not in v1_record
        with pytest.raises(ValueError, match="missing required field"):
            validate_evidence_record(v1_record)


@pytest.mark.unit
class TestSchemaFileSync:
    """Pins schemas/scenario-evidence.v2.schema.json against the harness
    constants so the JSON Schema and the structural validator cannot drift."""

    @pytest.fixture()
    def schema(self):
        return json.loads(EVIDENCE_SCHEMA_PATH.read_text())

    def test_schema_vocabulary_matches_harness(self, schema):
        props = schema["properties"]
        assert props["schema"]["const"] == EVIDENCE_SCHEMA_ID
        assert set(props["status"]["enum"]) == SCENARIO_STATUSES
        assert set(props["method"]["enum"]) == EVIDENCE_METHODS
        assert set(props["evidence_provenance"]["enum"]) == EVIDENCE_PROVENANCES
        assert props["capability_ids"]["items"]["pattern"] == CAPABILITY_ID_RE.pattern
        step_props = schema["$defs"]["step"]["properties"]
        assert set(step_props["status"]["enum"]) == STEP_STATUSES

    def test_schema_required_matches_emitted_record_shape(self, schema):
        result = run_scenario(_one_step_runbook(), {"x": lambda: "ok"})
        emitted_keys = set(asdict(result))
        assert set(schema["required"]) == emitted_keys
        assert set(schema["properties"]) == emitted_keys

    def test_v2_record_validates_against_schema_file(self, schema):
        """Full JSON Schema validation — runs only where jsonschema happens
        to be importable (it is not a declared dependency; the structural
        validator in harness.py is the always-on check)."""
        jsonschema = pytest.importorskip("jsonschema")
        validator = jsonschema.Draft202012Validator(schema)
        validator.validate(_synthetic_v2_record())
        assert list(validator.iter_errors(_synthetic_v2_record(build="")))

    @pytest.mark.parametrize(
        "capability_id",
        ["workrooms.create", "workroom-app-launch", "a", "a2.b-c.d", "s3.multi-part"],
    )
    def test_capability_id_pattern_accepts(self, capability_id):
        assert CAPABILITY_ID_RE.fullmatch(capability_id)

    @pytest.mark.parametrize(
        "capability_id",
        [
            "Workrooms.Create",
            "workrooms..create",
            "-lead",
            "trail-",
            "a_b",
            "",
            "a.",
            "workrooms.create\n",
        ],
    )
    def test_capability_id_pattern_rejects(self, capability_id):
        assert not CAPABILITY_ID_RE.fullmatch(capability_id)


@pytest.mark.unit
class TestValidateRunbookCapabilityIds:
    """The OPTIONAL capability_ids runbook field (mapping runbooks to
    capability documents is T1.4; the field is accepted but not required)."""

    def test_absent_capability_ids_is_accepted(self, tmp_path):
        _validate_runbook(_one_step_runbook(), source=tmp_path / "s1-x.yaml")

    def test_valid_capability_ids_accepted(self, tmp_path):
        rb = _one_step_runbook(
            capability_ids=["workrooms.create", "workroom-app-launch"]
        )
        _validate_runbook(rb, source=tmp_path / "s1-x.yaml")

    def test_non_list_capability_ids_raises(self, tmp_path):
        rb = _one_step_runbook(capability_ids="workrooms.create")
        with pytest.raises(ValueError, match="capability_ids must be a list"):
            _validate_runbook(rb, source=tmp_path / "s1-x.yaml")

    def test_non_string_entry_raises(self, tmp_path):
        rb = _one_step_runbook(capability_ids=["workrooms.create", 7])
        with pytest.raises(ValueError, match="capability_ids must be a list"):
            _validate_runbook(rb, source=tmp_path / "s1-x.yaml")

    def test_malformed_entry_raises(self, tmp_path):
        rb = _one_step_runbook(capability_ids=["Workrooms.Create"])
        with pytest.raises(ValueError, match="kebab-case"):
            _validate_runbook(rb, source=tmp_path / "s1-x.yaml")


@pytest.mark.unit
class TestBuildIdentityProducerContract:
    """Direct cover for the producer half (``build_identity``) itself.

    ``resolve`` is the only caller the harness has, but its branches route
    through ``compose`` and ``is_well_formed``, and a branch reachable only
    through one shape of operator env is a branch no scenario test walks.
    """

    def test_compose_joins_release_and_annotations(self):
        assert build_identity.compose("1.3.0", "core@sha256:abc", "uat") == (
            "1.3.0; core@sha256:abc; uat"
        )

    def test_compose_drops_blank_and_none_annotations(self):
        assert build_identity.compose("1.3.0", None, "  ", "uat") == "1.3.0; uat"

    def test_compose_refuses_an_empty_release(self):
        with pytest.raises(ValueError, match="non-empty release segment"):
            build_identity.compose("   ", "core@sha256:abc")

    def test_compose_refuses_a_separator_inside_a_part(self):
        """A part carrying ``;`` would silently become two segments."""
        with pytest.raises(ValueError, match="may not contain"):
            build_identity.compose("1.3.0", "core@sha256:abc; uat")

    def test_split_segments_strips_and_tolerates_a_missing_space(self):
        assert build_identity.split_segments("1.3.0;  core@sha256:abc ; uat") == [
            "1.3.0",
            "core@sha256:abc",
            "uat",
        ]

    def test_is_well_formed_covers_both_reachable_shapes(self):
        assert build_identity.is_well_formed("1.3.0; core@sha256:abc")
        assert build_identity.is_well_formed("develop@8d21d43; core@sha256:abc")
        assert not build_identity.is_well_formed("core@sha256:abc; 1.3.0")

    def test_resolve_reads_the_env_mapping_it_is_given(self):
        """``env=`` is the injection seam the harness never uses in anger."""
        env = {"KAMIWAZA_RELEASE": "1.3.0", "KAMIWAZA_BUILD": "core@sha256:abc"}
        assert build_identity.resolve(None, env) == "1.3.0; core@sha256:abc"

    def test_a_whitespace_only_build_argument_does_not_shadow_the_env(self):
        env = {"KAMIWAZA_BUILD": "1.3.0; core@sha256:abc"}
        assert build_identity.resolve("   ", env) == "1.3.0; core@sha256:abc"
