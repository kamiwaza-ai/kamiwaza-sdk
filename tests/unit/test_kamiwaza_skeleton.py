"""T5.1 / ENG-4677 — kamiwaza-sdk/kamiwaza/ package skeleton smoke tests.

Verifies the new top-level `kamiwaza` package (distinct from the legacy
`kamiwaza_sdk` and `kamiwaza_client` packages already in the SDK repo) is
importable and exposes the customer-facing entry points the design's §4.2.11
specifies. Implementation breadth lives in T5.2 (client surface), T5.3
(federations module), T5.9 (jobs module), T5.10 (typed exceptions); T5.1
locks in the package structure + import shape.
"""

from __future__ import annotations


def test_kamiwaza_package_importable():
    """The new top-level `kamiwaza` namespace must be importable so customer
    code can do `import kamiwaza` after `pip install kamiwaza-sdk`."""
    import kamiwaza  # noqa: F401


def test_kamiwaza_class_exposed_at_top_level():
    """The customer-facing entry point is `from kamiwaza import Kamiwaza`."""
    from kamiwaza import Kamiwaza

    assert isinstance(Kamiwaza, type), "Kamiwaza must be a class"


def test_kamiwaza_client_module_exists():
    """The Kamiwaza class lives in `kamiwaza.client` per design §4.2.11.
    Customer-facing imports go through the top-level package, but the
    module-level location is stable for advanced consumers."""
    from kamiwaza.client import Kamiwaza as KamiwazaFromClient
    from kamiwaza import Kamiwaza as KamiwazaFromTopLevel

    assert KamiwazaFromClient is KamiwazaFromTopLevel


def test_kamiwaza_instantiation_accepts_base_url_and_token():
    """T5.1 skeleton accepts the constructor params the SDK will use; T5.2
    wires them to actual httpx behavior. Skeleton just stores them."""
    from kamiwaza import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-test-1234")
    assert client.base_url == "https://kamiwaza.test"
    assert client.token == "pat-test-1234"


def test_kamiwaza_from_env_classmethod_exists():
    """T5.1 skeleton — the `from_env()` factory classmethod is the canonical
    construction path per design §4.2.11. T5.2 fills in env-var resolution;
    skeleton just verifies the classmethod is present and returns an
    instance."""
    from kamiwaza import Kamiwaza

    assert callable(getattr(Kamiwaza, "from_env", None)), (
        "Kamiwaza must expose a from_env() classmethod"
    )


def test_kamiwaza_error_base_exception():
    """T5.1 skeleton: `kamiwaza.exceptions.KamiwazaError` is the base class
    for all SDK-raised exceptions. T5.10 adds the typed subclasses."""
    from kamiwaza.exceptions import KamiwazaError

    assert issubclass(KamiwazaError, Exception)
    err = KamiwazaError("boom")
    assert str(err) == "boom"


def test_kamiwaza_error_exposed_at_top_level():
    """KamiwazaError is also importable from the top-level package for
    convenience (mirror of FastAPI's `HTTPException` ergonomics)."""
    from kamiwaza import KamiwazaError as TopLevel
    from kamiwaza.exceptions import KamiwazaError as Submodule

    assert TopLevel is Submodule
