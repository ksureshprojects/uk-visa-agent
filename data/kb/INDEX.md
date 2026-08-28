# UK Visa Knowledge Base Index

RAG-ready knowledge base of official UK government guidance, now spanning four visa categories: **Standard Visitor**, **Skilled Worker**, **Student**, and **Family (Partner/Spouse)**. Each file below is a self-contained chunk with YAML frontmatter (`citation_id`, `source_url`, `source_title`, `retrieved_date`).

## Standard Visitor (retrieved/compiled 2026-08-23)

| File | citation_id | Summary |
|---|---|---|
| `overview.md` | `visitor-overview-01` | What the Standard Visitor visa is, who needs it, headline facts (max 6-month stay, £135 fee, apply from outside UK, not a settlement route). |
| `eligibility.md` | `visitor-eligibility-01` | The core eligibility tests: leave-the-UK, not-de-facto-residence, genuine purpose, no prohibited activities, sufficient funds, valid passport/entry clearance; notes on age/minors. |
| `genuine-visitor-test.md` | `visitor-genuine-test-01` | Deep dive on the "genuine visitor" / intention-to-leave test — how caseworkers assess travel history, credibility, and ties; consequences of failing it (refusal/cancellation). |
| `permitted-activities.md` | `visitor-permitted-activities-01` | What a Standard Visitor may do: tourism, qualifying business activities, short study (≤6 months), academic visits, private medical treatment, Permitted Paid Engagements, events. |
| `prohibited-activities.md` | `visitor-prohibited-activities-01` | What a Standard Visitor must not do: work (with the narrow PPE exception), access public funds, study >6 months, marry/register civil partnership, de facto residence. |
| `financial-requirement.md` | `visitor-financial-requirement-01` | Maintenance/funds requirement — no fixed published minimum; must show sufficient resources for living costs, accommodation, return travel, dependants, and planned paid activities; third-party sponsorship evidence. |
| `required-documents.md` | `visitor-required-documents-01` | Supporting documents: passport, financial evidence (bank statements showing fund origin), employer/student letters, purpose-specific invitation letters (business, academic, medical treatment). |
| `application-process.md` | `visitor-application-process-01` | How to apply: online application, VAC biometric appointment (ID Check app availability not confirmed on this pass), ~3-week standard decision time, long-term (2/5/10-year) visa option and its under-18 cap. |
| `fees-and-processing.md` | `visitor-fees-01` | Fee figures: £135 standard fee, £1,172 in-UK extension fee, priority (+£500) and super priority (+£1,000) faster-decision add-ons, with a currency caveat. |

This route also has a Phase 2 document-assembly schema (`data/schemas/standard_visitor.json`) — the app can walk an applicant all the way through drafting a package, not just determine the category.

## Skilled Worker (retrieved 2026-08-26)

| File | citation_id | Summary |
|---|---|---|
| `skilled-worker-overview.md` | `skilled-worker-overview-01` | What it is (replaces old Tier 2 General), 5-year duration, settlement after 5 years, dependants, key restrictions (no public funds, can't freely switch employer). |
| `skilled-worker-eligibility.md` | `skilled-worker-eligibility-01` | Sponsorship/certificate of sponsorship requirement, eligible-occupation requirement, salary requirement (role/date-specific, no single flat figure), English requirement. |
| `skilled-worker-required-documents.md` | `skilled-worker-required-documents-01` | CoS reference, English evidence, passport, job/employer details, conditional docs (savings, TB test, criminal record certificate for named sectors, ATAS, translations). |
| `skilled-worker-application-process.md` | `skilled-worker-application-process-01` | Online application, ID Check app vs VAC biometrics, ~3-week decision time outside the UK. |
| `skilled-worker-fees.md` | `skilled-worker-fees-01` | Fee tiers (£819/£1,618 outside UK, £943/£1,865 inside UK, reduced Immigration Salary List rates), IHS ~£1,035/year, £1,270 savings requirement. |

## Student (retrieved 2026-08-26)

| File | citation_id | Summary |
|---|---|---|
| `student-overview.md` | `student-overview-01` | 16+ age requirement, CAS-sponsored place, duration by course level (up to 5 years degree / 2 years below-degree), £558 fee headline, dependants, work summary. |
| `student-eligibility.md` | `student-eligibility-01` | Age, CAS/sponsorship, financial requirement pointer, English requirement (qualification vs SELT vs institution assessment, B2/B1 by course level), exemptions. |
| `student-financial-requirement.md` | `student-financial-requirement-01` | £1,529/month London vs £1,171/month outside London (up to 9 months), 28-day bank statement holding rule, course-fee evidence, loan/sponsorship alternative. |
| `student-required-documents.md` | `student-required-documents-01` | Passport, CAS, conditional docs (funds, ATAS, parental consent + relationship proof if under 18, TB test, sponsor consent), under-18 extra requirements. |
| `student-work-restrictions.md` | `student-work-restrictions-01` | What's permitted (study, sabbatical officer role, conditional term-time work) vs not (public funds, certain jobs, self-employment, maintained schools); dependants/progression notes. |
| `student-fees.md` | `student-fees-01` | £558 flat fee (new/extend/switch), IHS payable separately and scaled to visa length. |

## Family — Partner/Spouse route only (retrieved 2026-08-26)

The wider `gov.uk/uk-family-visa` guide also covers parent, child, and adult-dependent-relative routes — **those are out of scope for this knowledge base**; only the partner/spouse route is covered below.

| File | citation_id | Summary |
|---|---|---|
| `family-partner-overview.md` | `family-partner-overview-01` | Scope note (partner/spouse route only), £2,064/£1,407 fee (outside/inside UK), IHS worked example, settlement pointer. |
| `family-partner-eligibility.md` | `family-partner-eligibility-01` | 18+ age, qualifying relationship types (married/civil partner, 2-year cohabiting, fiancé(e), long-distance 2-year), sponsor's own status options, genuineness/cohabitation-intent requirement. |
| `family-partner-financial-requirement.md` | `family-partner-financial-requirement-01` | £29,000/year combined income (new applications from 11 Apr 2024); £18,600 + per-child amounts for pre-11-Apr-2024 extensions; savings alternative (figure not published); disability/carer's-benefit exemption. |
| `family-partner-required-documents.md` | `family-partner-required-documents-01` | Identity/immigration history, sponsor status proof, English proof, financial evidence sources, relationship/family evidence. |
| `family-partner-settlement.md` | `family-partner-settlement-01` | 5-year, 2-year (conditions not fully detailed — confirm per case), and 10-year routes to indefinite leave to remain. |

Skilled Worker, Student, and Family (Partner) each also have a Phase 2 document-assembly schema now (`data/schemas/skilled_worker.json`, `student.json`, `family_partner.json`) — a confident or forced determination for any of the four categories walks the applicant through interactive document collection, not just a one-off advisory summary.

## Skilled Worker / Student / Family (Partner) — known gaps

Compiled via `WebFetch` of live gov.uk pages on 2026-08-26, one topic-page at a time (these three routes are gov.uk "smart answer" guides with many sub-pages, unlike Standard Visitor's single flatter guide). Known gaps, deliberately left as gaps rather than guessed at:

- **Skilled Worker salary threshold**: no single figure exists — it depends on the occupation's "going rate" and the certificate-of-sponsorship date. Do not let retrieval/generation state a specific salary number as a general rule.
- **Skilled Worker eligible-occupation list**: not enumerated in this KB; the general eligibility page doesn't restate it, and pulling the full occupation-code table was out of scope for this pass.
- **Family (Partner) English-language evidence**: this KB does not yet confirm which specific evidence types (SELT, qualification, exemptions) apply to the partner/spouse route specifically — do not assume it mirrors the Student route's rules.
- **Family (Partner) savings-alternative conversion**: gov.uk confirms savings can substitute for income but the exact conversion figure was not captured in this pass.
- **Family (Partner) 2-year settlement route**: the qualifying conditions for this shorter route are not fully detailed in `family-partner-settlement-01` — confirm per case rather than assuming general eligibility.
- **IHS (Immigration Health Surcharge) exact per-year rates**: captured as one illustrative figure for Skilled Worker (~£1,035/year) and one worked example for Family Partner (£2,587.50 for 2.5 years); Student's IHS page did not surface a rate in this pass. Confirm the current rate at application time for all three routes rather than treating these as fixed.
- Country-specific mechanics (ID Check app availability by nationality, VAC locations, per-country document lists) were not researched for these three routes, matching the same known gap already flagged for Standard Visitor.

## Recompile note (2026-08-23)

This knowledge base was recompiled from **direct `WebFetch` of live gov.uk pages** on 2026-08-23, after the environment's network policy was updated to allow gov.uk access. All nine chunk files below were rewritten from the raw page text/quoted content of their cited `source_url`, superseding an earlier version of this KB that was compiled only from `WebSearch` result snippets (that version's caveats are now resolved — see below).

Sources fetched directly for this recompile: `gov.uk/standard-visitor` (+ its `/apply-standard-visitor-visa` and `/extend-your-stay` sub-pages), `gov.uk/guidance/immigration-rules/immigration-rules-appendix-v-visitor`, `gov.uk/guidance/immigration-rules/immigration-rules-appendix-visitor-permitted-activities`, `gov.uk/government/publications/visit-guidance/visit-caseworker-guidance-accessible--2`, `gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/...`, and `gov.uk/faster-decision-visa-settlement`.

Gaps resolved by this pass:
- Fee figures (standard £135, transit £74.50, medical £234, academic £234, long-term 2/5/10-year at £506/£903/£1,128, in-UK extension £1,172, priority +£500, super priority +£1,000) are now confirmed directly from live gov.uk pages rather than search summaries — see `fees-and-processing.md`.
- The Appendix Visitor vs Appendix V: Visitor naming ambiguity is resolved: **`immigration-rules-appendix-visitor` (without "V") 404s** — `immigration-rules-appendix-v-visitor` is the current, correct live guidance page and paragraph numbering (V 1, V 4.2, V 4.4–4.6) is now cited directly — see `eligibility.md` and `prohibited-activities.md`.
- The volunteering time cap is confirmed as **30 days total** — see `permitted-activities.md`.
- Financial-document recency: gov.uk does not publish a strict "within N months" rule, but does explicitly flag statements/letters **older than 1 year** as less useful evidence — see `financial-requirement.md` and `required-documents.md`.
- Translation-document requirements (translator attestation, date, name/signature, contact details) are now quoted directly — see `required-documents.md`.

## Known gaps / uncertainties still remaining (see also individual files)

- No official numeric financial threshold exists for this route (confirmed absence, not a gap) — do not let retrieval/generation invent a specific "£X required" figure.
- The live gov.uk apply page did not explicitly restate the "UK Immigration: ID Check" app as an identity-verification alternative to an in-person VAC appointment; only the in-person route was confirmed on that page in this pass — flagged in `application-process.md`.
- Country-specific application mechanics (ID Check app availability by nationality, VAC locations, per-country document lists) were not researched per-country.
- The underlying visa fee table (the ODS/HTML "Home Office immigration and nationality fees" document itself, effective 8 April 2026 per its publication page) was not read directly — its figures were instead cross-confirmed via two live gov.uk consumer-facing pages (`/apply-standard-visitor-visa` and `/extend-your-stay`) rather than the primary fee-table document. Re-verify against the live fee table if this KB is used well after its retrieval date.
