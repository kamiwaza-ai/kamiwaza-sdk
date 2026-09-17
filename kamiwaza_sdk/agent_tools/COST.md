# Agent tools: measured token cost

Why the full catalog is an escape hatch and not the default, in numbers taken
from this package rather than from an estimate.

Every number below was measured on branch `feat/agent-tools-contract` at commit
`77d4098`.

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
from kamiwaza_sdk.agent_tools.catalog import build_catalog, categories, measure_cost

encoding = tiktoken.get_encoding("cl100k_base")


def count(text: str) -> int:
    return len(encoding.encode(text))


client = KamiwazaClient(base_url="http://localhost:7777/api")
index = build_index(client)
catalog = build_catalog(index, client)
print(index.coverage())
print(description_coverage(index, client))
print(measure_cost(catalog, count))
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
| Published | 337 |
| Withheld, each with a stated reason | 25 |
| Operations described by the interface document | 357 |

## Full catalog cost

`cl100k_base`, entries serialised as compact JSON.

| Measure | Value |
| --- | --- |
| Entries | 337 |
| Total | **22,602 tokens** |
| Mean per entry | 67 tokens |

Against a 200,000-token context window that is 11.3% spent before an agent does
anything, and it grows with every platform release. The fixed discovery surface
costs under 2,000 tokens and does not grow, which is the whole argument for
FR-001.

## Per category

Filtering is what makes the catalog usable at all: the smallest category is 7%
of the cost of all of them, and the largest is a third.

| Category | Entries | Tokens | Mean |
| --- | --- | --- | --- |
| data | 105 | 7,463 | 71 |
| models | 63 | 4,028 | 63 |
| infrastructure | 41 | 2,723 | 66 |
| access | 40 | 2,505 | 62 |
| extensions | 38 | 2,444 | 64 |
| agents | 25 | 1,591 | 63 |
| collaboration | 25 | 1,848 | 73 |

## Description coverage

The catalog's cost is mostly description text, so coverage and cost move
together. Measured by `description_coverage()` over the 337 published
operations:

| Source | Operations |
| --- | --- |
| Method docstring | 333 |
| Interface document description | 4 |
| Interface document summary | 0 |
| None | 0 |
| — of the resolved ones, under six words | 0 |

Every published operation now resolves a description, and none of them is under
six words, which is what the documentation gate holds. Writing those sentences
*raised* the catalog's token cost, which is the correct trade: an agent that
picks the wrong operation because the description was three words costs more
than the tokens saved.

## Note on the 29-token figure

Earlier planning used 29 tokens per *minimal* definition — an identifier and a
description and nothing else. The 67 above is a full catalog entry: identifier,
category, description, approval requirement, four behaviour hints, and the
parameter names. Both are right for what they measure; only the second one is
what a host actually receives.
