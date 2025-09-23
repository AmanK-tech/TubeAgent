from __future__ import annotations

"""
# YouTube Token Allocation Implementation Plan

## Phase 1: Core Classification System Setup

### 1.1 Video Length Classification
**TODO:** Create duration-based categorization logic
- Extract video duration from YouTube API metadata
- Map durations to categories: 0-10min (SHORT), 10-30min (MEDIUM), 30-60min (LONG), 60+min (EXTENDED)
- Handle edge cases like live streams or unavailable duration data
- Set base token allocations: SHORT(250), MEDIUM(500), LONG(850), EXTENDED(1200)

### 1.2 Query Type Detection System  
**TODO:** Build pattern recognition for query complexity
- Create regex pattern library for simple queries ("what is this about", "who is mentioned")
- Develop complex query patterns ("compare X with Y", "analyze the arguments")
- Build analytical query detection ("historical context", "critical evaluation")
- Implement fallback to STANDARD type when patterns don't match
- Test pattern accuracy with sample YouTube queries

### 1.3 Content Type Detection
**TODO:** Analyze video metadata for content classification
- Extract keywords from video titles and descriptions
- Create keyword dictionaries for each content type (educational, technical, news, entertainment, review, discussion)
- Implement scoring system to determine dominant content type
- Handle multi-category videos (e.g., educational entertainment)
- Set content multipliers: technical(1.3x), educational(1.2x), news(1.1x), discussion(1.0x), review(0.9x), entertainment(0.8x)

## Phase 2: Query Analysis Engine

### 2.1 Depth Modifier Detection
**TODO:** Parse user queries for depth indicators
- Build comprehensive keyword list: brief/quick/short (0.7x), detailed/comprehensive (1.3x), in-depth/extensive (1.6x)
- Implement keyword priority system (analytical keywords override basic ones)
- Handle conflicting depth signals in same query
- Default to 1.0x when no depth indicators found

### 2.2 Context Enhancement
**TODO:** Leverage additional YouTube metadata
- Incorporate video category from YouTube API
- Factor in channel type/niche for better content classification
- Use video tags and chapters for enhanced context
- Consider video engagement metrics for complexity assessment

## Phase 3: Token Calculation Engine

### 3.1 Multi-Factor Token Computation
**TODO:** Implement the core allocation formula
- Base allocation × Query multiplier × Depth modifier × Content multiplier
- Set query multipliers: SIMPLE(0.6x), STANDARD(1.0x), COMPLEX(1.4x), ANALYTICAL(1.8x)
- Add bounds checking (minimum 100 tokens, maximum 3000 tokens)
- Round to reasonable token increments

### 3.2 Dynamic Adjustment Logic
**TODO:** Build adaptive allocation system
- Implement transcript length consideration (longer transcripts may need more tokens)
- Add user preference learning (track if users find responses too long/short)
- Create override mechanisms for special cases
- Build testing framework for allocation accuracy

## Phase 4: Integration Points

### 4.1 YouTube API Integration
**TODO:** Connect classification system to video data
- Fetch video duration, title, description, category from YouTube API
- Handle API rate limits and error responses
- Cache video metadata to avoid repeated API calls
- Implement fallback when metadata is incomplete

### 4.2 Query Processing Pipeline
**TODO:** Integrate token allocation into existing query flow
- Position token calculation after query receipt but before transcript processing
- Pass calculated tokens to response generation system
- Implement logging for allocation decisions and outcomes
- Add debugging mode to show allocation breakdown to developers

## Phase 5: Quality Assurance & Optimization

### 5.1 Allocation Accuracy Testing
**TODO:** Validate token allocation effectiveness
- Create test dataset of YouTube videos across all categories and lengths
- Generate sample queries of varying complexity
- Measure response quality vs token allocation
- A/B test different multiplier values

### 5.2 Performance Monitoring
**TODO:** Track system performance in production
- Monitor response generation time vs token allocation
- Track user satisfaction with response length
- Identify edge cases where allocation is suboptimal
- Implement feedback collection for continuous improvement

### 5.3 Calibration and Tuning
**TODO:** Refine allocation parameters based on real usage
- Analyze response quality metrics across different allocations
- Adjust base allocations and multipliers based on performance data
- Fine-tune pattern matching accuracy
- Update content type classifications as YouTube evolves

## Phase 6: Advanced Features

### 6.1 User Personalization
**TODO:** Add user-specific allocation preferences
- Learn individual user preferences for response length
- Allow manual override settings (always brief, always detailed)
- Implement user feedback incorporation
- Create user profiles for consistent allocation

### 6.2 Multi-Query Optimization
**TODO:** Handle complex multi-part queries
- Detect when queries have multiple components requiring different allocation strategies
- Implement query decomposition for optimal token distribution
- Handle follow-up questions that reference previous responses
- Balance token usage across conversation threads

## Phase 7: Maintenance & Evolution

### 7.1 Pattern Library Updates
**TODO:** Keep classification patterns current
- Regular review of query patterns as user language evolves
- Update content type keywords as YouTube content trends change
- Incorporate new video categories and formats
- Maintain pattern effectiveness through ongoing testing

### 7.2 System Scalability
**TODO:** Ensure system scales with usage growth
- Optimize pattern matching performance for high query volumes
- Implement caching strategies for repeated video analyses
- Design for horizontal scaling if needed
- Monitor and optimize memory usage of classification data

Note: This module also provides a minimal reference implementation to allocate tokens today based on the plan. See allocate_tokens().
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


# -------------------------- Core Config Defaults ----------------------------

LENGTH_THRESHOLDS_S = {
    "SHORT": (0, 10 * 60),        # 0–10 min
    "MEDIUM": (10 * 60, 30 * 60), # 10–30 min
    "LONG": (30 * 60, 60 * 60),   # 30–60 min
    "EXTENDED": (60 * 60, 10**9), # 60+ min
}

BASE_TOKENS = {
    "SHORT": 250,
    "MEDIUM": 500,
    "LONG": 850,
    "EXTENDED": 1200,
}

QUERY_MULTIPLIERS = {
    "SIMPLE": 0.6,
    "STANDARD": 1.0,
    "COMPLEX": 1.4,
    "ANALYTICAL": 1.8,
}

DEPTH_MULTIPLIERS = {
    "brief": 0.7,       # brief/quick/short
    "detailed": 1.3,    # detailed/comprehensive
    "in_depth": 1.6,    # in-depth/extensive
}

CONTENT_MULTIPLIERS = {
    "technical": 1.3,
    "educational": 1.2,
    "news": 1.1,
    "discussion": 1.0,
    "review": 0.9,
    "entertainment": 0.8,
}


# --------------------------- Helper Classifier ------------------------------

def classify_length(duration_s: Optional[float]) -> Tuple[str, int]:
    d = float(duration_s or 0)
    for name, (lo, hi) in LENGTH_THRESHOLDS_S.items():
        if lo <= d < hi:
            return name, BASE_TOKENS[name]
    return "SHORT", BASE_TOKENS["SHORT"]


_PAT_SIMPLE = re.compile(r"\b(what\s+is\s+this\s+about|who\s+is\s+mentioned|main\s+point|gist|overview|summary)\b", re.I)
_PAT_COMPLEX = re.compile(r"\b(compare|versus|vs\.?|pros\s+and\s+cons|trade[- ]?off|difference\s+between)\b", re.I)
_PAT_ANALYTICAL = re.compile(r"\b(analy[sz]e|analysis|critique|evaluate|assessment|implication|historical\s+context|critical\s+evaluation)\b", re.I)


def detect_query_type(query_text: str) -> str:
    t = (query_text or "").strip()
    if not t:
        return "STANDARD"
    if _PAT_ANALYTICAL.search(t):
        return "ANALYTICAL"
    if _PAT_COMPLEX.search(t):
        return "COMPLEX"
    if _PAT_SIMPLE.search(t):
        return "SIMPLE"
    return "STANDARD"


def detect_depth_modifier(query_text: str) -> float:
    t = (query_text or "").lower()
    # Priority: in-depth > detailed > brief
    if re.search(r"\b(in[- ]?depth|extensive|exhaustive|deep\s+dive|long[- ]?form)\b", t):
        return DEPTH_MULTIPLIERS["in_depth"]
    if re.search(r"\b(detailed|comprehensive|thorough)\b", t):
        return DEPTH_MULTIPLIERS["detailed"]
    if re.search(r"\b(brief|quick|short)\b", t):
        return DEPTH_MULTIPLIERS["brief"]
    return 1.0


def detect_content_type(title: Optional[str] = None, description: Optional[str] = None, category: Optional[str] = None, tags: Optional[list[str]] = None) -> str:
    text = " ".join([title or "", description or "", category or "", " ".join(tags or [])]).lower()
    scores = {k: 0 for k in CONTENT_MULTIPLIERS.keys()}

    def bump(keys: list[str], bucket: str):
        for kw in keys:
            if kw in text:
                scores[bucket] += 1

    bump(["api", "code", "developer", "programming", "algorithm", "physics", "engineering", "math"], "technical")
    bump(["lesson", "course", "lecture", "explained", "how to", "tutorial"], "educational")
    bump(["breaking", "news", "report", "press conference", "announcement"], "news")
    bump(["podcast", "interview", "discussion", "debate", "roundtable", "panel"], "discussion")
    bump(["review", "hands-on", "impressions", "unboxing"], "review")
    bump(["vlog", "comedy", "music video", "trailer", "funny", "prank", "gaming"], "entertainment")

    # choose highest score, default discussion (neutral) if tie/none
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] == 0:
        return "discussion"
    return best[0]


def _round_to_increment(n: float, inc: int = 50) -> int:
    return int(inc * round(float(n) / inc))


@dataclass
class AllocationResult:
    tokens: int
    length_category: str
    base_tokens: int
    query_type: str
    query_multiplier: float
    depth_multiplier: float
    content_type: str
    content_multiplier: float
    adjustments: Dict[str, Any]


def allocate_tokens(
    video_duration_s: Optional[float],
    query_text: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    transcript_chars: Optional[int] = None,
    min_tokens: int = 100,
    max_tokens: int = 3000,
) -> AllocationResult:
    """Compute answer token budget based on video length, query type, depth, and content type.

    This follows the plan's Phase 1–3. Returns both the token count and a breakdown.
    """
    length_category, base = classify_length(video_duration_s)
    qtype = detect_query_type(query_text)
    qmult = QUERY_MULTIPLIERS.get(qtype, 1.0)
    dmult = detect_depth_modifier(query_text)
    ctype = detect_content_type(title=title, description=description, category=category, tags=tags)
    cmult = CONTENT_MULTIPLIERS.get(ctype, 1.0)

    tokens = base * qmult * dmult * cmult

    adjustments: Dict[str, Any] = {}
    # Simple dynamic adjustment by transcript length (if available)
    if transcript_chars and transcript_chars > 0:
        if transcript_chars > 100_000:
            tokens *= 1.5
            adjustments["transcript_chars_boost"] = 1.5
        elif transcript_chars > 30_000:
            tokens *= 1.2
            adjustments["transcript_chars_boost"] = 1.2

    # Bounds + rounding
    tokens = max(min_tokens, min(max_tokens, int(tokens)))
    tokens = _round_to_increment(tokens, inc=50)

    return AllocationResult(
        tokens=tokens,
        length_category=length_category,
        base_tokens=base,
        query_type=qtype,
        query_multiplier=qmult,
        depth_multiplier=dmult,
        content_type=ctype,
        content_multiplier=cmult,
        adjustments=adjustments,
    )


# --------------------------- Integration Hints ------------------------------

def to_generation_config(alloc: AllocationResult) -> Dict[str, Any]:
    """Optional helper to translate allocation to a model config map.

    For models that support it, pass as generation_config={"max_output_tokens": alloc.tokens}.
    """
    return {"max_output_tokens": int(alloc.tokens)}


__all__ = [
    "AllocationResult",
    "allocate_tokens",
    "classify_length",
    "detect_query_type",
    "detect_depth_modifier",
    "detect_content_type",
    "to_generation_config",
]

