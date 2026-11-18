"""Pytest configuration and fixtures."""

import os
import pytest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(autouse=True)
def isolate_environment():
    """Restore os.environ after every test.

    Modules here call dotenv.load_dotenv() while running, which publishes real
    keys and model names into the shared process environment. Anything that
    then reads an env-backed default - cost_dashboard.os_model_default() is one
    - observes whatever an earlier test happened to load, so suite results
    depend on file order instead of on the code under test.
    """
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def sample_sources():
    """Sample source data for testing."""
    return [
        {
            "title": "Climate Change Effects on Agriculture",
            "url": "https://example.com/climate",
            "authors": ["John Smith", "Jane Doe"],
            "publication_date": "2024-01-15",
        },
        {
            "title": "Machine Learning in Healthcare",
            "url": "https://example.org/ml-health",
            "authors": ["Alice Johnson"],
            "publication_date": "2023-06-20",
        },
        {
            "title": "Quantum Computing Advances",
            "url": "https://research.edu/quantum",
        },
    ]


@pytest.fixture
def sample_query():
    """Sample research query."""
    return "effects of climate change on global agriculture production"
