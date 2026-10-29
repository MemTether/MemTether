"""
Two-Step Structured Aggregation (TSSA)
=======================================
Step 1: LLM extracts structured facts from evidence as JSON
Step 2: Python code performs deterministic computation on the JSON
Step 3: LLM formats the computed result as the final answer

This separates semantic understanding (LLM) from mathematical computation (code),
avoiding the LLM's known weakness in arithmetic and counting.
"""
import json, re

EXTRACTION_PROMPT = """Read the CONVERSATION below and extract a JSON list of ALL relevant facts that could help answer the QUESTION. Focus on numbers, dates, items, and quantities.

Format: [{{"entity": "...", "attribute": "...", "value": ..., "unit": "..."}}]

CONVERSATION:
{evidence}

QUESTION: {question}

EXTRACTED FACTS (JSON only):"""

COMPUTATION_PROMPTS = {
    'count_items': "Count the total number of unique items in these facts. Answer with just the number.",
    'count_generic': "Count the total number of unique entries. Answer with just the number.",
    'sum': "Sum all the values. Answer with just the number.",
    'sum_money': "Sum all the monetary values. Answer with just the number and currency symbol if present.",
    'duration': "Calculate the total duration from these facts. Answer with just the number and unit.",
}

def tssa_extract(llm_call, question, evidence):
    """Step 1: LLM extracts structured facts from evidence."""
    prompt = EXTRACTION_PROMPT.format(evidence=evidence[:4000], question=question)
    response = llm_call(prompt, max_tokens=1500)
    if not response:
        return None
    # Parse JSON
    try:
        # Find JSON array in response
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except:
        pass
    return None


def tssa_compute(facts, question):
    """Step 2: Deterministic computation on structured facts."""
    if not facts:
        return None
    
    comp_type = None
    q = question.lower()
    if any(kw in q for kw in ['how many', 'count', 'number of']):
        comp_type = 'count'
    elif any(kw in q for kw in ['total', 'sum', 'combined']):
        comp_type = 'sum'
    elif any(kw in q for kw in ['how long', 'how many days', 'how many weeks', 'duration']):
        comp_type = 'duration'
    
    if comp_type == 'count':
        # Count unique entities or values
        entities = set()
        for f in facts:
            if isinstance(f, dict):
                e = f.get('entity', '') or f.get('item', '') or f.get('name', '')
                if e:
                    entities.add(str(e).lower())
                elif isinstance(f, str):
                    entities.add(f.lower())
        return len(entities), list(entities)
    
    elif comp_type in ('sum', 'duration'):
        values = []
        for f in facts:
            if isinstance(f, dict):
                v = f.get('value') or f.get('amount') or f.get('quantity')
                if v is not None:
                    try:
                        values.append(float(v))
                    except:
                        pass
        if values:
            return sum(values), values
    
    return None, None


def tssa_format(llm_call, question, computed_value, items=None):
    """Step 3: LLM formats the computed result as a natural answer."""
    if computed_value is None:
        return None
    
    prompt = f"The computed answer to the question '{question}' is {computed_value}"
    if items:
        prompt += f" (items: {', '.join(items[:5])})"
    prompt += ". Format this as a concise answer. Just the answer, nothing else."
    
    return llm_call(prompt, max_tokens=50)
