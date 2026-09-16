# Agent tools: measured token cost

Why the full catalog is an escape hatch and not the default, in numbers taken
from this package rather than from an estimate.

Reproduce:

```python
import tiktoken
from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.agent_tools.spec_index import build_index
from kamiwaza_sdk.agent_tools.catalog import build_catalog, categories, measure_cost

encoding = tiktoken.get_encoding("cl100k_base")
client = KamiwazaClient(base_url="http://localhost:7777/api")
index = build_index(client)
catalog = build_catalog(index, client)
print(measure_cost(catalog, lambda text: len(encoding.encode(text))))
print(categories(catalog))
```

## Surface

| Measure | Value |
| --- | --- |
| Callable operations reachable through the client | 310 |
| Services | 27 |
| Published | 294 |
| Withheld, each with a stated reason | 16 |
| Operations described by the interface document | 233 |

## Full catalog cost

`cl100k_base`, entries serialised as compact JSON.

| Measure | Value |
| --- | --- |
| Entries | 294 |
| Total | **18,865 tokens** |
| Mean per entry | 64 tokens |

Against a 200,000-token context window that is 9.4% spent before an agent does
anything, and it grows with every platform release. The fixed discovery surface
costs under 2,000 tokens and does not grow, which is the whole argument for
FR-001.

## Per category

Filtering is what makes the catalog usable at all: one category is a tenth of
the cost of all of them.

| Category | Entries | Tokens | Mean |
| --- | --- | --- | --- |
| data | 79 | 5,385 | 68 |
| models | 67 | 4,202 | 62 |
| infrastructure | 41 | 2,642 | 64 |
| access | 33 | 1,891 | 57 |
| extensions | 30 | 1,842 | 61 |
| collaboration | 21 | 1,497 | 71 |
| agents | 23 | 1,406 | 61 |

## Description coverage

The catalog's cost is mostly description text, so coverage and cost move
together. Measured by `description_coverage()`:

| Source | Operations |
| --- | --- |
| Method docstring | 224 |
| Interface document description | 6 |
| Interface document summary | 23 |
| None | 41 |
| — of the resolved ones, under six words | 85 |

The 41 with no description and the 85 too thin to choose between are what the
documentation gate holds at a ceiling and T116 drives to zero. Writing those
sentences will *raise* the catalog's token cost, which is the correct trade: an
agent that picks the wrong operation because the description was three words
costs more than the tokens saved.

## Note on the 29-token figure

Earlier planning used 29 tokens per *minimal* definition — an identifier and a
description and nothing else. The 64 above is a full catalog entry: identifier,
category, description, approval requirement, four behaviour hints, and the
parameter names. Both are right for what they measure; only the second one is
what a host actually receives.
