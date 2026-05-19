# {{SCENARIO_ID}} — {{SCENARIO_NAME}}

**Sign-off actor:** {{SIGN_OFF_ACTOR}}
**Run date:** {{RUN_DATE}}
**CI job:** {{CI_JOB_URL}}
**Total duration:** {{DURATION_S}}s

## Step results

| Step | Status | Duration | Detail |
|------|--------|----------|--------|
{{STEPS_TABLE}}

## Decision

- [ ] **PASS** — all steps green; no UAC violations; ready for D210 sign-off
- [ ] **PASS WITH NOTES** — green with caveats (record below; require lead review)
- [ ] **FAIL** — at least one step failed or one expected outcome did not hold

## Notes / caveats

_(Free-form: surprising behavior, follow-on issues to file, deviations from the runbook.)_

## Sign-off

| Field | Value |
|-------|-------|
| GitHub login | _(fill in)_ |
| Timestamp (UTC) | _(fill in)_ |
| Decision | _(PASS / PASS WITH NOTES / FAIL)_ |
| Follow-on issues filed | _(comma-separated ENG-NNNN list, or "none")_ |
