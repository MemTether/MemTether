"""entity_extractor.py — smart entity extraction using language detection + GLiNER/regex.

Chinese text → regex (existing logic, works well for 中文)
English text → GLiNER (pretrained NER model, excellent for English)
Mixed → run both, merge results

GLiNER is an optional dependency (pip install gliner). If not installed,
falls back to regex-only (current behavior unchanged).
"""
import os
import re

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_ALPHA_RE = re.compile(r'[a-zA-Z]')

def detect_language(text):
    """Detect primary language: 'zh', 'en', or 'mixed'.

    Counts CJK chars vs Latin chars. If both > 20% it's 'mixed'.
    """
    if not text:
        return 'en'
    cjk_count = len(_CJK_RE.findall(text))
    alpha_count = len(_ALPHA_RE.findall(text))
    total = cjk_count + alpha_count
    if total == 0:
        return 'en'  # numbers/symbols only, default to en
    cjk_ratio = cjk_count / total
    if cjk_ratio > 0.6:
        return 'zh'
    elif cjk_ratio < 0.2:
        return 'en'
    else:
        return 'mixed'


# ---------------------------------------------------------------------------
# GLiNER (optional)
# ---------------------------------------------------------------------------

_gliner_model = None
_gliner_loaded = False

# Entity labels relevant to agent memory
_GLINER_LABELS = [
    "software", "service", "tool", "database", "person", "organization",
    "file path", "port number", "version", "technology", "system", "config",
    "server", "container", "framework", "library", "API",
]

def _load_gliner():
    """Lazy-load GLiNER model. Returns None if not installed."""
    global _gliner_model, _gliner_loaded
    if _gliner_loaded:
        return _gliner_model
    _gliner_loaded = True
    try:
        from gliner import GLiNER
        _gliner_model = GLiNER.from_pretrained("urchade/gliner_small-v2.1")
    except ImportError:
        pass  # gliner not installed
    except Exception:
        _gliner_model = None
    return _gliner_model


def _extract_gliner(text):
    """Extract entities using GLiNER. Returns list[dict] or None."""
    model = _load_gliner()
    if model is None:
        return None
    try:
        results = model.predict_entities(text, _GLINER_LABELS, threshold=0.4)
        entities = []
        seen = set()
        for e in results:
            text_val = e.get("text", "").strip()
            label = e.get("label", "")
            score = e.get("score", 0)
            if not text_val or len(text_val) < 2:
                continue
            key = text_val.lower()
            if key in seen:
                continue
            seen.add(key)
            entities.append({
                "entity": text_val,
                "attr": label,  # use NER label as attribute
                "score": score,
                "source": "gliner",
            })
        return entities if entities else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Regex extraction (existing logic, for Chinese)
# ---------------------------------------------------------------------------

# Import from predicates.py (same patterns)
# 2026-11-07 fix: use predicates.extract_predicates as the canonical regex
# extraction (it has the solid-entity filter, generic-word blocklist, ASCII/CJK
# split, and English patterns). entity_extractor delegates to it and just
# reformats the output. This avoids pattern drift between the two modules.

def _extract_regex(text):
    """Extract entities via predicates.extract_predicates (canonical patterns).

    Returns list[dict] with keys: entity, attr, source='regex', or None.
    """
    try:
        from predicates import extract_predicates
        preds = extract_predicates(text)
        if not preds:
            return None
        return [{"entity": p["e"], "attr": p["a"], "source": "regex"}
                for p in preds[:5]]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract(text):
    """Smart entity extraction: language detection → GLiNER or regex → merge.

    Returns list[dict] with keys: entity, attr, source.
    Each dict has 'source' field: 'gliner', 'regex', or both if merged.
    Returns None if no entities found.

    Design (2026-11-08 fix): for ENGLISH text, regex is a last-resort fallback
    only when GLiNER is truly unavailable. The rationale: the plain regex
    patterns are designed for Chinese "X的Y" structures and produce noisy
    pseudo-entities for English prose ("deploy server is running" → 'server').
    If GLiNER is not installed, returning None here lets the caller (gateway)
    try the optional LLM path before falling back to regex. This gives the
    LLM a chance to extract better English entities than the Chinese regex.
    """
    if not text or len(text.strip()) < 3:
        return None

    lang = detect_language(text)
    gliner_results = None
    regex_results = None

    if lang == 'en':
        # English: GLiNER first. If unavailable, let the caller fall through
        # to LLM/regex — do NOT use the Chinese-biased regex as primary.
        gliner_results = _extract_gliner(text)
        if not gliner_results:
            # Check if gliner is actually installed; if not, return None so
            # gateway can try LLM. If gliner IS installed but returned nothing,
            # we still fall to regex (the model genuinely found nothing).
            if not is_gliner_available():
                return None
            regex_results = _extract_regex(text)
    elif lang == 'zh':
        # Chinese: regex first, fallback to GLiNER
        regex_results = _extract_regex(text)
        if not regex_results:
            gliner_results = _extract_gliner(text)
    else:
        # Mixed: run both, merge
        gliner_results = _extract_gliner(text)
        regex_results = _extract_regex(text)

    # Merge results
    all_entities = []
    seen = set()

    # GLiNER results first (higher confidence for English)
    if gliner_results:
        for e in gliner_results:
            key = e["entity"].lower()
            if key not in seen:
                seen.add(key)
                all_entities.append(e)

    # Regex results (better for Chinese, skip if duplicate)
    if regex_results:
        for e in regex_results:
            key = e["entity"].lower()
            if key not in seen:
                seen.add(key)
                all_entities.append(e)

    # Deduplicate against existing fact_entities (caller handles this via INSERT OR IGNORE)
    return all_entities[:8] if all_entities else None


def is_gliner_available():
    """Check if GLiNER is installed and loadable."""
    try:
        import gliner
        return True
    except ImportError:
        return False
