# {{name}}

{{description}}

## Getting Started

```bash
kz-ext login <your-instance-url>
kz-ext dev local
```

## Structure

- `src/server.py` — FastMCP server with tool definitions
- `kamiwaza.json` — Extension metadata
- `docker-compose.yml` — Local development configuration

## Kamiwaza compatibility

Declare the oldest supported platform in `kamiwaza.json`, for example
`"kamiwaza_version": ">=1.3.0"`. This is independent of `kz_ext_version`, which
constrains the developer CLI. Omission means unrestricted platform support.
Use comma-separated numeric comparisons for bounded support, such as
`">=1.3.0,<2.0.0"`; the minimum must reflect features actually required.
The explicitly enabled `compat-v1` catalog retains every extension version and
Core selects the highest compatible release. Publishing another release does
not remove older releases needed by older platforms.
