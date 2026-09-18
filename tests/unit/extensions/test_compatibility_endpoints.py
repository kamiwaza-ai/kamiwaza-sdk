"""Full containment respects included and excluded release endpoints."""

import json
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from kamiwaza_extensions.doctor import (
    DoctorChecker,
    _npm_spec_outside_supported,
    _python_spec_outside_supported,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "declared,expected",
    [
        ("0.6.0", "pass"),
        ("0.6.5", "pass"),
        ("0.7.0", "warn"),
        ("=0.7.0", "warn"),
        (">=0.6 <0.7", "pass"),
        (">=0.6 <=0.7", "warn"),
        (">=0.6 <=0.6.9", "pass"),
        (">0.6 <0.7", "pass"),
        ("^0.6.0", "pass"),
        ("~0.6.5", "pass"),
        (">=0.6 <0.8", "warn"),
        (">=0.6", "warn"),
        ("0.5.0", "warn"),
        (">=0.6 <=0.7 <0.7", "pass"),
        (">=0.6 <0.7 <=0.7", "pass"),
    ],
)
def test_typescript_current_window(
    tmp_path: Path, declared: str, expected: str
) -> None:
    package = tmp_path / "package.json"
    package.write_text(
        json.dumps({"dependencies": {"@kamiwaza-ai/extensions-lib": declared}})
    )
    result = DoctorChecker(config_dir=tmp_path / ".kamiwaza")._check_ts_runtime_lib(
        package
    )
    assert result.status == expected


@pytest.mark.parametrize(
    "declared,expected",
    [
        ("==0.6.0", "pass"),
        ("==0.7.0", "warn"),
        (">=0.6,<0.7", "pass"),
        (">=0.6,<=0.7", "warn"),
        (">=0.6,<=0.6.9", "pass"),
        (">0.6,<0.7", "pass"),
        ("~=0.6.0", "pass"),
        ("~=0.5.5", "warn"),
        (">=0.6,<0.8", "warn"),
        (">=0.6", "warn"),
        ("==0.5.0", "warn"),
        (">=0.6,<=0.7,<0.7", "pass"),
    ],
)
def test_python_current_window(tmp_path: Path, declared: str, expected: str) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("kamiwaza-extensions-lib" + declared + "\n")
    result = DoctorChecker(config_dir=tmp_path / ".kamiwaza")._check_python_runtime_lib(
        requirements
    )
    assert result.status == expected


@pytest.mark.parametrize(
    "declared,supported,outside",
    [
        (">=0.6 <=0.7", ">=0.6 <=0.7", False),
        (">=0.6 <0.7", ">0.6 <0.7", True),
        (">0.6 <0.7", ">0.6 <0.7", False),
        (">=0.5 <0.6", ">=0.5 <0.6", False),
        ("^0.5.0", ">=0.5 <0.6", False),
        ("0.6.0", ">=0.5 <0.6", True),
    ],
)
def test_npm_explicit_supported_endpoints(
    declared: str, supported: str, outside: bool
) -> None:
    assert (_npm_spec_outside_supported(declared, supported) is not None) is outside


@pytest.mark.parametrize(
    "declared,supported,outside",
    [
        (">=0.6,<=0.7", ">=0.6,<=0.7", False),
        (">=0.6,<0.7", ">0.6,<0.7", True),
        (">0.6,<0.7", ">0.6,<0.7", False),
        (">=0.5,<0.6", ">=0.5,<0.6", False),
        ("~=0.5.0", ">=0.5,<0.6", False),
        ("==0.6.0", ">=0.5,<0.6", True),
    ],
)
def test_python_explicit_supported_endpoints(
    declared: str, supported: str, outside: bool
) -> None:
    reason = _python_spec_outside_supported(
        SpecifierSet(declared), SpecifierSet(supported)
    )
    assert (reason is not None) is outside
