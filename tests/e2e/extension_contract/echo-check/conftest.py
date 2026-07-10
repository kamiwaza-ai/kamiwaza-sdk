# The echo-check backend has its own pytest suite under backend/tests/ that
# assumes backend/ as its working directory (imports `from app.main`). It
# cannot be collected from the SDK repo root — the imports would fail with
# `ModuleNotFoundError: No module named 'app'`. Keep ignored here; run the
# backend suite explicitly via `cd backend && pytest` (the extension-contract-live.yml
# workflow does this in a dedicated step).
collect_ignore = ["backend"]

