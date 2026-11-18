"""Evaluation harness for the Deep Research AI Agent.

This package is deliberately dependency-free (standard library only) so that
it can be imported, unit-tested, and run in CI without API keys, a vector
store, or the agent's third-party stack. It measures the agent from the
outside: given a report (and the sources behind it), it produces reproducible
citation, grounding, abstention, and cost metrics.

Entry point::

    python -m eval run --mode replay
    python -m eval report --results results/latest.json
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
