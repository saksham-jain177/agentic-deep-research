"""The versioned report contract (ROADMAP item 21).

The eval harness, the cost dashboard and the exporters all parse the report
dict the pipeline emits; silent drift there is how a benchmark starts lying.
This module pins the shape and versions it:

* ``SCHEMA_VERSION`` - stamped on every report dict the pipeline emits
  (``draft_agent.collect_streamed_report`` is the single construction site).
* ``validate(report)`` - returns a list of human-readable violations for
  missing required keys, undocumented keys, wrong sub-shapes and a stale or
  missing version. Empty list means the report conforms.
* ``assert_valid(report)`` - raises :class:`ReportContractError` on the first
  violation, for callers that want a gate instead of a list.

The test that enforces the contract end to end is
``tests/test_report_contract.py::TestPipelineEmitsTheContractedShape``: it
runs the offline mock pipeline and validates its emitted report, so ANY shape
change anywhere upstream - a key added, removed, or retyped - fails the suite
until the contract here is updated and ``SCHEMA_VERSION`` is bumped. That is
deliberate friction: a shape change is a contract change, and the contract is
what every downstream parser depends on.

Versioning rule: adding an OPTIONAL key or widening a type is a **minor**
bump (1.0 -> 1.1); removing or renaming a key, changing a type, or making an
optional key required is a **major** bump (1.x -> 2.0) and every downstream
parser must be updated in the same change.

v1 boundary: the contract pins top-level keys, the section sub-shape
(``title``/``content`` required, ``verification`` optional), the reference
element type and the metadata keys. The top-level ``verification`` and
``contradictions`` lists are pinned as lists only - their element shapes
differ between the LLM judge and the heuristic fallback and are not
load-bearing for any parser today. Pin them when a parser starts depending
on them.
"""

SCHEMA_VERSION = "1.0"

# Keys every emitted report dict must carry.
REQUIRED_KEYS = {"sections", "references", "metadata", "verification", "contradictions"}

# Keys that may be absent but, when present, are part of the contract.
OPTIONAL_KEYS = {"error", "schema_version", "events"}

# Sub-shapes. ``verification`` in a section is the judge's verdict dict for
# that section, present when judging ran; ``index``/``event`` are transport
# keys and are deliberately NOT part of the report contract.
SECTION_KEYS = {"title", "content"}
SECTION_OPTIONAL_KEYS = {"verification"}
METADATA_KEYS = {"model", "language", "writing_style", "citation_format"}


class ReportContractError(RuntimeError):
    """A report dict violates the versioned contract."""


def validate(report) -> list:
    """Return the list of contract violations (empty means conformant)."""
    if not isinstance(report, dict):
        return [f"report must be a dict, got {type(report).__name__}"]

    violations: list = []

    version = report.get("schema_version")
    if version is None:
        violations.append(
            "missing 'schema_version' - every emitted report must carry the "
            "contract version"
        )
    elif version != SCHEMA_VERSION:
        violations.append(
            f"'schema_version' is {version!r} but the contract is "
            f"{SCHEMA_VERSION!r} - if the shape changed, update "
            "report_contract.py and bump SCHEMA_VERSION"
        )

    for key in sorted(REQUIRED_KEYS):
        if key not in report:
            violations.append(f"missing required key '{key}'")

    for key in sorted(set(report) - REQUIRED_KEYS - OPTIONAL_KEYS):
        violations.append(
            f"undocumented top-level key '{key}' - document it in "
            "report_contract.py (and bump SCHEMA_VERSION) or remove it"
        )

    sections = report.get("sections")
    if sections is not None and not isinstance(sections, list):
        violations.append("'sections' must be a list")
    elif isinstance(sections, list):
        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                violations.append(f"sections[{i}] must be a dict with 'title' and 'content'")
                continue
            for key in ("title", "content"):
                if not isinstance(section.get(key), str):
                    violations.append(f"sections[{i}].{key} must be a string")
            for key in sorted(set(section) - SECTION_KEYS - SECTION_OPTIONAL_KEYS):
                violations.append(
                    f"sections[{i}] has undocumented key '{key}' - document it "
                    "in report_contract.py or remove it"
                )

    references = report.get("references")
    if references is not None and not isinstance(references, list):
        violations.append("'references' must be a list")
    elif isinstance(references, list):
        for i, reference in enumerate(references):
            if not isinstance(reference, str):
                violations.append(f"references[{i}] must be a string")

    metadata = report.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        violations.append("'metadata' must be a dict")
    elif isinstance(metadata, dict):
        for key in sorted(METADATA_KEYS):
            if key not in metadata:
                violations.append(f"metadata is missing documented key '{key}'")
        for key in sorted(set(metadata) - METADATA_KEYS):
            violations.append(
                f"metadata has undocumented key '{key}' - document it in "
                "report_contract.py (and bump SCHEMA_VERSION) or remove it"
            )

    for key in ("verification", "contradictions"):
        value = report.get(key)
        if value is not None and not isinstance(value, list):
            violations.append(f"'{key}' must be a list")

    return violations


def assert_valid(report) -> None:
    """Gate form: raise :class:`ReportContractError` on the first violation."""
    violations = validate(report)
    if violations:
        raise ReportContractError(violations[0] + f" (+{len(violations) - 1} more)" if len(violations) > 1 else violations[0])
