"""Build identity for scenario-evidence.v2 records (ENG-10715).

A record's ``build`` names the thing the run executed against (design gap
G1). The kit's primary consumer question is asked by **release version** --
*"does Kamiwaza do X for 1.3.0?"* -- so the string is **version-first**: its
first ``"; "``-separated segment is the release identity, and everything
after it is a producer annotation.

    1.3.0; ghcr.io/.../core@sha256:1902...; kamiwaza.test (local k0s)
    develop@1902515efc7f; ghcr.io/.../core@sha256:1902...; kamiwaza.test

**Canonical definition:** ``schemas/scenario-evidence.v2.schema.md`` in
``kamiwaza-internal/capability-kit``, alongside the schema this repo already
vendors a mirror of. That document is authoritative; this module is the
producer half of the same contract, kept small on purpose -- the harness
needs to *compose* an identity and *recognize* a well-formed one. It never
needs to *match* one against a query, which is the part that lives in the
kit and stays there.

Why a stamp that leads with a digest is refused rather than warned about:
the first pass at G1 stamped exactly that, and the whole of cycle 1's
evidence -- 26 records -- turned out to be unreachable by any question a
consumer would ask. Nothing failed at capture time; it failed months later,
at the query. Refusing here costs one clear error at the point where the
identity is actually known.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

SEPARATOR = "; "

#: Semver 2.0.0. A *release* identity, pre-releases (`1.0.0-rc3`) included.
_SEMVER = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

#: A development build: ``<ref>@<sha>``, e.g. ``develop@8d21d43``. The sha is
#: a short git commit sha, or the short hex of the image digest when that is
#: all the runner can honestly say about what it pulled.
_DEV_BUILD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*@[0-9a-f]{7,40}$")

#: The release the run is evidence for. Set by the operator/CI from the
#: release image's tag -- the same place the digest comes from.
RELEASE_ENV = "KAMIWAZA_RELEASE"
#: The build identity, or (with RELEASE_ENV set) the annotations to hang off
#: it: image digest, environment, whatever identifies the artifact.
BUILD_ENV = "KAMIWAZA_BUILD"


def split_segments(build: str) -> list[str]:
    """The ``;``-separated segments of a build identity, stripped.

    The canonical separator is ``"; "``, but a stamp written without the
    space is the same identity and is read as one -- the space is
    presentation, not contract.
    """
    return [segment.strip() for segment in build.split(";")]


def release_segment(build: str) -> str:
    """The leading segment: the release identity the record is evidence for."""
    return split_segments(build)[0]


def is_release_version(segment: str) -> bool:
    """True for a semver release version, pre-releases included."""
    return bool(_SEMVER.match(segment))


def is_dev_build(segment: str) -> bool:
    """True for a development build, ``<ref>@<sha>``."""
    return bool(_DEV_BUILD.match(segment))


def is_well_formed(build: str) -> bool:
    """True when a build identity leads with a release identity.

    The two shapes the kit can be asked by. Anything else is admissible to
    the record layer -- ``build`` is a ``minLength: 1`` string and must stay
    one -- but no version query can reach it.
    """
    segment = release_segment(build)
    return is_release_version(segment) or is_dev_build(segment)


def compose(release: str, *annotations: str | None) -> str:
    """Compose a version-first build identity from its parts.

    Blank and ``None`` annotations are dropped: an empty segment would break
    every prefix comparison after it. A separator inside a part is refused,
    since it would silently split one annotation into two.
    """
    parts = [release.strip()]
    parts += [a.strip() for a in annotations if a is not None and a.strip()]
    if not parts[0]:
        raise ValueError("build identity requires a non-empty release segment")
    for part in parts:
        if ";" in part:
            raise ValueError(
                f"build identity segment may not contain ';': {part!r}"
            )
    return SEPARATOR.join(parts)


def _validated_release(release: str) -> str:
    """The release segment, or refuse.

    Narrower than what the record layer accepts, deliberately: this value
    becomes the identity a consumer asks by, and a dev build here would make
    develop evidence answer release questions -- the premise the whole
    version-first design protects, arrived at by the back door.
    """
    if not is_release_version(release):
        raise ValueError(
            f"{RELEASE_ENV} is {release!r}, which is not a semver release "
            "version (e.g. 1.3.0). A dev build belongs in "
            f"{BUILD_ENV} as 'develop@<sha>; ...', never here."
        )
    return release


def _no_build_refusal() -> str:
    return (
        "scenario evidence requires a build identity: pass build=... to "
        f"run_scenario (the drivers wire the --build pytest option), or set "
        f"{BUILD_ENV} (with {RELEASE_ENV} for the release). Refusing to run "
        "-- a record without build identity cannot support staleness "
        "queries (G1)."
    )


def _refusal(resolved: str) -> str:
    return (
        f"scenario evidence requires a version-first build identity; got "
        f"{resolved!r}, whose leading segment {release_segment(resolved)!r} is "
        "neither a release version (1.3.0) nor a dev build (develop@8d21d43).\n"
        f"Either stamp it version-first, or set {RELEASE_ENV} to the release "
        f"and leave {BUILD_ENV} as the digest/environment annotation:\n"
        f"    export {RELEASE_ENV}=1.3.0\n"
        f"    export {BUILD_ENV}='ghcr.io/.../core@sha256:<digest>'\n"
        "Refusing to run -- a record no version query can reach is evidence "
        "of nothing anyone will ask for (ENG-10715)."
    )


def resolve(build: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Resolve the build identity for an evidence record, or refuse.

    Precedence for the identity: the explicit ``build`` argument (the
    scenario drivers wire the ``--build`` pytest option through here), then
    ``KAMIWAZA_BUILD``.

    Then the release. If the resolved identity is already version-first its
    segments are kept as they are -- a caller that stamps correctly is never
    second-guessed; only the separator between segments is normalized, and
    empty segments dropped. Otherwise ``KAMIWAZA_RELEASE`` is composed in front of
    it, which is the migration path from the digest-only stamp: the operator
    keeps exporting the digest and adds the release the image was tagged
    with. With neither, the harness refuses.

    Refusing rather than warning is this arm's existing contract (a record
    without a build identity cannot support a staleness query, so the
    harness will not emit one), extended to the identity being *usable*
    rather than merely present.
    """
    environ = os.environ if env is None else env
    resolved = (build or "").strip() or environ.get(BUILD_ENV, "").strip()
    release = (environ.get(RELEASE_ENV) or "").strip()

    # A release and nothing else is a complete, if unannotated, identity --
    # better than refusing a run whose release IS known.
    if not resolved and release:
        return _validated_release(release)
    if not resolved:
        raise ValueError(_no_build_refusal())
    if is_well_formed(resolved):
        # Already reachable; kept as-is but for the separator, which is
        # normalized so a consumer comparing on "; " sees what it expects.
        segments = split_segments(resolved)
        return compose(segments[0], *segments[1:])
    if release:
        return compose(_validated_release(release), *split_segments(resolved))
    raise ValueError(_refusal(resolved))
