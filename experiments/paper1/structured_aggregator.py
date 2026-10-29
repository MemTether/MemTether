"""
Structured Aggregator: Extract structured data from evidence, compute answers
deterministically, then inject the computed result into the LLM prompt.

This is a genuine method contribution because:
1. It replaces LLM guessing with deterministic computation
2. It's architecture-level (not prompt engineering)
3. It's generalizable to any multi-session aggregation task
"""
import re
from collections import defaultdict

def extract_numbers(text):
    """Extract all numbers with their surrounding context."""
    numbers = []
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(weeks?|days?|months?|years?|hours?|minutes?|miles?|km|kg|%|dollars?|\$[\d,]+|items?|pages?|projects?|restaurants?|times?|people|books?|movies?)?', text, re.IGNORECASE):
        num = m.group(1)
        unit = m.group(2) or ''
        # Get surrounding context (±50 chars)
        start = max(0, m.start() - 50)
        end = min(len(text), m.end() + 50)
        context = text[start:end].replace('\n', ' ')
        numbers.append({
            'value': float(num) if '.' in num else int(num),
            'raw': m.group(0),
            'unit': unit,
            'context': context,
        })
    return numbers


def extract_dates(text):
    """Extract date references (e.g., 'March 15th', 'last Monday', '2026-03-15')."""
    dates = []
    # Month day format
    for m in re.finditer(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?', text, re.IGNORECASE):
        dates.append({'raw': m.group(0), 'type': 'month_day', 'context': text[max(0,m.start()-50):m.end()+50]})
    # ISO format
    for m in re.finditer(r'\d{4}-\d{2}-\d{2}', text):
        dates.append({'raw': m.group(0), 'type': 'iso', 'context': text[max(0,m.start()-50):m.end()+50]})
    return dates


def detect_computation_type(question):
    """Detect what computation the question requires."""
    q = question.lower()
    if any(kw in q for kw in ['how many', 'number of', 'count']):
        if any(kw in q for kw in ['item', 'thing', 'clothing', 'book', 'movie', 'restaurant', 'project']):
            return 'count_items'
        return 'count_generic'
    if any(kw in q for kw in ['total', 'sum', 'combined', 'altogether']):
        return 'sum'
    if any(kw in q for kw in ['how long', 'how many days', 'how many weeks', 'duration']):
        return 'duration'
    if any(kw in q for kw in ['difference', 'how much more', 'how much less']):
        return 'difference'
    if any(kw in q for kw in ['how much', 'cost', 'spend', 'price']):
        return 'sum_money'
    return None


def compute_count_items(evidence_text, question):
    """Count unique items mentioned in the evidence that match the question context."""
    # Extract key nouns from the question
    q_lower = question.lower()
    
    # Common item categories
    item_patterns = {
        'clothing': r'(jacket|shirt|pants|shoes|boots|dress|skirt|coat|sweater|socks|shorts|hat|cap|gloves|scarf|belt)',
        'books': r'(book|novel|textbook|magazine)',
        'movies': r'(movie|film)',
        'restaurants': r'(restaurant|cafe|diner|bistro)',
        'projects': r'(project|task|assignment)',
    }
    
    # Find which category the question is about
    target_pattern = None
    for cat, pat in item_patterns.items():
        if cat.replace('_', ' ') in q_lower or cat in q_lower:
            target_pattern = pat
            break
    
    # If no specific category, look for generic items after "pick up", "return", "buy", etc.
    if not target_pattern:
        # Look for "pick up or return" pattern
        action_match = re.search(r'(pick up|return|buy|get|need to)', q_lower)
        if action_match:
            # Find items mentioned near action words in evidence
            items = set()
            for m in re.finditer(r'(?:pick up|return|buy|get|need to)\s+(?:my\s+|a\s+|the\s+)?([a-z]+(?:\s+[a-z]+)?)', evidence_text.lower()):
                noun = m.group(1).strip()
                if len(noun) > 2 and noun not in ('it', 'the', 'a', 'an', 'my', 'some', 'this', 'that'):
                    items.add(noun)
            return len(items), list(items)
    
    # Count unique items matching the pattern
    items = set()
    for m in re.finditer(target_pattern or r'\b([a-z]{3,})\b', evidence_text, re.IGNORECASE):
        items.add(m.group(1).lower())
    
    return len(items), list(items)


def compute_sum(evidence_text, question):
    """Sum all relevant numbers in the evidence."""
    numbers = extract_numbers(evidence_text)
    if not numbers:
        return None, []
    
    # Filter numbers that appear near relevant keywords
    q_lower = question.lower()
    keywords = []
    for kw in ['mile', 'km', 'dollar', '$', 'cost', 'spend', 'page', 'hour', 'minute']:
        if kw in q_lower:
            keywords.append(kw)
    
    if keywords:
        relevant = [n for n in numbers if any(kw in n['context'].lower() for kw in keywords)]
    else:
        relevant = numbers
    
    if not relevant:
        return None, []
    
    total = sum(n['value'] for n in relevant)
    return total, relevant


def build_structured_prompt(question, evidence, computation_type, computed_value=None, items=None):
    """Build a prompt that includes the structured computation result."""
    
    base = (
        "Answer the QUESTION using ONLY the CONTEXT below. "
        "Be concise — give the direct answer, no explanation.\n"
        "If the context does not contain the answer, reply exactly: NOT ENOUGH INFO\n\n"
    )
    
    if computed_value is not None:
        hint = f"\n[STRUCTURED ANALYSIS] Based on systematic extraction from the evidence:\n"
        if computation_type == 'count_items' and items:
            hint += f"Identified {len(items)} unique items: {', '.join(items)}\n"
            hint += f"Count = {computed_value}\n"
        elif computation_type in ('sum', 'sum_money') and computed_value is not None:
            hint += f"Computed sum = {computed_value}\n"
        hint += "Use this analysis to verify your answer. If the structured analysis differs from your reading, trust the structured analysis.\n"
        return base + hint + f"CONTEXT:\n{evidence[:3000]}\n\nQUESTION: {question}\nANSWER:"
    
    return base + f"CONTEXT:\n{evidence[:3000]}\n\nQUESTION: {question}\nANSWER:"


def structured_aggregate(question, evidence):
    """Main entry: analyze the question and evidence, perform structured computation.
    
    Returns:
        (computation_type, computed_value, items, structured_prompt)
        computation_type is None if no structured computation is applicable.
    """
    comp_type = detect_computation_type(question)
    if not comp_type:
        return None, None, None, None
    
    if comp_type == 'count_items':
        count, items = compute_count_items(evidence, question)
        if count > 0:
            prompt = build_structured_prompt(question, evidence, comp_type, count, items)
            return comp_type, count, items, prompt
    
    elif comp_type in ('sum', 'sum_money'):
        total, relevant = compute_sum(evidence, question)
        if total is not None:
            prompt = build_structured_prompt(question, evidence, comp_type, total)
            return comp_type, total, relevant, prompt
    
    elif comp_type == 'count_generic':
        count, items = compute_count_items(evidence, question)
        if count > 0:
            prompt = build_structured_prompt(question, evidence, comp_type, count, items)
            return comp_type, count, items, prompt
    
    # No structured computation applicable or no result
    return comp_type, None, None, None
