# Extension Regression — Manual Checklist

Companion to `tests/unit/extensions/regression/inventory.yaml`. Items here
cannot be automated under `pytest -m extension_regression` and must be
re-validated by hand against each D210-candidate build before sign-off.

Run order does not matter. Record pass/fail in the D210 UAT sign-off packet
under "Regression checklist result".

---

## REG-002 — push-local-extensions on macOS Bash 3.2

**Origin:** ENG-3718.

**Why this is manual:** the failure mode (`unbound variable` under `set -u`
with empty array expansion) only reproduces on Bash 3.2, the system shell
on macOS. CI runners ship Bash 5+ and cannot exercise the regression.

**Steps:**

1. On a macOS workstation (Bash 3.2 — confirm with `bash --version`),
   from a kamiwaza checkout that includes the candidate D210 build of
   `scripts/push-local-extensions.sh`, run:

   ```bash
   KAMIWAZA_REQUIRE_EXTENSION_PUSH=true \
   KAMIWAZA_REQUIRED_EXTENSION_REPOS='' \
   ./scripts/install-dev.sh
   ```

   (the empty-string assignment is what previously triggered
   `REQUIRED_REPO_NAMES[@]: unbound variable`)

2. Repeat with a single-element list:

   ```bash
   KAMIWAZA_REQUIRE_EXTENSION_PUSH=true \
   KAMIWAZA_REQUIRED_EXTENSION_REPOS='kamiwaza-extensions-kaizen' \
   ./scripts/install-dev.sh
   ```

3. Repeat with the full canonical list:

   ```bash
   KAMIWAZA_REQUIRE_EXTENSION_PUSH=true \
   KAMIWAZA_REQUIRED_EXTENSION_REPOS='kamiwaza-extensions-kaizen,kamiwaza-extensions-dde,kamiwaza-extension-omniparse' \
   ./scripts/install-dev.sh
   ```

**Pass criteria:** all three invocations reach the install logic without
any `unbound variable` shell errors. The empty-list case must short-circuit
the push without crashing.

**Fail handling:** open a follow-on against `scripts/push-local-extensions.sh`
and block D210 sign-off until fixed; the harness counts this as a regression.
