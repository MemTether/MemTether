"""
SEMA: Session-aware Evidence Packaging for Multi-session Aggregation
====================================================================
Method contribution for CCF C paper.

Problem: Multi-session questions require aggregating information across
multiple conversation sessions. Current evidence packaging flattens all
retrieved fragments into a single text block, destroying the session
boundaries that are critical for cross-session reasoning.

Solution: Package evidence with explicit session markers, preserving
temporal and structural relationships between sessions.

Usage:
    from sema import package_evidence
    packaged = package_evidence(retrieval_results, question)
"""
import re
from collections import defaultdict

def extract_session_id(uid):
    """Extract session identifier from uid like 'lme-0a995998-2-4'.
    Format: lme-<question_hash>-<session_idx>-<turn_idx>
    Returns session_idx as int, or None if not parseable."""
    m = re.match(r'lme-[a-f0-9]+-(\d+)-(\d+)', uid)
    if m:
        return int(m.group(1))
    return None


def package_evidence(results, question, max_session_chars=10000):
    """Package retrieval results grouped by session with explicit markers.
    
    Args:
        results: List of dicts with 'uid', 'content', 'score' keys
        question: The question string (for relevance hints)
        max_session_chars: Max chars per session block
    
    Returns:
        Formatted evidence string with session markers
    """
    if not results:
        return ""
    
    # Group by session
    sessions = defaultdict(list)
    no_session = []
    
    for r in results:
        sid = extract_session_id(r.get('uid', ''))
        if sid is not None:
            sessions[sid].append(r)
        else:
            no_session.append(r)
    
    if not sessions:
        # No session structure found, fall back to flat packaging
        return '\n'.join([r.get('content', '')[:500] for r in results[:12]])
    
    # Sort sessions by index (temporal order)
    sorted_sessions = sorted(sessions.keys())
    
    # Build packaged evidence
    parts = []
    total_chars = 0
    
    for sid in sorted_sessions:
        session_results = sessions[sid]
        # Sort by turn index within session
        session_results.sort(key=lambda r: int(re.match(r'lme-[a-f0-9]+-\d+-(\d+)', r.get('uid', '0')).group(1)) if re.match(r'lme-[a-f0-9]+-\d+-(\d+)', r.get('uid', '0')) else 0)
        
        # Build session block
        session_parts = []
        session_chars = 0
        for r in session_results:
            content = r.get('content', '')
            if session_chars + len(content) > max_session_chars:
                # Truncate this turn but include what fits
                remaining = max_session_chars - session_chars
                if remaining > 100:
                    session_parts.append(content[:remaining])
                    session_chars += remaining
                break
            session_parts.append(content)
            session_chars += len(content)
        
        if session_parts:
            parts.append(f"=== Session {sid + 1} ===\n" + "\n".join(session_parts))
            total_chars += session_chars
    
    # Add non-session results at the end
    if no_session:
        parts.append("=== Additional Context ===\n" + 
                     "\n".join([r.get('content', '')[:300] for r in no_session[:3]]))
    
    return "\n\n".join(parts)
