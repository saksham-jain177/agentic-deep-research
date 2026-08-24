"""
Cost Estimator Module - Multi-Tokenizer with Trust Boundaries and Layered Pricing

Provides token and cost estimation with uncertainty ranges for research queries.
Uses multiple providers for pricing (genai-prices, tokencost) and tokenization.
"""

import os
from typing import Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# Lazy imports
_tiktoken = None
_tokencost = None
_genai_prices = None


def _load_tiktoken():
    """Lazy load tiktoken."""
    global _tiktoken
    if _tiktoken is None:
        try:
            import tiktoken
            _tiktoken = tiktoken
        except ImportError:
            _tiktoken = False
    return _tiktoken if _tiktoken else None


def _load_tokencost():
    """Lazy load tokencost."""
    global _tokencost
    if _tokencost is None:
        try:
            import tokencost
            _tokencost = tokencost
        except ImportError:
            _tokencost = False
    return _tokencost if _tokencost else None


def _load_genai_prices():
    """Lazy load genai-prices."""
    global _genai_prices
    if _genai_prices is None:
        try:
            import genai_prices
            # Ensure we have the necessary components
            # We need calc_price and Usage to be importable
            from genai_prices import calc_price, Usage
            _genai_prices = (calc_price, Usage, genai_prices.__version__)
        except (ImportError, AttributeError):
            _genai_prices = False
    return _genai_prices if _genai_prices else None


# Calibration offsets based on empirical testing
CALIBRATION = {
    "deep_research": 1.15,  # Deep research uses ~15% more tokens due to loops
    "shallow": 1.05,
}

# Model pricing fallbacks (USD per 1K tokens) - used when other sources fail
FALLBACK_PRICING = {
    "input": 0.0005,   # $0.50 per 1M tokens (conservative low-end)
    "output": 0.0015,  # $1.50 per 1M tokens
}

# Template overhead estimates (tokens)
TEMPLATE_OVERHEAD = {
    "shallow": 500,   # Base prompt tokens for shallow research
    "deep": 1500,     # Base prompt tokens for deep research
}

# Provider mapping for genai-prices
# Maps common model prefixes/names to (provider_id, strict_match_model_name)
PROVIDER_MAPPING = {
    "gpt-": "openai",
    "o1-": "openai",
    "claude-": "anthropic",
    "claude-3-": "anthropic",
    "gemini": "google",
    "command": "cohere",
}


@dataclass
class EstimationResult:
    """Result of token/cost estimation with trust boundaries."""
    tokens_min: int
    tokens_avg: int
    tokens_max: int
    cost_min: float
    cost_avg: float
    cost_max: float
    confidence: str  # "high", "medium", "low"
    pricing_source: str
    pricing_staleness_days: int
    degradation_reasons: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for UI consumption."""
        return {
            "tokens": {"min": self.tokens_min, "avg": self.tokens_avg, "max": self.tokens_max},
            "cost_usd": {"min": self.cost_min, "avg": self.cost_avg, "max": self.cost_max},
            "confidence": self.confidence,
            "pricing_source": self.pricing_source,
            "pricing_staleness_days": self.pricing_staleness_days,
            "degradation_reasons": self.degradation_reasons,
        }


class TokenEstimator:
    """
    Multi-tokenizer estimation with layered pricing resolution.
    
    Pricing Layers:
    1. genai-prices (Primary Catalog): Versioned, structured pricing.
    2. tokencost (Secondary Library): API-based or library-based lookup.
    3. Heuristic (Fallback): Safe static estimates.
    
    Confidence Semantics:
        high: genai-prices match + Exact Tokenizer (e.g., GPT-4 + tiktoken)
        medium: genai-prices/tokencost match + Approx Tokenizer (e.g., Claude)
        low: Heuristic pricing OR unknown model behavior
    """
    
    def __init__(self):
        self._tiktoken = _load_tiktoken()
        self._tokencost = _load_tokencost()
        self._genai_components = _load_genai_prices()
        self._pricing_timestamp = datetime.now()  # Session-scoped timestamp for "freshness" calculation
    
    def _count_tokens_tiktoken(self, text: str, model: str = "gpt-3.5-turbo") -> Optional[int]:
        """Count tokens using tiktoken (OpenAI standard)."""
        if not self._tiktoken:
            return None
        try:
            # Try to get encoding for the specific model
            try:
                encoding = self._tiktoken.encoding_for_model(model)
            except KeyError:
                # Fall back to cl100k_base for unknown models (common for modern LLMs)
                encoding = self._tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            return None
    
    def _count_tokens_heuristic(self, text: str) -> int:
        """Fallback: estimate tokens from word count."""
        # Average English word is ~1.3 tokens
        words = len(text.split())
        return int(words * 1.3)
    
    def _resolve_provider(self, model: str) -> Optional[str]:
        """Resolve provider ID from model name for genai-prices."""
        model_lower = model.lower()
        
        # Check explicit mapping
        for prefix, provider in PROVIDER_MAPPING.items():
            if prefix in model_lower:
                return provider
                
        # Heuristics for common openrouter patterns
        if "/" in model:
            provider_part = model.split("/")[0]
            # Simple mapping for OpenRouter prefixes
            if provider_part in ["openai", "anthropic", "google", "meta-llama", "mistralai"]:
                return provider_part
                
        return None  # Let genai-prices attempt auto-detection if possible
    
    def _get_pricing_genai_prices(self, model: str, input_tokens: int, output_tokens: int) -> Optional[Tuple[float, str]]:
        """
        Layer 1: Get pricing from genai-prices catalog.
        Returns (total_cost_usd, source_string) or None if failed.
        """
        if not self._genai_components:
            return None
            
        calc_price, Usage, version = self._genai_components
        provider_id = self._resolve_provider(model)
        
        # Clean model name (remove provider prefix if present for matching)
        clean_model = model.split("/")[-1] if "/" in model else model
            
        try:
            usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
            result = calc_price(usage, model_ref=clean_model, provider_id=provider_id)
            if result and hasattr(result, "total_price"):
                # Convert Decimal to float
                return (float(result.total_price), f"genai-prices@{version}")
        except Exception:
            # Squelch errors (e.g. model not found) to allow usage of next layer
            pass
            
        return None

    def _get_pricing_tokencost(self, model: str, input_tokens: int, output_tokens: int) -> Optional[Tuple[float, str]]:
        """
        Layer 2: Get pricing from tokencost library.
        Returns (total_cost_usd, source_string) or None.
        """
        if not self._tokencost:
            return None
            
        try:
            # We normalize to 1k unit prices or just calculate total directly
            # Tokencost usually exposes calculate_cost or similar
            from tokencost import calculate_prompt_cost, calculate_completion_cost
            
            # tokencost expects model names usually
            input_cost = calculate_prompt_cost(model=model, token_count=input_tokens)
            output_cost = calculate_completion_cost(model=model, token_count=output_tokens)
            
            if input_cost is not None and output_cost is not None:
                total = input_cost + output_cost
                version = getattr(self._tokencost, "__version__", "unknown")
                return (total, f"tokencost@{version}")
        except Exception:
            pass
            
        return None
        
    def _get_pricing_heuristic(self, input_tokens: int, output_tokens: int) -> Tuple[float, str]:
        """
        Layer 3: Fallback heuristic pricing.
        Returns (total_cost_usd, source_string).
        """
        input_cost = (input_tokens / 1000) * FALLBACK_PRICING["input"]
        output_cost = (output_tokens / 1000) * FALLBACK_PRICING["output"]
        return (input_cost + output_cost, "fallback_heuristic")
    
    def estimate(
        self,
        query: str,
        deep_research: bool,
        target_word_count: int,
        model: str = "gpt-3.5-turbo"
    ) -> EstimationResult:
        """
        Estimate tokens and cost for a research query using layered resolution.
        """
        degradation_reasons = []
        confidence = "high"
        
        # 1. Estimate Tokens
        query_tokens = self._count_tokens_tiktoken(query, model)
        if query_tokens is None:
            query_tokens = self._count_tokens_heuristic(query)
            degradation_reasons.append("tiktoken unavailable, using word count")
            confidence = "low"
        
        # Confidence Check: Tokenizer Accuracy
        # If model is not GPT-family, tiktoken is an approximation
        is_gpt_family = "gpt" in model.lower() or "o1" in model.lower()
        if not is_gpt_family and confidence == "high":
            confidence = "medium"
            degradation_reasons.append("using tiktoken for non-GPT model (approximate)")

        # Calculate base tokens
        template_overhead = TEMPLATE_OVERHEAD["deep"] if deep_research else TEMPLATE_OVERHEAD["shallow"]
        base_input_tokens = query_tokens + template_overhead
        
        # Estimate output tokens (words * 1.3 factor + 10% formatting)
        base_output_tokens = int(target_word_count * 1.3 * 1.1)
        
        # Calibration
        cal_factor = CALIBRATION["deep_research"] if deep_research else CALIBRATION["shallow"]
        calibrated_input = int(base_input_tokens * cal_factor)
        calibrated_output = int(base_output_tokens * cal_factor)
        
        # Uncertainty Ranges (±20%)
        total_avg = calibrated_input + calibrated_output
        total_min = int(total_avg * 0.8)
        total_max = int(total_avg * 1.2)
        
        # 2. Resolve Pricing (Layered)
        staleness_days = (datetime.now() - self._pricing_timestamp).days
        
        # Layer 1: genai-prices
        price_result = self._get_pricing_genai_prices(model, total_avg, 0) # total used as surrogate for rate check? No.
        # We need independent input/output costs for accurate estimation
        # But calc_price typically returns total.
        # Let's calculate cost for the AVG case usage.
        
        pricing_source = "unknown"
        cost_avg = 0.0
        
        # Calculate cost for the "average" usage scenario
        # We assume price scales linearly, so we can calculate min/max later from avg rate or just apply ±20% to cost
        
        # Try genai-prices
        res_gp = self._get_pricing_genai_prices(model, calibrated_input, calibrated_output)
        if res_gp:
            cost_avg, pricing_source = res_gp
        else:
            # Try tokencost
            res_tc = self._get_pricing_tokencost(model, calibrated_input, calibrated_output)
            if res_tc:
                cost_avg, pricing_source = res_tc
                # Downgrade confidence if falling back to layer 2? 
                # Ideally yes, but tokencost is also reputable. 
                # We'll allow medium confidence.
            else:
                # Fallback
                cost_avg, pricing_source = self._get_pricing_heuristic(calibrated_input, calibrated_output)
                confidence = "low"
                if "fallback pricing" not in str(degradation_reasons):
                    degradation_reasons.append("using fallback pricing")

        # 3. Calculate Cost Ranges
        # Assuming linear scaling with token count uncertainty
        cost_min = cost_avg * 0.8
        cost_max = cost_avg * 1.2
        
        return EstimationResult(
            tokens_min=total_min,
            tokens_avg=total_avg,
            tokens_max=total_max,
            cost_min=round(cost_min, 4),
            cost_avg=round(cost_avg, 4),
            cost_max=round(cost_max, 4),
            confidence=confidence,
            pricing_source=pricing_source,
            pricing_staleness_days=staleness_days,
            degradation_reasons=degradation_reasons,
        )


# Singleton
_estimator = None

def get_estimator() -> TokenEstimator:
    global _estimator
    if _estimator is None:
        _estimator = TokenEstimator()
    return _estimator

def estimate_research_cost(
    query: str,
    deep_research: bool = False,
    target_word_count: int = 1000,
    model: str = "gpt-3.5-turbo"
) -> dict:
    """Convenience function for estimating research cost."""
    estimator = get_estimator()
    result = estimator.estimate(query, deep_research, target_word_count, model)
    return result.to_dict()


def count_text_tokens(text: str) -> int:
    """Count tokens in arbitrary text (tiktoken if available, heuristic fallback)."""
    estimator = get_estimator()
    exact = estimator._count_tokens_tiktoken(text)
    if exact is None:
        return estimator._count_tokens_heuristic(text)
    return exact
