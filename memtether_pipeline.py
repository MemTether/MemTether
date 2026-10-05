# -*- coding: utf-8 -*-
"""
memtether_pipeline.py — N7 (2026-09-25): 统一安全扫描管线

把 memtether_guard.py（检索侧 prompt injection / 凭据检测）
和 memtether_export.py（导出侧 PII 脱敏）的规则合并到一个模块。

分工：
  - scan_content()      → 检索侧：标记可疑内容（不改写）
  - sanitize_text()     → 导出侧：脱敏改写
  - full_scan()         → 同时做标记 + 脱敏，返回 (flagged, clean_text)

两端统一从这取规则，避免规则漂移。
"""
import re as _re

# === 导出侧 PII 脱敏规则（memtether_export.py 原有 + 补充）===
PII_PATTERNS = [
    # 中国大陆手机号
    (_re.compile(r'\b(1[3-9]\d{9})\b'), '[PHONE]'),
    # 邮箱（保守）
    (_re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|cn|net|org|io|me|run|app|dev)\b'), '[EMAIL]'),
    # API key / token
    (_re.compile(r'\b(sk|zpu|hf_|ghp_|gho_|glpat_)[A-Za-z0-9_\-]{20,}\b'), '[API_KEY]'),
    (_re.compile(r'\bbearer\s+[A-Za-z0-9\-_.]{20,}', _re.I), 'Bearer [TOKEN]'),
    # AWS access key
    (_re.compile(r'\b(AKIA[0-9A-Z]{16})\b'), '[AWS_KEY]'),
    # Slack bot token
    (_re.compile(r'\b(xoxb-[0-9A-Za-z\-]{20,})\b'), '[SLACK_TOKEN]'),
]

# === 检索侧安全扫描规则（memtether_guard.py 原有）===
GUARD_RULES = [
    # (category, pattern, severity 0=info/1=warn/2=block, description)
    # LLM01 Prompt Injection
    ('prompt_injection', _re.compile(r'忽略(?:之前|以上|上面|前)?(?:的|所有|之前的|之前所有)*?(?:指令|规则|约束|设置)', _re.I), 2,
     '试图让 agent 违反已有指令'),
    ('prompt_injection', _re.compile(r'(?:你是|你现在)(?:一个新的|不同的|无限制的)(?:AI|agent|模型)', _re.I), 2,
     '试图重定义 agent 身份'),
    ('prompt_injection', _re.compile(r'(?:system|系统)?(?:prompt|提示词)(?:如下|是|为)', _re.I), 1,
     '暴露或尝试操纵系统提示词'),
    ('prompt_injection', _re.compile(r'(?:你必须|你务必|请务必)(?:不顾|无视|忽略)', _re.I), 2,
     '要求无视安全约束'),

    # LLM02 Insecure Output / Code Execution
    ('code_exec', _re.compile(r'(?:os\.system|subprocess\.(?:run|call|Popen)|eval\s*\(|exec\s*\()', _re.I), 2,
     '含直接代码执行模式'),
    ('code_exec', _re.compile(r'(?:curl|wget|Invoke-WebRequest)\s+https?://', _re.I), 1,
     '含外部资源下载命令'),
    ('code_exec', _re.compile(r'(?:rm\s+-rf|del\s+/[sqs]\s|Remove-Item.*-Recurse.*-Force)', _re.I), 2,
     '含破坏性文件系统命令'),

    # LLM06 Sensitive Info
    ('sensitive', _re.compile(r'\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|AKIA[A-Z0-9]{16})\b'), 2,
     '含疑似 API 密钥/凭证'),
    ('sensitive', _re.compile(r'\beyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\b'), 1,
     '含疑似 JWT token'),
    ('sensitive', _re.compile(r'(?i)password\s*[=:]\s*\S{4,}'), 1,
     '含疑似明文密码'),

    # Social Engineering
    ('social_eng', _re.compile(r'(?:紧急|立即|马上| urgently|immediately).*(?:执行|运行|打开|点击|访问)', _re.I), 1,
     '社会工程：紧急诱导'),
    ('social_eng', _re.compile(r'(?:sudo|管理员|root|elevated).*(?:运行|执行|启动)', _re.I), 1,
     '要求提升权限执行'),
]

SCAN_KEY = '_guard_flags'


def scan_content(text):
    """对单条文本做安全扫描，返回 hits 列表。"""
    if not text or not isinstance(text, str):
        return []
    hits = []
    for cat, pat, sev, desc in GUARD_RULES:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 10)
            snippet = text[start:m.end() + 10].replace('\n', ' ')[:60]
            hits.append({'category': cat, 'severity': sev, 'matched': snippet, 'description': desc})
    return hits


def sanitize_text(text):
    """对单条文本做 PII 脱敏改写，返回 (cleaned, n_changed)。"""
    if not isinstance(text, str):
        return text, 0
    n = 0
    for pat, repl in PII_PATTERNS:
        new = pat.sub(repl, text)
        if new != text:
            n += 1
            text = new
    return text, n


def full_scan(text):
    """同时做安全标记 + PII 脱敏。返回 (hits, cleaned_text, n_pii)。"""
    hits = scan_content(text)
    cleaned, n_pii = sanitize_text(text)
    return hits, cleaned, n_pii


def scan_results(results):
    """对 memsearch 返回的 results 列表扫描，添加 _guard_flags 字段。"""
    if not isinstance(results, list):
        return results
    for item in results:
        if not isinstance(item, dict):
            continue
        content = item.get('content', '') or ''
        hits = scan_content(content)
        if hits:
            item[SCAN_KEY] = hits
            item['_guard_max_severity'] = max(h['severity'] for h in hits)
    return results


def sanitize_snapshot(data):
    """对快照中的所有 content / text 字段做 PII 脱敏。返回 (clean_data, n_changes)."""
    n = 0
    def _clean_str(val):
        nonlocal n
        if isinstance(val, str):
            cleaned, c = sanitize_text(val)
            n += c
            return cleaned
        return val

    for section in ('facts', 'tool_assets'):
        for item in data.get(section, []):
            for key in list(item.keys()):
                item[key] = _clean_str(item[key])

    for sup in data.get('supersessions', []):
        for key in ('reason', 'by_agent'):
            if key in sup:
                sup[key] = _clean_str(sup[key])
    return data, n
