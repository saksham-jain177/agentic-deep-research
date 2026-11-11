# Deep Research Agent - Evaluation

_4 queries | judge: `heuristic` | mode: `replay` | commit: `111ebf3` | run at 2026-11-05T11:17:13+00:00_

## Headline

| Metric | Value |
| --- | --- |
| Citation accuracy | 72.7% |
| Support rate (of on-topic) | 80% |
| Relevance rate | 90.9% |
| Link validity | 100% |
| Effective citations / report | 2 |
| Gold claim recall | 83.3% |
| Uncited claim rate | 26.7% |
| Abstention on unanswerable (T4) | 100% |
| False abstention (T1-T2) | 0% |
| Latency p50 (s) | 22.4 |
| Latency p95 (s) | 48.6 |
| Mean cost / report (USD) | 0.00805 |
| Queries evaluated | 4 |
| Errors | 0 |
| Scored pairs | 11 |

## By tier

| Tier | Queries | Citation acc. | Support | Claim recall | Abstention | p95 latency |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 2 | 71.4% | 83.3% | 100% | 0% | 14.2s |
| T2 | 1 | 75% | 75% | 75% | 0% | 48.6s |
| T4 | 1 | n/a | n/a | n/a | 100% | 22.4s |

## Where it fails

| Failure mode | Pairs |
| --- | --- |
| source does not establish claim | 2 |
| source off-topic for claim | 1 |

## Reading this table

- **Citation accuracy** is the headline: of all evaluable (claim, source) pairs, how many had a working link, an on-topic source, and a source that actually establishes the claim.
- **Support rate** isolates tier 3, so a high citation accuracy driven by easy sources is visible rather than hidden.
- **Abstention on T4** and **false abstention on T1-T2** must be read together; either alone is trivially gameable.

