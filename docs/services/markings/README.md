# Optional markings

`client.markings` exposes the active server profile. Markings are disabled by
default; an installed provider does not activate them. The SDK contains no
vocabulary or parser and never infers authorization from a label.

This example assumes the active server profile recognizes `Company Private`.

```python
config = client.markings.config()
if config.enabled:
    parsed = client.markings.parse("Company Private", default_for="document")
    if parsed.marking is None:
        raise ValueError("The server returned no marking for this input")
    workroom = client.workrooms.create(
        "Project", "persistent", marking=parsed.marking,
    )
    presentation = client.markings.compose([parsed.marking])
```

A marking contains `profile_id`, `profile_revision`, `level_id`, optional
`raw_text`, and an `attributes` object validated by the server's selected
provider. Resource responses also expose the derived `level_id`. Connector
configuration accepts optional `level_boundary` and `default_marking`; document
indexing accepts `marking_text` for server normalization. Omitted values carry
no SDK default. Partial updates preserve omitted fields; explicit null requests
removal, subject to server validation and authorization.

The response display is plain `text`, `background_color`, and `foreground_color`.
Render it as text; do not parse it to make access decisions. Use the profile's
configured `levels` for selectors, limiting assignments to `assignable` choices.

SDK 1.2 and extension runtime libraries 0.6 must deploy with the corresponding
platform update. Identity now carries `marking_level` (Python/Go JSON) or
`markingLevel` (TypeScript), from `X-User-Marking-Level`. The header is trusted
only after platform ingress authentication; extensions do not mint authority.
No provider package is an SDK dependency. Offline deployments must include the
selected provider and dependencies with the platform installation media.
