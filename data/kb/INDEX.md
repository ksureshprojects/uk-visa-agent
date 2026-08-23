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
| `application-process.md` | `visitor-application-process-01` | How to apply: online application, ID Check app or VAC biometric appointment, ~3-week standard decision time, long-term (2/5/10-year) visa option and its under-18 cap. |
| `fees-and-processing.md` | `visitor-fees-01` | Fee figures: £135 standard fee, £1,172 in-UK extension fee, priority (+£500) and super priority (+£1,000) faster-decision add-ons, with a currency caveat. |

## Known gaps / uncertainties (see also individual files)

- Direct `WebFetch` of gov.uk and legislation.gov.uk pages was **blocked by this environment's network egress policy** for the entire research pass. All content was instead compiled from `WebSearch` result snippets/summaries that cite official gov.uk URLs, cross-checked across multiple queries for consistency. **This knowledge base has not been verified against the raw, verbatim gov.uk page text or the raw Immigration Rules Appendix Visitor legal text.** A follow-up pass with working gov.uk access is recommended before this is used for anything beyond a portfolio demo.
- No official numeric financial threshold exists for this route (confirmed absence, not a gap) — do not let retrieval/generation invent a specific "£X required" figure.
- Exact recency requirements for financial documents (e.g. "bank statements must be within the last N months") were not confirmed against an official gov.uk statement — flagged in `required-documents.md`.
- Exact current fees for the 2/5/10-year long-term visa options were not confirmed with confidence — flagged in `fees-and-processing.md`.
- Country-specific application mechanics (ID Check app availability, VAC locations) were not researched per-country.
