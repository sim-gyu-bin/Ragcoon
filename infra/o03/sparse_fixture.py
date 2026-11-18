"""O03 전용 빈 SQLite에만 희소 검색 fixture를 준비한다. 실제 벡터 색인을 뜻하지 않는다."""
import os
from app.documents import connection, register

path = os.environ['RAGCOON_DB']
if path != '/data/ragcoon.sqlite3' or os.environ.get('OPENAI_API_KEY'):
    raise ValueError('smoke storage required')
with connection(path) as db:
    if db.execute('SELECT count(*) FROM documents').fetchone()[0] != 0:
        raise ValueError('empty smoke storage required')
fixture = register(path, 'o03-fixture.md', bytearray(b'o03sparseproof'))
with connection(path) as db:
    db.execute('INSERT INTO chunks_fts(rowid,text) SELECT id,text FROM chunks WHERE document_id=?', (fixture.id,))
    db.execute("UPDATE documents SET status='ready' WHERE id=?", (fixture.id,))
