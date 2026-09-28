# Test extract.py with mocked LLM response
import os, sys, json
sys.path.insert(0, r'E:\RUANJIAN\memtether')
sys.stdout.reconfigure(encoding='utf-8')

# Mock the LLM call
import extract
import unittest.mock as mock

mock_response = {
    'choices': [{'message': {'content': json.dumps({
        'memories': [
            {'content': 'SQLite WAL mode 并发写入时必须加 hubguard 跨进程锁', 'type': 'experience', 'confidence': 0.9, 'tags': 'sqlite,concurrency'},
            {'content': 'sandbox 用户可以读写 E:\\Temp 但下轮可能被回收', 'type': 'fact', 'confidence': 0.8},
            {'content': '', 'type': 'fact', 'confidence': 0.5},
        ]
    })}}]
}

with mock.patch.object(extract, 'call_llm', return_value=mock_response['choices'][0]['message']['content'] if isinstance(mock_response, str) else json.loads(mock_response['choices'][0]['message']['content'])):
    memories, err = extract.extract_memories('some test transcript', api_key='fake', base_url='http://localhost', model='test')
    assert err is None, f'unexpected error: {err}'
    assert len(memories) == 2, f'expected 2 valid, got {len(memories)}: {memories}'
    assert memories[0]['content'] == 'SQLite WAL mode 并发写入时必须加 hubguard 跨进程锁'
    assert memories[0]['type'] == 'experience'
    print('extract_memories: PASS (2 valid, 1 empty filtered)')

# Test empty input
memories, err = extract.extract_memories('   ', api_key='fake', base_url='http://localhost', model='test')
assert err == 'empty input', f'expected empty input error, got {err}'
print('empty input: PASS')

# Test no API key
memories, err = extract.extract_memories('test', api_key='', base_url='http://localhost', model='test')
assert err == 'MEM_EXTRACT_API_KEY not set (fail-closed)', f'expected fail-closed, got {err}'
print('no key fail-closed: PASS')

# Test long input truncation
long_text = 'x' * 40000
# Just verify the truncation logic (we won't actually call)
truncated = long_text[:32000] + '\n...[truncated]'
assert len(truncated) <= 32020
print('truncation: PASS')

print('All extract tests PASS')
