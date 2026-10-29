"""
AFAG: Answer Formulation-Aware Generation
=========================================
Method contribution for the CCF C paper.

Uses the diagnostic finding (91% of multi-session strict-applicable questions
require computed aggregate answers that don't appear verbatim in any source)
to guide the generation strategy. Instead of a generic "answer the question"
prompt, AFAG injects type-specific scaffolds that guide the LLM to output
the computed value in a format strict matching can find.

Usage:
    from afag import afag_generate
    answer, err = afag_generate(question, evidence, gold_hint=None)
"""

import sys, io, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memtether")

def detect_afag_type(question):
    """Detect if this question needs AFAG treatment and what type it is.
    Returns (needs_afag, afag_type) or (False, None)."""
    q = question.lower()
    
    # Counting questions: "how many", "how much", "number of"
    if any(kw in q for kw in ['how many', 'how much', 'number of', 'count']):
        return True, 'counting'
    
    # Temporal duration questions: "how long", "how many days/months/years"
    if any(kw in q for kw in ['how long', 'how many days', 'how many months', 
                               'how many years', 'how many hours']):
        return True, 'temporal_duration'
    
    # Comparison questions: "which is more", "difference between"
    if any(kw in q for kw in ['difference between', 'which is more', 'which is larger',
                               'compare', 'versus', ' vs ']):
        return True, 'comparison'
    
    # Sum questions: "total", "sum", "combined"
    if any(kw in q for kw in ['total', 'sum', 'combined', 'altogether', 'in total']):
        return True, 'sum'
    
    return False, None


def build_afag_prompt(question, evidence, afag_type):
    """Build an AFAG-enhanced prompt that guides the LLM to compute
    the aggregate answer and output it in a strict-matchable format."""
    
    base = (
        "Answer the QUESTION using ONLY the CONTEXT below. "
        "Be concise — give the direct answer, no explanation.\n"
        "If the context does not contain the answer, reply exactly: NOT ENOUGH INFO\n\n"
    )
    
    if afag_type == 'counting':
        scaffold = (
            "IMPORTANT: This question asks you to COUNT items or events. "
            "Read through ALL the evidence carefully and count each relevant item. "
            "Your answer MUST include the number AND the unit (e.g., '3' or '5'), nothing else.\n\n"
        )
    elif afag_type == 'temporal_duration':
        scaffold = (
            "IMPORTANT: This question asks about a TIME DURATION (days, months, hours, etc.). "
            "Find the start and end dates/times in the evidence and calculate the difference. "
            "Your answer MUST be a specific duration with the unit (e.g., '3 days' or '8 hours'), nothing else.\n\n"
        )
    elif afag_type == 'comparison':
        scaffold = (
            "IMPORTANT: This question asks you to COMPARE two or more items. "
            "Identify each item, find their values, and determine which is larger/smaller. "
            "Your answer MUST name the specific item that answers the question (e.g., 'Project A'), nothing else.\n\n"
        )
    elif afag_type == 'sum':
        scaffold = (
            "IMPORTANT: This question asks for a TOTAL or SUM of multiple values. "
            "Find each relevant value in the evidence and add them together. "
            "Your answer MUST be the total with any relevant unit (e.g., '$2,500' or '15'), nothing else.\n\n"
        )
    else:
        scaffold = ""
    
    return base + scaffold + "CONTEXT:\n%s\n\nQUESTION: %s\nANSWER:" % (evidence[:3000], question)


def afag_generate(question, evidence, gold_hint=None, model=None, question_type=None):
    """AFAG-enhanced answer generation.
    Returns (answer_text, error) same as bench_longmemeval.generate_answer.
    
    Falls back to standard generation if AFAG type is not detected."""
    
    needs_afag, afag_type = detect_afag_type(question)
    
    if not needs_afag:
        # Standard path - use the original prompt
        import requests
        ch = _pick_judge_channel()
        if not ch:
            return None, 'no available channel'
        envn, base_url, mdl, key = ch
        prompt = (
            "Answer the QUESTION using ONLY the CONTEXT below. "
            "Be concise — give the direct answer, no explanation.\n"
            "If the context does not contain the answer, reply exactly: NOT ENOUGH INFO\n\n"
            "CONTEXT:\n%s\n\nQUESTION: %s\nANSWER:" % (evidence[:3000], question)
        )
    else:
        # AFAG path - use enhanced prompt with scaffold
        prompt = build_afag_prompt(question, evidence, afag_type)
        import requests
        ch = _pick_judge_channel()
        if not ch:
            return None, 'no available channel'
        envn, base_url, mdl, key = ch
    
    try:
        r = requests.post(base_url.rstrip('/') + '/chat/completions',
                          headers={'Authorization': 'Bearer ' + key,
                                   'Content-Type': 'application/json'},
                          json={'model': 'cn:glm-5.3-flash',
                                'messages': [{'role': 'user', 'content': prompt}],
                                'max_tokens': 2000, 'temperature': 0},
                          timeout=60)
        r.raise_for_status()
        out = (r.json()['choices'][0]['message']['content'] or '').strip()
        return (out or None), ('' if out else 'empty generation')
    except Exception as e:
        return None, '[%s] %s: %s' % (envn, type(e).__name__, str(e)[:70])


def _pick_judge_channel():
    """Import from bench_longmemeval to reuse the channel selection."""
    from bench_longmemeval import _pick_judge_channel
    return _pick_judge_channel()


