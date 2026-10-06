"""hubguard.py D4 deep tests: DBWatch, journal, proj_paths, format/parse edge cases."""
import os, sys, io, tempfile, sqlite3, threading
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'

def test_db_path_env(tmp_path):
    import hubguard, os
    db = str(tmp_path / 'env.db')
    conn = sqlite3.connect(db); conn.execute('CREATE TABLE t(x)'); conn.commit(); conn.close()
    os.environ['MEM_DB'] = db
    assert hubguard.db_path() == db
    os.environ.pop('MEM_DB', None)

def test_db_path_explicit(tmp_path):
    import hubguard
    db = str(tmp_path / 'exp.db')
    assert hubguard.db_path(db) == db

def test_typ_abbr():
    import hubguard
    assert hubguard.typ_abbr('fact') is not None
    assert hubguard.typ_abbr('experience') is not None

def test_src_code():
    import hubguard
    s = hubguard.src_code('codex')
    assert s is not None

def test_same_snapshot_consistent(tmp_path):
    import hubguard
    db = str(tmp_path / 'sn2.db')
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE facts (uid TEXT PRIMARY KEY, content TEXT, status TEXT)')
    conn.execute("INSERT INTO facts VALUES ('u1', 'x', 'active')")
    conn.commit(); conn.close()
    s1 = hubguard.snapshot(db)
    s2 = hubguard.snapshot(db)
    assert hubguard.same_snapshot(s1, s2)

def test_same_snapshot_changed(tmp_path):
    import hubguard
    db = str(tmp_path / 'sn3.db')
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE facts (uid TEXT PRIMARY KEY, content TEXT, status TEXT)')
    conn.execute("INSERT INTO facts VALUES ('u1', 'x', 'active')")
    conn.commit(); conn.close()
    s1 = hubguard.snapshot(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO facts VALUES ('u2', 'y', 'active')")
    conn.commit(); conn.close()
    s2 = hubguard.snapshot(db)
    assert not hubguard.same_snapshot(s1, s2)

def test_safe_append_bytes(tmp_path):
    import hubguard
    target = tmp_path / 'sb.bin'
    hubguard.safe_append_bytes(str(target), b'binary\x00data')
    assert target.read_bytes() == b'binary\x00data'

def test_format_fact_line_no_source():
    import hubguard
    line = hubguard.format_fact_line('2026-10-06', 'fact', '', 'content here', tag_src=False)
    assert line is not None
    assert 'codex' not in line  # no source tag

def test_parse_fact_line_invalid():
    import hubguard
    assert hubguard.parse_fact_line('not a fact line') is None
    assert hubguard.parse_fact_line('') is None

def test_parse_fact_line_no_source():
    import hubguard
    line = hubguard.format_fact_line('2026-10-06', 'fact', '', 'content', tag_src=False)
    parsed = hubguard.parse_fact_line(line)
    assert parsed is not None
    assert parsed.get('src') is None or parsed.get('src') == ''

def test_detect_newline_lf():
    import hubguard
    f = tempfile.mktemp(suffix='.txt')
    with open(f, 'wb') as fh: fh.write(b'line1\nline2\n')
    nl = hubguard.detect_newline(f)
    assert nl in ('\n', '\\n', 'lf')
    os.remove(f)

def test_detect_newline_crlf():
    import hubguard
    f = tempfile.mktemp(suffix='.txt')
    with open(f, 'wb') as fh: fh.write(b'line1\r\nline2\r\n')
    nl = hubguard.detect_newline(f)
    assert nl in ('\r\n', '\\r\\n', 'crlf')
    os.remove(f)
