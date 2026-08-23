# UK Standard Visitor Visa — Knowledge Base Index

RAG-ready knowledge base of official UK government guidance on the Standard Visitor visa. Each file below is a self-contained chunk with YAML frontmatter (`citation_id`, `source_url`, `source_title`, `retrieved_date`). All content retrieved/compiled 2026-08-23.

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
