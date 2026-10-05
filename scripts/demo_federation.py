# -*- coding: utf-8 -*-
"""
demo_federation.py — Two MemTether instances federate over HTTP with live
conflict resolution. This is the CCF "system-level" demo: two independent
memory hubs exchange memories; polarity conflicts are auto-resolved with
supersession (never delete).

Run:  python scripts/demo_federation.py
Exit 0 = PASS. Zero production writes (temp DBs).
"""
import json, os, sys, tempfile, threading, time, sqlite3, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import memtether_exchange as mx
import gateway


def _mkdb(tmp, name):
    db = os.path.join(tmp, name)
    gateway.DB = db
    gateway.init_db()
    return db


def _serve(db, port):
    srv = mx.serve_peer(db_path=db, port=port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    return srv


def main():
    tmp = tempfile.mkdtemp(prefix="mt_federation_")
    # Instance A (the "laptop" agent): has a key-status fact
    db_a = _mkdb(tmp, "a.db")
    gateway.remember("OPENAI_API_KEY is valid and funded", type="fact",
                     source="laptop", scope="shared")
    conn_a0 = sqlite3.connect(db_a)
    conn_a0.execute("UPDATE facts SET updated_at=datetime('now','-2 seconds')")
    conn_a0.commit()
    conn_a0.close()
    # Instance B (the "desktop" agent): has a NEWER contradicting fact
    db_b = _mkdb(tmp, "b.db")
    import datetime
    future = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    r_desktop = gateway.remember("OPENAI_API_KEY failed with 401 quota exceeded",
                     type="incident", source="desktop", scope="shared",
                     valid_from=future)
    # make latest_wins deterministic across fast CI: bump updated_at by 1s
    conn_b = sqlite3.connect(db_b)
    conn_b.execute("UPDATE facts SET updated_at=datetime('now','+1 second') WHERE uid=?",
                   (r_desktop['uid'],))
    conn_b.commit()
    conn_b.close()

    srv_a = _serve(db_a, 18421)

    # B pulls from A -> merge; then conflict: desktop's newer 401 vs laptop's valid
    stats, conflicts = mx.pull_peer("http://127.0.0.1:18421", db_path=db_b,
                                    resolve="latest_wins")
    srv_a.shutdown()

    # assert merge happened
    conn = sqlite3.connect(db_b)
    n_b = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    superseded = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE status='superseded'").fetchone()[0]
    kept = conn.execute(
        "SELECT content FROM facts WHERE status='active' AND content LIKE '%OPENAI%'"
    ).fetchall()
    conn.close()

    print(json.dumps({
        "imported": stats.get("facts_inserted"),
        "total_in_b": n_b,
        "superseded": superseded,
        "active_key_facts": [k[0][:60] for k in kept],
        "conflicts_resolved": conflicts,
        "tmp": tmp,
    }, ensure_ascii=False, indent=2))

    ok = (n_b >= 2 and len(kept) == 1 and "401" in kept[0][0])
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
