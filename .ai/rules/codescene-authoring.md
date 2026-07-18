# CodeScene Authoring Rules — Write Green on the First Push

Goal: get the **CodeScene Code Health CI check** (the `codescene-reviewer` GitHub App
check, e.g. "CodeScene Code Health Review") to pass on the **first** push — no
review-then-fix ping-pong.

## Why a few per-function caps are enough

The check is a **delta gate on the files your diff touches**. It fails only when your
change **introduces or worsens** a code-health biomarker on a function you edited —
not for pre-existing debt you merely brushed past. So "green in one turn" reduces to a
small, memorizable target: **keep every function you write or touch under the caps
below, and do not reduce the file's cohesion.**

## The caps

Thresholds were established by probing the CodeScene analysis engine
(`@codescene/codehealth-mcp` — the same engine the CI check runs) on 2026-07-16: a
synthetic file brackets each biomarker just above and just below its suspected trigger,
and `code_health_review` reports which functions cross the line. The Complex Method
threshold is additionally confirmed by the CodeScene tooling ("threshold = 9"). Re-run
that probe when the engine version changes.

| Keep it under | Biomarker that fires when exceeded | Fix when approaching the cap |
|---|---|---|
| **Cyclomatic complexity ≤ 9** per function | Complex Method (fires > 9) | Replace if/elif ladders with a dispatch dict; extract branches |
| **Nesting depth ≤ 3** | Deep/Nested Complexity (fires at 4) | Guard-clause early returns; invert conditions; extract inner loop |
| **≤ 4 parameters** | Excess Function Arguments (fires at 5) | Bundle related args into a `@dataclass(frozen=True)` / struct |
| **≤ 1 boolean operator per condition** | Complex Conditional (fires at 2 `and`/`or`) | Extract compound tests to a named predicate: `if _is_ready(x):` |
| **Function body ≤ 70 lines** | Large Method (fires > 70) | Extract cohesive chunks into helpers |
| **One logical block per function** | Bumpy Road (fires at ≥ 2 nested "bumps") | Each distinct nested block becomes its own helper |

**Brain Method needs no rule of its own** — it is precisely the *combination* of high
complexity, deep nesting, many lines, and many arguments. Hold the six caps and you
cannot construct one. Your repo's own `.ai/rules/style.md` may set stricter limits on function
length, file size, and class size — follow the stricter number; it keeps you comfortably clear of Large Method and Brain
Class.

## File/module level

- **One responsibility per file** (Low Cohesion / LCOM4): if a file's functions split
  into groups that neither share state nor call each other, they belong in separate
  modules. This is what makes a file score Red even when each function is small.
- **Cap file size and function count** (Brain Class = large file + many functions +
  ≥ 1 Brain Method): extract collaborators before a file balloons.
- **DRY** (Duplication): extract a shared helper on the second or third repeat of a
  block.

## The trap when editing an already-complex function

The gate compares against the base branch, so **adding lines to a function that is
already near a cap pushes it over** — even when your addition is reasonable. Real case:
a PR added 8 lines of error-handling to `_index_chunks_inner`, tipping it past
cyclomatic 9 into Complex Method and failing the check, despite the file's score barely
moving.

Rule: **when you must add to a function already near a cap, refactor it in the SAME
change** (extract a helper) so the net stays under the cap. Aim to leave every function
you touch **net-neutral or better**, never worse.

## The backstop that guarantees it

Static caps handle the function-level smells; cohesion and duplication you cannot always
eyeball. Close the gap **in the same turn**, before you push:

- Heed the **local pre-push CodeScene hook** (quality-gates runs `analyze_change_set`
  against your change set). Do not skip past a Code-Health decline with
  `DEVLOOP_SKIP_PREPUSH`.
- Or run `code_health_review` (CodeScene MCP) on each file you touched.

It is the **same engine** as the CI App — clean locally means green in CI. When it flags
something, fix it now; that is the whole point of getting to green in one push.

See `.ai/quality-gates.md` for the accept/suppress policy (when a finding is genuinely
not worth fixing — generated code, idiomatic tests, pre-existing debt — say so
explicitly rather than skipping silently).
