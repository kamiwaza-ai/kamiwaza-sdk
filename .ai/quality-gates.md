---
name: code-health
managed-by: quality-gates
version: 1
---

# Code Health (CodeScene)

Code Health (CodeScene) runs as an advisory pre-push gate and is available on
demand via the CodeScene MCP (`analyze_change_set` for a branch,
`code_health_review` / `code_health_score` per file). Bias toward leaving every
file you touch as healthy or healthier than you found it — but spend the effort
where it changes the outcome, not chasing a green number.

- **A "degraded" verdict is a prompt to look, not noise to wave off.** "It's
  advisory" is not a reason to skip it. Before you conclude, pull the per-file
  detail and classify each degraded file.
- **Improve what you own.** If Code Health declined in production logic you wrote
  or edited, fix it when the fix is small and safe — reduce nesting, extract a
  helper, split an over-long method. This is where Code Health pays off.
- **Don't rathole on no-value findings:**
  - **Generated code** (`zz_generated*.go`, `*.pb.go`, generated clients/mocks)
    is machine output — not hand-editable, and in-file markers are clobbered on
    regen. Note it and move on; never refactor it or bend the generator to a metric.
  - **Idiomatic test patterns** (e.g. Go table-driven tests flagged "Bumpy
    Road") — only restructure if it genuinely reads better; don't make a clear
    test worse for a score.
  - **Pre-existing debt your change merely nudged** — if the file was already
    over threshold and your change crossed no new threshold, you don't own that
    debt. Note it (file a tech-debt ticket if it's material) and move on.
- **Make the call visible.** When you accept a degraded finding, say so in one
  line — which file, which class (generated / idiomatic test / pre-existing), and
  why it isn't worth fixing here. A silent dismissal and a reasoned acceptance
  look identical in the diff; write the reason.

The aim is net-positive health on the code you control, with judgment about where
improvement adds value — not a clean dashboard at the cost of mangling generated
files, tests, or unrelated legacy.
