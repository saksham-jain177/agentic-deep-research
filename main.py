from langgraph.graph import StateGraph
from research_agent import research_web
from draft_agent import draft_tool
from joblib import Memory
import os
import json
import time

# Initialize caching with joblib
memory = Memory("cache", verbose=0)
# memory.clear()  # Don't clear on every import

# Cache entries older than this many hours are treated as misses.
# Set RESEARCH_CACHE_TTL_HOURS=0 to keep entries valid forever (old behavior).
CACHE_TTL_HOURS = float(os.getenv("RESEARCH_CACHE_TTL_HOURS", "24"))


def _cache_bucket() -> int:
    """Return a time bucket used as part of the cache key.

    joblib caches on argument values, so folding a coarse time bucket into
    the arguments expires cached research every CACHE_TTL_HOURS without a
    custom backend. Bucket 0 (TTL disabled) never changes.
    """
    if CACHE_TTL_HOURS <= 0:
        return 0
    return int(time.time() // (CACHE_TTL_HOURS * 3600))

# Optional: Import vector store for summary storage
VECTOR_STORE_ENABLED = os.getenv("ENABLE_VECTOR_STORE", "false").lower() == "true"
vector_store = None
if VECTOR_STORE_ENABLED:
    try:
        from vector_store import get_vector_store
        vector_store = get_vector_store()
    except Exception:
        VECTOR_STORE_ENABLED = False

# Define the research node to update the state
@memory.cache
def fetch_research_data(query: str, deep_research: bool = False, language: str = 'en', cache_bucket: int = 0) -> list:
    """Fetch research data using the research tool with caching."""
    # Map language names to codes
    lang_map = {'english': 'en', 'spanish': 'es', 'german': 'de'}
    lang_code = lang_map.get(language.lower(), 'en')

    # Call research_web directly: Tool.run() forwards unknown kwargs to the
    # callback manager, not to the wrapped function, so deep_research and
    # language passed via .run() were silently ignored.
    result = research_web(query, deep_research, lang_code)
    if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict) and "error" in result[0]:
        raise Exception(f"Research failed: {result[0]['error']}")
    return result

def research_node(state):
    """Fetch research data and update the state."""
    query = state["query"]
    deep_research = state.get("deep_research", False)
    language = state.get("language", "english")
    research_data = fetch_research_data(query, deep_research, language, cache_bucket=_cache_bucket())
    state["research"] = research_data
    return state

# Define the draft node to use research data and update the state
def draft_node(state):
    """Draft a summary using research data and update the state."""
    research_data = state["research"]
    if not isinstance(research_data, list):
        raise Exception("Research data is not in the expected format (list required)")
    
    # Extract all parameters from state
    deep_research = state.get("deep_research", False)
    target_word_count = state.get("target_word_count", 1000)
    writing_style = state.get("writing_style", "academic").lower()  # Normalize to lowercase
    citation_format = state.get("citation_format", "APA")
    language = state.get("language", "english").lower()  # Normalize to lowercase
    
    result = draft_tool.invoke({
        "data": research_data,
        "deep_research": deep_research,
        "target_word_count": target_word_count,
        "writing_style": writing_style,
        "citation_format": citation_format,
        "language": language,
        "retries": 3,
        "delay": 5
    })
    if "Error drafting response" in result:
        raise Exception(result)
    
    # Store summary in vector store if enabled
    if VECTOR_STORE_ENABLED and vector_store:
        try:
            # Parse the JSON result if it's a string
            summary_data = None
            if isinstance(result, str) and result.strip().startswith('{'):
                try:
                    summary_data = json.loads(result)
                except json.JSONDecodeError:
                    pass
            
            if summary_data:
                # Map language to code
                lang_map = {'english': 'en', 'spanish': 'es', 'german': 'de'}
                lang_code = lang_map.get(language.lower(), 'en')
                
                # Store the summary sections
                vector_store.add_research_data(
                    query=state["query"],
                    sources=[],  # Don't duplicate sources, they're already stored
                    summary_sections=summary_data,
                    language=lang_code
                )
                print("Stored summary in vector store")
        except Exception as e:
            print(f"Failed to store summary in vector DB: {e}")
    
    state["draft"] = result
    return state

# Initialize the graph
workflow = StateGraph(dict)

# Add nodes to the workflow
workflow.add_node("research", research_node)
workflow.add_node("draft", draft_node)

# Define edges
workflow.add_edge("research", "draft")

# Set entry and finish points
workflow.set_entry_point("research")
workflow.set_finish_point("draft")

# Compile the workflow
app = workflow.compile()

# Function to run the research system
def run_research(query: str, deep_research: bool = False, target_word_count: int = 1000, writing_style: str = "academic", citation_format: str = "APA", language: str = "english") -> tuple:
    """Run the research workflow and return results."""
    # Normalize style and language to lowercase to align with draft_agent expectations
    writing_style = writing_style.lower()
    language = language.lower()
    input_dict = {
        "query": query,
        "deep_research": deep_research,
        "target_word_count": target_word_count,
        "writing_style": writing_style,
        "citation_format": citation_format,
        "language": language
    }
    
    try:
        result = app.invoke(input_dict)
        # Ensure result is a dictionary and extract outputs
        if not isinstance(result, dict):
            raise Exception(f"Workflow returned unexpected type: {type(result)}")
        research_data = result.get("research", [])
        draft_response = result.get("draft", "Error: Draft not generated")
        return research_data, draft_response
    except Exception as e:
        return [], f"Workflow failed: {str(e)}"

# Example usage
if __name__ == "__main__":
    query = "why sugar is bad for your health"
    deep_research = False
    target_word_count = 1000
    research_data, response = run_research(query, deep_research, target_word_count)
    print("Research Data:", research_data)
    print("Research Response:", response)
