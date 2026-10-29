#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aggregation-Aware Retrieval (AAR) for MemTether
================================================
Architecture-level contribution: the retrieval pipeline itself detects
aggregation-type questions and performs deterministic computation on
structured evidence, rather than relying on the LLM to "figure it out".

Three-step pipeline:
  1. Question Classifier: detect computation type (count/sum/duration/diff)
  2. Multi-Query Entity Retrieval: decompose into targeted sub-queries
  3. Deterministic Extraction + Computation: regex + Python, not LLM

The output is a "computed context" injected into the LLM prompt so the
LLM verifies rather than computes.
"""
import re, sys, os, json, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============ Step 1: Question Classifier ============
def classify_question(question):
    """Detect what type of aggregation the question requires.
    Returns: (type, entity_hint) or (None, None)
    """
    q = question.lower().strip()

    # Count questions
    if re.search(r'how many\b|number of\b|count\b|how much\b.*\b(total|altogether)', q):
        # Extract target entity/noun
        entity = _extract_target_entity(question)
        if any(kw in q for kw in ['book', 'movie', 'film', 'restaurant', 'place', 'item',
                                    'clothing', 'shirt', 'jacket', 'project', 'task',
                                    'song', 'album', 'game', 'dish', 'recipe']):
            return 'count_items', entity
        return 'count_generic', entity

    # Sum questions
    if re.search(r'total\b|sum\b|combined\b|altogether\b|overall\b|in total\b|add up\b', q):
        entity = _extract_target_entity(question)
        return 'sum', entity

    # Duration questions
    if re.search(r'how long\b|duration\b|how many (days|weeks|months|years)\b', q):
        entity = _extract_target_entity(question)
        return 'duration', entity

    # Difference/comparison
    if re.search(r'difference\b|how much (more|less|longer|shorter)\b|compared to\b', q):
        entity = _extract_target_entity(question)
        return 'difference', entity

    # Temporal (first/last/when)
    if re.search(r'when (did|was|is|were)\b|what (date|time|year)\b|first time\b|last time\b', q):
        entity = _extract_target_entity(question)
        return 'temporal', entity

    return None, None


def _extract_target_entity(question):
    """Extract the subject/entity the question is about (for sub-query generation)."""
    # Pattern: "How many X did/does [Name] ..." -> Name
    m = re.search(r'(?:did|does|was|is|were)\s+([A-Z][a-z]+)', question)
    if m:
        return m.group(1)
    # Pattern: "[Name]'s ..." -> Name
    m = re.search(r"([A-Z][a-z]+)(?:'s)?\s", question)
    if m:
        return m.group(1)
    # Pattern: "How many ... [noun]" -> noun
    m = re.search(r'how many\s+([a-z]+)', question.lower())
    if m:
        return m.group(1)
    return None


# ============ Step 2: Multi-Query Decomposition ============
def generate_sub_queries(question, comp_type, entity, max_queries=4):
    """Generate multiple targeted sub-queries for comprehensive evidence retrieval."""
    queries = [question]  # always include original

    if comp_type == 'count_items' or comp_type == 'count_generic':
        # Extract the item being counted
        item_m = re.search(r'how many\s+((?:\w+\s+){0,2}\w+)', question.lower())
        item = item_m.group(1).strip() if item_m else 'items'
        if entity:
            queries.append('%s %s' % (entity, item))
            queries.append('%s mentioned %s' % (entity, item))
            queries.append('%s list of %s' % (entity, item))
        else:
            queries.append('list of %s' % item)
            queries.append('mentioned %s' % item)

    elif comp_type == 'sum':
        # Look for units in question
        unit_m = re.search(r'(dollars?|km|miles?|hours?|pages?|kg|pounds?)', question.lower())
        unit = unit_m.group(1) if unit_m else ''
        if entity:
            queries.append('%s %s' % (entity, unit) if unit else entity)
            queries.append('%s total %s' % (entity, unit) if unit else '%s total' % entity)
        elif unit:
            queries.append('total %s' % unit)
            queries.append('sum of %s' % unit)

    elif comp_type == 'duration':
        if entity:
            queries.append('%s started' % entity)
            queries.append('%s finished' % entity)
            queries.append('%s completed' % entity)

    elif comp_type == 'temporal':
        if entity:
            queries.append('%s first' % entity)
            queries.append('%s happened' % entity)

    # Deduplicate and limit
    seen = set()
    unique = []
    for q in queries:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            unique.append(q)
    return unique[:max_queries]


# ============ Step 3: Structured Extraction ============
def extract_structured_data(evidence_texts, comp_type, entity=None):
    """Extract structured data (numbers, dates, items) from evidence texts."""
    full_text = '\n'.join(evidence_texts)

    if comp_type in ('count_items', 'count_generic'):
        return _extract_countable_items(full_text, evidence_texts, comp_type)

    elif comp_type in ('sum', 'difference'):
        return _extract_numbers_for_sum(full_text, evidence_texts)

    elif comp_type == 'duration':
        return _extract_duration_data(full_text, evidence_texts)

    elif comp_type == 'temporal':
        return _extract_temporal_data(full_text, evidence_texts)

    return None


def _extract_countable_items(full_text, evidence_texts, comp_type):
    """Extract unique countable items from evidence."""
    # Try to find list-like patterns
    items = set()
    details = []

    # Pattern 1: numbered or bulleted lists in evidence
    for text in evidence_texts:
        for line in text.split('\n'):
            line = line.strip()
            # Count explicit enumerations: "1. X", "2. Y" or "- X", "- Y"
            if re.match(r'^\d+[\.\)]\s', line) or re.match(r'^[-*]\s', line):
                content = re.sub(r'^\d+[\.\)]\s|^[-*]\s', '', line).strip()
                if len(content) > 3:
                    items.add(content)

    # Pattern 2: items associated with intentional actions (pick up, return, etc.)
    # Key improvement over naive counting: filter by ACTION relevance
    action_pats = [
        r'(?:need to|going to|have to|will|must|should)\s+(?:pick up|return|buy|get|exchange|collect)\s+'
        r'(?:my |the |a |an |some )?'
        r'([A-Za-z][A-Za-z\s\-]{2,40}?)(?:\s+(?:from|at|to|for)\s+\w+|\s*[,;.]|$)',
        r'(?:picked up|returned|bought|got|exchanged|collected)\s+'
        r'(?:my |the |a |an |some )?'
        r'([A-Za-z][A-Za-z\s\-]{2,40}?)(?:\s+(?:from|at|to|for)\s+\w+|\s*[,;.]|$)',
    ]
    for text in evidence_texts:
        for pat in action_pats:
            for m in re.finditer(pat, text, re.IGNORECASE):
                item = m.group(1).strip()
                item = re.sub(r'\s+(?:from|at|to|for|on|in|the|a|an)\s*$', '', item, flags=re.IGNORECASE).strip()
                if len(item) > 2 and item.lower() not in ('it', 'this', 'that', 'the', 'a', 'them', 'some'):
                    items.add(item)
                    details.append('ACTION: %s' % m.group(0)[:80])

    # Pattern 3: specific item categories
    item_patterns = {
        'book': r'\b(book|novel|textbook|paperback|hardcover)\b',
        'movie': r'\b(movie|film|cinema|show)\b',
        'restaurant': r'\b(restaurant|cafe|diner|bistro|eatery)\b',
        'clothing': r'\b(jacket|shirt|pants|shoes|boots|dress|coat|sweater|socks|hat|scarf)\b',
        'song': r'\b(song|track|album|single)\b',
        'place': r'\b(park|beach|mountain|city|country|island|lake|river)\b',
    }
    for cat, pat in item_patterns.items():
        found = set(re.findall(pat, full_text, re.IGNORECASE))
        if found:
            items.update(found)

    if items:
        return {'type': 'count', 'items': sorted(items), 'count': len(items), 'details': details[:20]}
    return None


def _extract_numbers_for_sum(full_text, evidence_texts):
    """Extract numbers with units for sum/difference computation."""
    numbers = []
    for text in evidence_texts:
        for m in re.finditer(
            r'(\d[\d,]*(?:\.\d+)?)\s*'
            r'(km|kilometers?|miles?|meters?|hours?|hrs?|minutes?|mins?|'
            r'dollars?|USD|\$[\d,]+|€[\d,]+|pounds?|kg|grams?|'
            r'pages?|books?|items?|points?|%)',
            text, re.IGNORECASE
        ):
            raw_num = m.group(1).replace(',', '')
            try:
                val = float(raw_num) if '.' in raw_num else int(raw_num)
            except ValueError:
                continue
            unit = m.group(2)
            start = max(0, m.start() - 40)
            context = text[start:m.end() + 40].replace('\n', ' ')
            numbers.append({'value': val, 'unit': unit, 'context': context})

    if numbers:
        # Group by unit
        by_unit = defaultdict(list)
        for n in numbers:
            unit_lower = n['unit'].lower()
            by_unit[unit_lower].append(n)
        return {'type': 'numbers', 'by_unit': dict(by_unit), 'all': numbers[:50]}
    return None


def _extract_duration_data(full_text, evidence_texts):
    """Extract start/end references for duration computation."""
    dates = []
    for text in evidence_texts:
        # ISO dates
        for m in re.finditer(r'\d{4}-\d{2}-\d{2}', text):
            dates.append({'raw': m.group(0), 'type': 'iso', 'context': text[max(0,m.start()-40):m.end()+40]})
        # Month names
        for m in re.finditer(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?', text, re.IGNORECASE):
            dates.append({'raw': m.group(0), 'type': 'month_day', 'context': text[max(0,m.start()-40):m.end()+40]})
        # Relative durations
        for m in re.finditer(r'(\d+)\s*(days?|weeks?|months?|years?)', text, re.IGNORECASE):
            dates.append({'raw': m.group(0), 'type': 'duration', 'value': int(m.group(1)),
                         'unit': m.group(2).lower(), 'context': text[max(0,m.start()-40):m.end()+40]})

    if dates:
        return {'type': 'temporal', 'dates': dates[:50]}
    return None


def _extract_temporal_data(full_text, evidence_texts):
    """Extract temporal references (dates, times) for temporal questions."""
    return _extract_duration_data(full_text, evidence_texts)


# ============ Step 4: Deterministic Computation ============
def compute_answer(comp_type, structured_data, question):
    """Perform deterministic computation on structured data.
    Returns (computed_answer_str, computation_detail) or (None, None).
    """
    if structured_data is None:
        return None, None

    if structured_data['type'] == 'count':
        items = structured_data['items']
        count = structured_data['count']
        detail = 'Items found: %s' % ', '.join(items[:10])
        return str(count), detail

    elif structured_data['type'] == 'numbers':
        # Try to sum per unit, pick the unit most relevant to the question
        q_lower = question.lower()
        by_unit = structured_data['by_unit']

        # Find best matching unit
        best_unit = None
        best_match_len = 0
        for unit, nums in by_unit.items():
            if unit.lower() in q_lower:
                if len(unit) > best_match_len:
                    best_unit = unit
                    best_match_len = len(unit)

        if best_unit is None and by_unit:
            # Use the unit with most numbers
            best_unit = max(by_unit.keys(), key=lambda k: len(by_unit[k]))

        if best_unit and by_unit[best_unit]:
            nums = by_unit[best_unit]
            total = sum(n['value'] for n in nums)
            detail = 'Values (%s): %s = %s' % (
                best_unit,
                ' + '.join(str(n['value']) for n in nums[:10]),
                total)
            return str(total), detail

    elif structured_data['type'] == 'temporal':
        durations = [d for d in structured_data['dates'] if d.get('type') == 'duration']
        if durations:
            # Sum durations by unit
            by_unit = defaultdict(int)
            for d in durations:
                by_unit[d['unit']] += d['value']
            # Pick most relevant unit from question
            q_lower = question.lower()
            for unit in ['weeks', 'days', 'months', 'years']:
                if unit in q_lower and unit in by_unit:
                    detail = 'Duration: %d %s' % (by_unit[unit], unit)
                    return str(by_unit[unit]), detail
            # Default: return largest unit
            if by_unit:
                unit = max(by_unit.keys(), key=lambda k: by_unit[k])
                detail = 'Duration: %d %s' % (by_unit[unit], unit)
                return str(by_unit[unit]), detail

        # If ISO dates present, find min and max
        iso_dates = sorted([d['raw'] for d in structured_data['dates'] if d.get('type') == 'iso'])
        if len(iso_dates) >= 2:
            detail = 'Earliest: %s, Latest: %s' % (iso_dates[0], iso_dates[-1])
            return '%s to %s' % (iso_dates[0], iso_dates[-1]), detail

    return None, None


# ============ Step 5: Build Enhanced Prompt ============
def build_aar_prompt(question, raw_evidence, computed_answer=None, computation_detail=None,
                     session_summaries=None, max_evidence_chars=4000):
    """Build the AAR-enhanced prompt with computed context."""
    parts = []

    # Computed analysis block
    if computed_answer is not None:
        parts.append('[STRUCTURED ANALYSIS — deterministic extraction from evidence]')
        if computation_detail:
            parts.append(computation_detail)
        parts.append('Computed answer: %s' % computed_answer)
        parts.append('')

    # Session summaries
    if session_summaries:
        parts.append('[SESSION SUMMARIES]')
        for s in session_summaries[:5]:
            parts.append('- %s' % s)
        parts.append('')

    # Raw evidence
    parts.append('[EVIDENCE]')
    parts.append(raw_evidence[:max_evidence_chars])

    parts.append('')
    parts.append('INSTRUCTIONS: Answer the question concisely using the evidence above.')
    if computed_answer is not None:
        parts.append('The structured analysis has already been computed deterministically. '
                     'Verify it against the evidence. If correct, state the computed answer. '
                     'If incorrect, provide the correct answer based on the evidence.')
    parts.append('')
    parts.append('QUESTION: %s' % question)
    parts.append('ANSWER:')

    return '\n'.join(parts)


# ============ Main Pipeline ============
def aar_pipeline(question, search_fn, session_summaries=None, k=12, max_evidence_chars=4000):
    """Full AAR pipeline.

    Args:
        question: the question string
        search_fn: callable(query, limit=k) -> list of results with 'content' key
        session_summaries: optional list of session summary strings
        k: number of retrieval results per query
        max_evidence_chars: max chars for raw evidence in prompt

    Returns:
        dict with keys: enhanced_prompt, computed_answer, computation_detail,
                        comp_type, sub_queries, structured_data
    """
    # Step 1: Classify
    comp_type, entity = classify_question(question)

    # If no aggregation needed, fall back to standard single query
    if comp_type is None:
        results = search_fn(question, limit=k)
        evidence = '\n'.join([r.get('content', '')[:400] for r in results[:k]])
        return {
            'enhanced_prompt': build_aar_prompt(question, evidence, session_summaries=session_summaries),
            'computed_answer': None,
            'computation_detail': None,
            'comp_type': None,
            'sub_queries': [question],
            'structured_data': None,
        }

    # Step 2: Multi-query retrieval
    sub_queries = generate_sub_queries(question, comp_type, entity)
    all_results = []
    seen_uids = set()
    for sq in sub_queries:
        results = search_fn(sq, limit=k)
        for r in results:
            uid = r.get('uid', id(r))
            if uid not in seen_uids:
                seen_uids.add(uid)
                all_results.append(r)

    # Merge evidence from all sub-queries
    evidence_texts = [r.get('content', '') for r in all_results[:k*2]]
    raw_evidence = '\n'.join([t[:400] for t in evidence_texts[:k]])

    # Step 3+4: Extract and compute
    structured_data = extract_structured_data(evidence_texts, comp_type, entity)
    computed_answer, computation_detail = compute_answer(comp_type, structured_data, question)

    # Step 5: Build prompt
    enhanced_prompt = build_aar_prompt(
        question, raw_evidence,
        computed_answer=computed_answer,
        computation_detail=computation_detail,
        session_summaries=session_summaries,
        max_evidence_chars=max_evidence_chars
    )

    return {
        'enhanced_prompt': enhanced_prompt,
        'computed_answer': computed_answer,
        'computation_detail': computation_detail,
        'comp_type': comp_type,
        'sub_queries': sub_queries,
        'structured_data': structured_data,
    }
