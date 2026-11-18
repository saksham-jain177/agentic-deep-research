# Deep Research Agent - Evaluation (full gold set, replay)

_40 queries | judge: `heuristic` | mode: `replay` | commit: `88b1c10` | run at 2026-11-17T17:15:48+00:00_

## Headline

| Metric | Value |
| --- | --- |
| Citation accuracy (all pairs) | 72.7% |
| Content captured for citation | 100% |
| Link validity (URL probed) | n/a |
| Relevance rate (of capturable) | 90.9% |
| Support rate (of on-topic) | 80% |
| Judge failure rate | 0% |
| Effective citations / report | 2 |
| Gold claim recall | 83.3% |
| Uncited claim rate | 26.7% |
| Abstention on unanswerable (T4) | 100% |
| False abstention (T1-T2) | 0% |
| Queries truncated at pair cap | 0 |
| Latency p50 (s) | 22.4 |
| Latency p95 (s) | 48.6 |
| Mean cost / report (USD) | 0.00805 |
| Queries in gold set | 40 |
| Queries scored | 4 (10% of gold set) |
| Skipped, not attempted | 36 |
| Errors | 0 |
| Scored pairs | 11 |


## By tier

| Tier | Queries | Citation acc. | Support | Claim recall | Abstention | p95 latency |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 12 | 71.4% | 83.3% | 100% | 0% | 14.2s |
| T2 | 12 | 75% | 75% | 75% | 0% | 48.6s |
| T3 | 8 | n/a | n/a | n/a | n/a | 0s |
| T4 | 8 | n/a | n/a | n/a | 100% | 22.4s |

## Where it fails

| Failure mode | Pairs |
| --- | --- |
| source does not establish claim | 2 |
| source off-topic for claim | 1 |

## Reading this table

- **Citation accuracy** is the headline and its denominator is every mapped (claim, citation) pair. An unreachable link, an off-topic page, an unsupported claim and a judge that failed all count against it - the score cannot be raised by deleting citations or by a flaky judge.
- **Content captured** and **link validity** are different measurements. The first says the research step stored text for that citation; the second says a URL was probed and answered. Only one of them is shown at a time.
- **Support rate** is conditional on the source being on-topic; read it beside citation accuracy, never alone.
- **Abstention on T4** and **false abstention on T1-T2** must be read together; either alone is trivially gameable.
- **Queries truncated at pair cap** must be zero. A non-zero value means the run scored a prefix of some reports and the metrics understate the work.

> full gold set, replay mode: only the 4 committed recordings are scored

