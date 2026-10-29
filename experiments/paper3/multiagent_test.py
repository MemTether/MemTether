import sqlite3, time, json, sys, os, threading, random
sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = r'E:\RUANJIAN\memory_hub\bench_data\multiagent_test.db'

def main():
    # Setup: create DB and table in one connection
    conn_setup = sqlite3.connect(DB_PATH, timeout=30)
    conn_setup.execute("PRAGMA journal_mode=WAL")
    conn_setup.execute("PRAGMA synchronous=NORMAL")
    conn_setup.execute("PRAGMA busy_timeout=15000")
    conn_setup.execute("DROP TABLE IF EXISTS agent_memories")
    conn_setup.execute("CREATE TABLE agent_memories (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, content TEXT, created_at TEXT)")
    conn_setup.commit()
    # Keep this connection open to maintain WAL mode
    
    results = []
    stop_event = threading.Event()
    
    def writer(agent_id):
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=15000")
        count = 0
        errors = 0
        while not stop_event.is_set():
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO agent_memories (agent, content, created_at) VALUES (?,?,?)",
                    (agent_id, f"Memory #{count} from {agent_id}", time.strftime('%Y-%m-%dT%H:%M:%S')))
                conn.execute("COMMIT")
                count += 1
            except sqlite3.OperationalError:
                errors += 1
                time.sleep(random.uniform(0.001, 0.008))
        conn.close()
        results.append({'agent': agent_id, 'writes': count, 'lock_errors': errors})
    
    def reader(agent_id):
        time.sleep(1)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA busy_timeout=10000")
        reads = 0
        last = 0
        while not stop_event.is_set():
            try:
                last = conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
                reads += 1
            except: pass
            time.sleep(0.05)
        conn.close()
        results.append({'agent': agent_id, 'reads': reads, 'last_count': last})
    
    print('Starting 2 writers + 2 readers for 30 seconds...')
    threads = [
        threading.Thread(target=writer, args=('agent_A',)),
        threading.Thread(target=writer, args=('agent_B',)),
        threading.Thread(target=reader, args=('reader_1',)),
        threading.Thread(target=reader, args=('reader_2',)),
    ]
    for t in threads: t.start()
    time.sleep(30)
    stop_event.set()
    for t in threads: t.join()
    
    total = conn_setup.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
    a = conn_setup.execute("SELECT COUNT(*) FROM agent_memories WHERE agent='agent_A'").fetchone()[0]
    b = conn_setup.execute("SELECT COUNT(*) FROM agent_memories WHERE agent='agent_B'").fetchone()[0]
    rc = [r.get('last_count') for r in results if 'last_count' in r]
    le = sum(r.get('lock_errors', 0) for r in results)
    
    print(f'\nTotal writes: {total}')
    print(f'  Agent A: {a} ({a/total*100:.0f}%)')
    print(f'  Agent B: {b} ({b/total*100:.0f}%)')
    print(f'Reader observations: {rc}')
    print(f'Reader drift: {"ZERO" if len(set(rc)) <= 1 else "DETECTED"}')
    print(f'Lock contention errors: {le}')
    print(f'Throughput: {total/30:.0f} writes/s')
    conn_setup.close()
    
    output = {'duration_s': 30, 'writers': 2, 'readers': 2, 'total_writes': total,
              'agent_a': a, 'agent_b': b, 'reader_observations': rc,
              'zero_drift': len(set(rc)) <= 1, 'lock_errors': le,
              'throughput_wps': round(total/30, 0)}
    with open(r'E:\Temp\multiagent_test_results.json', 'w') as f:
        json.dump(output, f, indent=2)
    print('Saved to E:/Temp/multiagent_test_results.json')

if __name__ == '__main__':
    main()