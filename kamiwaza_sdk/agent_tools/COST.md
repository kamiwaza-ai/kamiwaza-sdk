# Agent tools: measured token cost

Why the full catalog is an escape hatch and not the default, in numbers taken
from this package rather than from an estimate.

Every number below was measured on branch `feat/agent-tools-contract` at commit
`7574824`, with `cl100k_base`, entries serialised as compact JSON — the
encoding a JSON transport puts on the wire.

This document is not gated. `scripts/regenerate_agent_tools_surface.py` writes
`SURFACE.json`, and `.github/workflows/agent-tools-drift.yml` regenerates that
file in CI and fails when the committed copy is stale — but nothing checks the
numbers here. Re-run the command below and update this document whenever the
surface changes, or it goes stale silently.

Reproduce:

```python
import tiktoken
from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.agent_tools.spec_index import build_index
from kamiwaza_sdk.agent_tools.descriptors import description_coverage
from kamiwaza_sdk.agent_tools.catalog import (
    DETAIL_LEVELS,
    build_catalog,
    categories,
    measure_cost,
)

encoding = tiktoken.get_encoding("cl100k_base")


def count(text: str) -> int:
    return len(encoding.encode(text))


client = KamiwazaClient(base_url="http://localhost:7777/api")
index = build_index(client)
catalog = build_catalog(index, client)
print(index.coverage())
print(description_coverage(index, client))
for detail in DETAIL_LEVELS:
    print(detail, measure_cost(catalog, count, detail))
for category in categories(catalog):
    entries = [entry for entry in catalog if entry.category == category]
    print(category, measure_cost(entries, count))
```

The surface counts are also recorded in `SURFACE.json`, under `counts`,
`categories` and `descriptions`.

## Surface

| Measure | Value |
| --- | --- |
| Callable operations reachable through the client | 362 |
| Services and nested sub-clients | 34 |
| Published | 336 |
| Withheld, each with a stated reason | 26 |
| Operations described by the interface document | 357 |

## Cost by detail level

An entry comes at three levels, and the level is what a cost describes: a
number measured at one does not describe another.

| Level | What an entry carries | Total | Mean per entry | Against `full` |
| --- | --- | --- | --- | --- |
| `names` | identifier | **2,639** | 7 | −88% |
| `brief` | identifier, category, description | **8,672** | 25 | −62% |
| `full` | every field | **22,533** | 67 | — |

The levels follow Anthropic's documented pattern for a large tool surface — a
detail level returning "name only, name and description, or the full definition
with schemas" — and the shape Stripe's MCP server ships, where `api_search`
finds an endpoint, `api_details` returns one endpoint's schema, and the call
tools use it.

Against a 200,000-token context window, `full` is 11.3% spent before an agent
does anything, and it grows with every platform release. `names` is 1.3%. The
fixed discovery surface costs under 2,000 tokens and does not grow, which is
the whole argument for FR-001.

### What these totals do and do not include

`measure_cost` prices the entries, measured one at a time and summed. A
response carrying them adds its own envelope — on the MCP server's catalog
route, counts and filters and this cost block come to 80 tokens — and a JSON
array shares separators between entries, which tokenises slightly cheaper than
the same entries measured apart. Measured on the 336-operation surface: the
`full` array is 22,535 tokens on the wire against the 22,533 summed here, and
the whole response body is 22,600.

At `names` the gap is wider in relative terms, because per-entry overhead is
most of a short entry: 2,639 summed against 2,306 as one array. A caller
budgeting a context window wants the part that scales with the surface, which
is what these numbers are.

## Per category

Filtering is what makes the catalog usable at all: the smallest category is 7%
of the cost of all of them, and the largest is a third.

| Category | Entries | `names` | `brief` | `full` | `full` mean |
| --- | --- | --- | --- | --- | --- |
| data | 105 | 866 | 2,747 | 7,463 | 71 |
| models | 63 | 504 | 1,602 | 4,028 | 63 |
| access | 40 | 311 | 1,039 | 2,505 | 62 |
| infrastructure | 40 | 288 | 1,044 | 2,654 | 66 |
| extensions | 38 | 290 | 952 | 2,444 | 64 |
| agents | 25 | 183 | 589 | 1,591 | 63 |
| collaboration | 25 | 197 | 699 | 1,848 | 73 |

## Description coverage

The catalog's cost is mostly description text, so coverage and cost move
together. Measured by `description_coverage()` over the 336 published
operations:

| Source | Operations |
| --- | --- |
| Method docstring | 332 |
| Interface document description | 4 |
| Interface document summary | 0 |
| None | 0 |
| — of the resolved ones, under six words | 0 |

Every published operation resolves a description, and none of them is under six
words, which is what the documentation gate holds. Writing those sentences
*raised* the catalog's token cost, which is the correct trade: an agent that
picks the wrong operation because the description was three words costs more
than the tokens saved.

## Note on the 29-token figure

Earlier planning used 29 tokens per *minimal* definition — an identifier and a
description and nothing else. The 67 above is a full catalog entry: identifier,
category, description, approval requirement, four behaviour hints, and the
parameter names. The `brief` level is the closer comparison at 25, and it
carries the category as well. All three are right for what they measure.

## Encodings this package does not use

Two cheaper encodings of the `full` level were measured and rejected, because
both change the shape a host already parses:

| Encoding | Total | Against `full` |
| --- | --- | --- |
| Behaviour hints as a flag string, empty fields omitted | 14,074 | −38% |
| The same, grouped by category | 12,657 | −44% |

The five booleans on an entry take 9 distinct combinations across all 336
operations, so a flag string is a real saving. It is not taken: the detail
levels above deliver more (−62% and −88%) without changing what a field is
called or where it sits, and a host that has already written the parse should
not have to write it again to get the saving.
