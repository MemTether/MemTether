"""
AFN: Answer Format Normalization
Post-processes LLM-generated answers to extract the core value
and format it consistently for strict matching.
"""
import re

def normalize_answer(answer, question=None):
    """Extract the core answer value from a verbose LLM response."""
    if not answer:
        return answer
    if 'NOT ENOUGH INFO' in answer:
        return answer
    
    a = answer.strip()
    
    # Pattern 1: Pure number
    if re.fullmatch(r'-?\d+(?:\.\d+)?', a):
        return a
    
    # Pattern 2: Number with unit
    m = re.search(r'(-?\d[\d,]*(?:\.\d+)?)\s*(weeks?|days?|months?|years?|hours?|minutes?|miles?|km|kg|%|dollars?|\$[\d,]+|items?|pages?|projects?|restaurants?|times?|people|books?|movies?)', a, re.IGNORECASE)
    if m:
        num = m.group(1)
        unit = m.group(2)
        if '.' in num:
            num = num.rstrip('0').rstrip('.')
        return f"{num} {unit}"
    
    # Pattern 3: Currency
    m = re.search(r'\$[\d,]+(?:\.\d+)?', a)
    if m:
        val = m.group(0)
        val = re.sub(r'\.00$', '', val)
        return val
    
    # Pattern 4: Percentage
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:%|percent)', a, re.IGNORECASE)
    if m:
        return f"{m.group(1)}%"
    
    # Pattern 5: Multiple numbers joined by "and" (sub-items, sum them)
    # "5 tomato plants and 3 cucumber plants" → "8"
    if ' and ' in a.lower() and len(a) < 100:
        parts = a.lower().split(' and ')
        if len(parts) == 2:
            nums1 = re.findall(r'\d+', parts[0])
            nums2 = re.findall(r'\d+', parts[1])
            if nums1 and nums2:
                total = int(nums1[-1]) + int(nums2[0])
                return str(total)
        # Check for word numbers
        word_map = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
        for word, num in word_map.items():
            if word in a.lower():
                return str(num)
    
    # Pattern 6: Bare number in a sentence
    m = re.search(r'\b(\d+(?:\.\d+)?)\b', a)
    if m and len(a) < 100:
        num = m.group(1)
        unit_m = re.search(re.escape(num) + r'\s*(weeks?|days?|months?|years?|hours?|miles?|%|items?|pages?)', a, re.IGNORECASE)
        if unit_m:
            return f"{num} {unit_m.group(1)}"
        return num
    
    # Pattern 7: Named entity
    if len(a) < 100:
        a = re.sub(r'^(The answer is|It was|It is|That would be|Based on|From the)\s+', '', a, flags=re.IGNORECASE)
        a = a.strip().rstrip('.')
        if a and len(a) < 60 and not any(c in a for c in [';', '{', '}', '<', '>']):
            return a
    
    return answer
