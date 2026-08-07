# Gate waiver register

The auditable history required by
[governance-policy.md](../governance-policy.md) §3.3 (GOVERN 1.4, MANAGE 1.1):
one row per waiver of a blocking release gate, linking the in-PR waiver
comment. An empty register alongside green required checks is itself evidence —
it states that no release has bypassed a blocking gate since this register
began (2026-08-07).

Rules (normative text lives in §3.3; summarized here so the register is
self-explaining):

- Only the accountable owner (§2) may approve a row.
- The zero-tolerance risk class (FR-26 leak, filter-module mutation-gate drop,
  BDD access-control scenario) is never waivable — a row claiming it is
  invalid on its face.
- Every row names a scope/expiry; an expired row with the gate still red means
  the gate is blocking again.
- Rows are append-only. A waiver that was later judged wrong gets a follow-up
  row or issue, not an edit.

| Date | PR | Gate | Risk class (§3.1) | Scope / expiry | Approved by | Waiver comment | Follow-up |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — |

*No waivers recorded to date.*
