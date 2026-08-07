# AI governance policy

**RMF outcomes served:** GOVERN (all), MANAGE 1/4.
**Status:** Draft. Sections marked **TBD (organizational)** are decisions only the
deployment owner can make; each names its tracking issue. Everything else states
policy that the repository already enforces mechanically — written down so an
auditor can see the policy, not just the mechanism.

## 1. Purpose and scope

This policy governs the MPNexus RAG pipeline (scope defined in
[README.md](README.md)). It complements, and never overrides, the engineering
documentation: `docs/governance.md` remains authoritative for data governance
(access control, curation, lineage, retention design); `docs/testing.md` for what
is gated vs advisory; `docs/roles-and-permissions.md` for the authorization
matrix. Where this policy and an engineering doc disagree, the engineering doc is
the bug *or* this policy is — file an issue; do not fork the truth.

## 2. Accountable ownership — TBD (organizational), issue #519

- Accountable AI owner: **unnamed**. Risk acceptance, gate waivers, and incident
  escalation have no defined terminus today.
- Platform-admin tier (holders of `POSTGRES_USER`, object-store root, Keycloak
  admin, Qdrant keys): **undefined** — flagged in `docs/governance.md`'s roles
  table as the only access the application does not mediate.
- Until issue #519 closes, `REQUIREMENTS.md`'s document owner ("Corey / MPNexus
  platform") is the de-facto escalation point; this sentence is a statement of
  current fact, not a ratified assignment.

## 3. Risk tolerance and release acceptance (GOVERN 1.3/1.4, MANAGE 1.1 — issue #521)

**Status:** written policy, in force as the working rule of this repository;
formal ratification by the accountable owner is pending issue #519 (no owner is
yet named to ratify it) and is on the management-review ledger (issue #542).
Until ratified, deviations from this section are treated as policy violations,
not as unregulated space.

### 3.1 Declared risk tolerance

The organization's tolerance is declared here and the gates are calibrated to
it — not inferred backwards from whatever the gates happen to enforce (GOVERN
1.3). Framing is qualitative, per risk class, because the dominant risks of a
classification-aware RAG system are categorical (content reaches someone it
must not) rather than statistical:

| Risk class | Declared tolerance | Enforcement point |
|---|---|---|
| Access-control regression (forbidden-status or out-of-scope content reaching a user) | **Zero.** Any FR-26 leak is a security incident, never a quality miss. No waiver may be issued against this class | Golden-harness hard-fail (nightly + labeled PRs — see the §3.4 acceptance); BDD scenarios on every PR; mutation gate ≥80% on `claims.py`/`qdrant_filters.py`/`metadata.py`/`versioning.py` |
| Retrieval-quality regression | **Bounded**: within `--regression-tolerance` of the carried baseline; a drop beyond it blocks | `evaluate_retrieval.py` fail-closed defaults; nightly trend store (#493); cross-config comparisons refuse by default (issue #525) |
| Judged answer-quality metrics | **Advisory**: comparative only, same judge/prompt/golden-set; never a release blocker | `relative_only_same_judge_and_prompt` stamped in every report |
| Hallucination / ungrounded answer surface | **Bounded by design**: the system returns evidence with citations, not answers (§4); abstention noise is measured (`mean_abstention_noise`) and tracked toward zero via relevance-floor calibration | Golden abstention cases; §4 prohibited-use rules for consumers |
| Availability/latency | Undefined until the NFR-4 budget is ratified from the benchmark measurements (issue #430) | Provisional 5s p95 alert; `benchmark_latency.py` artifacts are the evidence base |

### 3.2 Release-acceptance criteria (MANAGE 1.1)

A change is acceptable for release (merge to `main`; the same bar applies to
publishing images or the Helm chart, which version in lockstep) when **every
blocking gate is green or a §3.3 waiver is recorded**. The authoritative,
maintained enumeration of gates — which are blocking, which advisory, and the
honest list of what is *not* covered — is
[`docs/testing.md`](../testing.md)'s gate table; this policy deliberately
cross-references rather than restates it, so the two cannot drift. In risk
terms: the 16 required PR checks and the nightly mutation gate enforce the
zero-tolerance row; the golden-harness regression gate enforces the bounded
row; everything `docs/testing.md` marks advisory maps to the advisory rows and
may inform, but never block, a release decision.

### 3.3 Gate waivers

- **Authority:** only the accountable owner (§2) may waive a blocking gate.
  Until issue #519 names that owner, the de-facto escalation point in §2 holds
  waiver authority, and every waiver issued in the interim is flagged for
  retroactive review at the first management review (issue #542).
- **Never waivable:** the zero-tolerance class — an FR-26 leak, a
  mutation-gate drop on the four filter modules, or a red BDD access-control
  scenario. There is no legitimate release that ships an access-control
  regression.
- **Mechanism:** ruleset admin-bypass is the only technical bypass and is
  restricted to waiver-authority holders. Using it without the record below is
  a policy violation and a §7 reportable event.
- **Record (required, both halves):**
  1. a PR comment, posted before or with the merge, following this template:

     ```
     GATE WAIVER
     Gate:      <check name, e.g. security/trivy-fs>
     Reason:    <why the red gate does not represent the risk it guards>
     Scope:     <this PR only | until <date> | until issue #NNN closes>
     Risk class:<row from governance-policy §3.1 — must not be the zero-tolerance class>
     Approved:  <waiver authority, per §3.3>
     ```
  2. a row in the waiver register
     ([`evidence/waiver-register.md`](evidence/waiver-register.md)) linking
     that comment. The register is the auditable history (GOVERN 1.4); an
     empty register plus green gates is itself evidence that no bypass
     occurred.
- **Expiry:** every waiver names a scope; open-ended waivers are not valid.
  Expired-but-still-red gates reopen as blocking.

### 3.4 Standing risk acceptances folded into this policy

- **Label-gated golden-query e2e (risk R-6, issue #529):** the only live-stack
  leak/quality gate runs nightly and on labeled PRs, not on every PR — a
  deliberate cost tradeoff. Accepted, with conditions: the nightly must stay
  red-visible (a failed nightly is triaged next working day), the mutation and
  BDD gates continue to run on every PR as the compensating control, and any
  PR touching retrieval/filter code gets the `needs-e2e` label before merge.
  This acceptance is recorded here to close the "accepted pending formal
  record" status in the risk register.

## 4. Acceptable use

**Intended use.** Grounded retrieval over curator-approved organizational
documents for authenticated, authorized users, with source/classification
citations returned for verification (FR-21/FR-27). The system returns *evidence*;
answer generation belongs to the calling client (C7).

**Prohibited uses** (drawn from limits the engineering docs already state):

1. Outputs must not be used as a source for **derivative classification** —
   generated answers lack banner/portion/authority markings
   (`docs/governance.md`, output-handling rule).
2. Retrieval results must not be treated as a **completeness claim** — absence of
   results is not evidence of absence (access scoping means different users see
   different corpora by design).
3. No user may attempt to access content beyond their clearance/releasability/
   access-scope. Reconnaissance-shaped query patterns (volume, denial ratio,
   narrow-result probing) are detectable when the offline detector or adapted
   SIEM rules run (`docs/siem-detection-runbook.md`); filter-edge probing
   specifically is not observable (out-of-scope queries return successful
   empty results — threat model §4), and continuous detection is the open
   R-4/R-15 gap. Flagged identities are treated per §7.
4. The advisory scans (classification suggestion, PII, marking detection) must
   never be treated as decisions — they are curator-facing hints by design.
5. Uploading content above the environment's accreditation level is spillage,
   handled per §7, not a tagging correction.

## 5. Human oversight

Oversight is concentrated where consequence is highest, and is mandatory, not
optional:

- **Every document** passes a human curator (org-scoped, clearance-capped, FR-10..16)
  before becoming retrievable — validated live. Curators can also suspend
  approved documents (issue #478) — implemented, tested against mocks.
- **Destruction** supports two-person control (`PURGE_TWO_PERSON_REQUIRED`) —
  implemented, tested against mocks; the confirm step is API-only today (gap
  G3, no UI). Destruction evidence is retained by design.
- **Output-side oversight is deliberately out of scope** (C7): generation happens
  in the calling client. The compensating controls are citations for human
  verification (FR-27) and abstention on low-confidence/no-result (FR-28). This
  is a recorded scoping decision, revisited if the system boundary ever changes.

## 6. Model and system change management

Policy: no unpinned artifact reaches production (NFR-16, CI-enforced); any change
to embedding model, chunking, or reranker **mandates re-evaluation** against the
golden set (`docs/testing.md` re-evaluation policy); version lockstep and
immutable releases make every prior version redeployable (release publish flow
validated live at v0.1.0; rollback-by-redeploy is implied by immutability, not
separately exercised). Gap being closed: the retrieval-eval
config fingerprint that makes cross-change comparisons impossible to miss
(issue #525).

## 7. Incident response — TBD (organizational), issue #522

Proposed AI-incident taxonomy (to be ratified with the response ladder):

| Class | Example | Proposed severity |
|---|---|---|
| `access-control-leak` | Non-approved or out-of-scope content in results | Critical — zero-tolerance class of §3 |
| `spillage-via-mistag` | Over-classified content approved under wrong tags | Critical; invokes purge + `chat_plane_action_required` procedure |
| `injection-compromise` | Generation observably follows corpus-embedded instructions | High; see risk R-7 |
| `quality-regression` | Baseline gate failure | Medium |
| `detector-stale` | Watchdog alerts (anomaly-detector staleness today; quality staleness once issue #526 lands) | Medium — monitoring itself failed |

Blocking prerequisites, both tracked in issue #522: a real Alertmanager receiver
(default is a no-op) and a decided response ladder for flagged identities.

## 8. Decommissioning and data disposition

Mechanisms: supersession without a dead window (NFR-13), purge with tombstones
and retained destruction evidence, chat-plane purge procedure. Policy blocked on
the retention ratification (issue #520) — until then the implemented subset
(purge, session reaping) is the only destruction that happens, and that fact is
itself the recorded policy.

## 9. Internal audit and management review — TBD (organizational)

Proposed (tracked in issue #542):

- **Management review**: quarterly; inputs are this policy's TBD ledger, the
  [risk register](risk-register.md), the evidence index, and the trend stores;
  minutes archived under `evidence/snapshots/<date>/management-review.md`.
- **Internal audit**: annually; method = re-run the assessment behind
  [rmf-mapping.md](rmf-mapping.md) against current `main` and diff the statuses.
- The first conducted review converts this section from proposal to record.

## 10. Training and awareness — TBD (organizational)

Role-facing how-to articles exist in-app (FR-33, claims-gated). What the RMF asks
beyond that — periodic risk-awareness training for curators (the humans making
consequential approval decisions) and evidence it happened — does not exist and
is owned by the deployment, not this repository. Recorded as a gap.
