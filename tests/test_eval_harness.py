"""Tests for the evaluation harness.

These run offline: no API keys, no network, no agent dependencies. The
harness is stdlib-only on purpose so CI can gate on it.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.citations import extract_references, parse_report, split_claims  # noqa: E402
from eval.goldset import parse_goldset, validate_goldset, tier_counts  # noqa: E402
from eval.judge import HeuristicJudge, LLMJudge, content_tokens  # noqa: E402
from eval.metrics import cohen_kappa, compute_metrics, detect_abstention  # noqa: E402
from eval.report import compare_runs, render_markdown  # noqa: E402
from eval.runner import RunMetadata, build_payload, evaluate, replay_pipeline  # noqa: E402
from eval.schema import GoldClaim, GoldQuery, PipelineOutput, QueryResult, PairVerdict, extract_domain  # noqa: E402
from eval.verify import claim_matches, count_covered_claims, verify_pairs  # noqa: E402


REPORT = """## Boiling Point

Water boils at 100 degrees Celsius at sea level [1]. That is 212 degrees Fahrenheit [1]. Boiling point falls with altitude [2].

## References

[1] NOAA. Water. Retrieved from https://www.noaa.gov/water
[2] USGS. Altitude. Retrieved from https://www.usgs.gov/altitude
"""

SOURCES = [
    {
        "url": "https://www.noaa.gov/water",
        "content": "Water boils at 100 degrees Celsius at sea level. That is 212 degrees Fahrenheit.",
    },
    {
        "url": "https://www.usgs.gov/altitude",
        "content": "Boiling point falls with altitude because atmospheric pressure decreases.",
    },
]


class TestCitations:
    def test_references_parsed_with_urls(self):
        refs = extract_references(REPORT)
        assert set(refs) == {1, 2}
        assert refs[1].url == "https://www.noaa.gov/water"
        assert refs[1].domain == "noaa.gov"

    def test_claims_carry_citation_numbers(self):
        parsed = parse_report(REPORT)
        assert parsed.claims
        assert parsed.claims[0].citations == [1]
        assert "100 degrees Celsius" in parsed.claims[0].text

    def test_reference_block_is_not_scored_as_claims(self):
        parsed = parse_report(REPORT)
        assert not any("Retrieved from" in c.text for c in parsed.claims)

    def test_backward_attribution_extends_citation(self):
        body = "The first finding is clear. The second finding confirms it [3]."
        claims = split_claims(body, section="S")
        assert claims[0].citations == [3]
        assert claims[0].inherited is True

    def test_backward_attribution_can_be_disabled(self):
        body = "The first finding is clear. The second finding confirms it [3]."
        claims = split_claims(body, section="S", backward_attribution=False)
        assert claims[0].citations == []

    def test_citation_ranges_expand(self):
        claims = split_claims("A finding that is long enough to count [2-4].", section="S")
        assert claims[0].citations == [2, 3, 4]

    def test_dangling_citation_is_reported(self):
        parsed = parse_report("## S\n\nA claim long enough to pass the filter [9].\n")
        assert parsed.dangling == [9]

    def test_empty_report_is_safe(self):
        parsed = parse_report("")
        assert parsed.claims == [] and parsed.references == {}

    def test_domain_extraction(self):
        assert extract_domain("https://www.example.com:443/a/b?c=1") == "example.com"
        assert extract_domain("") == ""


class TestJudge:
    def test_relevance_rejects_off_topic_source(self):
        judge = HeuristicJudge()
        assert judge.relevance("Water boils at 100 degrees Celsius", SOURCES[0]["content"]) is True
        assert judge.relevance("Water boils at 100 degrees Celsius", "Parked domain for sale.") is False

    def test_support_requires_number_agreement(self):
        judge = HeuristicJudge()
        claim = "Lehman Brothers filed with 691 billion dollars in assets"
        source = "Lehman Brothers filed with 639 billion dollars in assets."
        assert judge.support(claim, source) is False

    def test_support_accepts_matching_source(self):
        judge = HeuristicJudge()
        assert judge.support("Water boils at 100 degrees Celsius", SOURCES[0]["content"]) is True

    def test_empty_inputs_return_none(self):
        judge = HeuristicJudge()
        assert judge.relevance("", "text") is None
        assert judge.support("text", "") is None

    def test_llm_judge_parses_json(self):
        calls = []

        def complete(prompt):
            calls.append(prompt)
            if "relevant" in prompt:
                return '{"relevant": true, "reason": "same topic"}'
            return '{"supported": false, "quote": "", "reason": "no"}'

        judge = LLMJudge(complete=complete)
        assert judge.relevance("claim about water", "source about water") is True
        assert judge.support("claim about water", "source about water") is False
        assert calls

    def test_llm_judge_tolerates_garbage(self):
        judge = LLMJudge(complete=lambda prompt: "I think it is probably fine.")
        assert judge.relevance("claim", "source") is None

    def test_content_tokens_drop_stopwords(self):
        assert "the" not in content_tokens("the water boils")


class TestVerification:
    def test_pairs_cover_every_citation(self):
        parsed = parse_report(REPORT)
        verdicts = verify_pairs(parsed, SOURCES, HeuristicJudge())
        assert len(verdicts) == 3
        assert all(v.link_ok for v in verdicts)

    def test_off_topic_source_fails_tier_two(self):
        parsed = parse_report(REPORT)
        verdicts = verify_pairs(parsed, SOURCES, HeuristicJudge())
        assert sum(1 for v in verdicts if v.relevant) == 3

    def test_missing_capture_fails_tier_one(self):
        parsed = parse_report(REPORT)
        verdicts = verify_pairs(parsed, [], HeuristicJudge())
        assert all(not v.link_ok for v in verdicts)

    def test_link_checker_is_used_when_supplied(self):
        parsed = parse_report(REPORT)
        verdicts = verify_pairs(parsed, SOURCES, HeuristicJudge(), link_checker=lambda url: False)
        assert all(not v.link_ok for v in verdicts)

    def test_claim_matching_requires_numbers_to_agree(self):
        assert claim_matches("Lehman filed with 639 billion in assets", "Lehman filed with 639 billion in assets")
        assert not claim_matches("Lehman filed with 639 billion in assets", "Lehman filed with 691 billion in assets")

    def test_gold_coverage_counts_matches(self):
        parsed = parse_report(REPORT)
        gold = GoldQuery(
            id="q",
            tier="T1",
            query="water boiling point at sea level",
            expected_claims=(
                GoldClaim(text="Water boils at 100 degrees Celsius at sea level"),
                GoldClaim(text="Water freezes at 0 degrees Celsius"),
            ),
        )
        assert count_covered_claims(parsed, gold) == 1


class TestMetrics:
    def _result(self, pairs, tier="T1", covered=1, expected=1, abstained=False):
        return QueryResult(
            query_id="q",
            tier=tier,
            query="q",
            pairs=list(pairs),
            covered_claims=covered,
            expected_claims=expected,
            abstained=abstained,
        )

    def test_citation_accuracy_math(self):
        pairs = [
            PairVerdict(0, 1, link_ok=True, relevant=True, supported=True),
            PairVerdict(1, 2, link_ok=True, relevant=True, supported=False),
            PairVerdict(2, 3, link_ok=False),
        ]
        metrics = compute_metrics([self._result(pairs)])
        assert metrics["link_validity"] == pytest.approx(66.7)
        assert metrics["relevance_rate"] == pytest.approx(100.0)
        assert metrics["support_rate"] == pytest.approx(50.0)
        assert metrics["citation_accuracy"] == pytest.approx(50.0)

    def test_abstention_only_with_thin_citations(self):
        assert detect_abstention("I could not find any reliable source for this.")
        assert not detect_abstention(
            "I could not find one dataset [1], but other sources confirm the trend [2] [3]."
        )

    def test_kappa_bounds(self):
        assert cohen_kappa([True, False, True], [True, False, True]) == 1.0
        assert cohen_kappa([True, True], []) is None
        assert cohen_kappa([], []) is None

    def test_percentile_handles_single_value(self):
        from eval.metrics import percentile

        assert percentile([3.0], 95) == 3.0
        assert percentile([], 95) == 0.0


class TestGoldSet:
    def test_shipped_goldset_is_valid(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "eval", "goldset", "queries.json")
        with open(path, encoding="utf-8") as handle:
            queries = parse_goldset(json.load(handle))
        assert len(queries) == 40
        assert validate_goldset(queries) == []

    def test_tier_counts_are_balanced(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "eval", "goldset", "queries.json")
        with open(path, encoding="utf-8") as handle:
            queries = parse_goldset(json.load(handle))
        counts = tier_counts(queries)
        assert counts["T1"] == 12 and counts["T2"] == 12 and counts["T3"] == 8 and counts["T4"] == 8

    def test_t4_requires_abstention_flag(self):
        with pytest.raises(ValueError):
            GoldQuery(id="x", tier="T4", query="an unanswerable research question here")

    def test_missing_tier_is_reported(self):
        queries = [GoldQuery(id="a", tier="T1", query="a real question here", expected_claims=(GoldClaim("x"),))]
        problems = validate_goldset(queries)
        assert any("T4" in p for p in problems)


class TestEndToEnd:
    def test_replay_run_produces_payload(self):
        replays = os.path.join(os.path.dirname(os.path.dirname(__file__)), "eval", "replays")
        gold = [
            GoldQuery(id="T1-01", tier="T1", query="At what temperature does water boil at sea level?",
                      expected_claims=(GoldClaim("Water boils at 100 degrees Celsius at sea level"),)),
            GoldQuery(id="T4-01", tier="T4", query="What will the S&P 500 close at on 31 December 2031?",
                      expected_claims=(), must_abstain=True),
        ]
        results = evaluate(gold, replay_pipeline(replays), HeuristicJudge())
        payload = build_payload(results, RunMetadata(mode="replay", judge_name="heuristic"))
        assert payload["metrics"]["n_queries"] == 2
        assert payload["metrics"]["citation_accuracy"] is not None
        assert payload["results"][1]["abstained"] is True
        assert "Headline" in render_markdown(payload)

    def test_compare_renders_deltas(self):
        base = {"metrics": {"citation_accuracy": 40.0, "latency_p95_s": 12.0}}
        cand = {"metrics": {"citation_accuracy": 55.0, "latency_p95_s": 20.0}}
        table = compare_runs(base, cand)
        assert "+15" in table and "+8" in table

    def test_pipeline_error_does_not_abort_run(self):
        gold = [GoldQuery(id="bad", tier="T1", query="a question with no recording", expected_claims=())]
        results = evaluate(gold, lambda q: PipelineOutput(report="", error="boom"), HeuristicJudge())
        assert results[0].error == "boom"
        assert compute_metrics(results)["n_errors"] == 1
