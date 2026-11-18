import io
import json
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import chat, documents, pdf, search
from app.vectors import VectorStore


# 파일·네트워크에 의존하지 않고 표준 글꼴과 실제 페이지 콘텐츠 스트림으로 작은 PDF를 만든다.
def make_pdf(streams, *, password=None, padding=0, compress=False):
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for content in streams:
        page = writer.add_blank_page(width=300, height=300)
        if content is not None:
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            stream = DecodedStreamObject()
            stream.set_data(content)
            page[NameObject("/Contents")] = writer._add_object(stream.flate_encode() if compress else stream)
    if password is not None:
        writer.encrypt(password)
    if padding:
        writer.add_metadata({"/Padding": "A" * padding})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


FIRST = b"BT /F1 12 Tf 14 TL 10 280 Td (first line) Tj T* (second line) Tj ET"
LAST = b"BT /F1 12 Tf 10 280 Td (unique needle) Tj ET"
IMAGE = b"q 10 0 0 10 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x7f EI Q"
linux_worker = unittest.skipUnless(sys.platform == "linux", "PDF 자원 격리는 Linux Docker에서 검증")


class PdfApiTest(unittest.TestCase):
    def setUp(self):
        self.path = str(Path(self.enterContext(TemporaryDirectory())) / "temporary.sqlite3")
        documents.initialize(self.path)
        app = FastAPI()
        app.state.db_path = self.path
        self.store = Mock(spec=VectorStore)
        app.state.vector_store = self.store
        app.include_router(documents.router)
        app.include_router(search.router)
        self.client = self.enterContext(TestClient(app))

    def upload(self, name, data):
        return self.client.post("/api/documents", params={"filename": name}, content=data)

    def test_raw_file_bounds_and_filename(self):
        for name, data, code in (
            ("안내.PDF", b"not a PDF", 400),
            ("../안내.pdf", b"", 400),
            ("안내.doc", b"", 415),
            ("안내.pdf", b"x" * pdf.MAX_PDF_BYTES, 400),
            ("안내.pdf", b"x" * (pdf.MAX_PDF_BYTES + 1), 413),
            ("안내.md", b"x" * (documents.MAX_BYTES + 1), 413),
        ):
            with self.subTest(name=name, size=len(data)):
                self.assertEqual(self.upload(name, data).status_code, code)
        self.assertEqual(self.client.get("/api/documents").json(), [])
        self.assertEqual(self.upload("안내.md", b" " * (documents.MAX_BYTES - 2) + b"\nx").status_code, 201)

    @linux_worker
    def test_physical_page_lines_index_and_sparse_source(self):
        data = make_pdf([FIRST, IMAGE, LAST], padding=documents.MAX_BYTES)
        self.assertGreater(len(data), documents.MAX_BYTES)
        response = self.upload("안내.PDF", data)
        self.assertEqual(response.status_code, 201, response.text)
        doc = response.json()["id"]
        first, last = self.client.get(f"/api/documents/{doc}/chunks").json()
        self.assertEqual((first["page_number"], first["start_line"], first["end_line"], first["text"]),
                         (1, 1, 2, "first line\nsecond line"))
        self.assertEqual((last["page_number"], last["start_line"], last["end_line"], last["text"]),
                         (3, 1, 1, "unique needle"))
        with documents.connection(self.path) as db:
            source = db.execute("SELECT source FROM documents WHERE id = ?", (doc,)).fetchone()[0]
            pages = json.loads(source)
            self.assertEqual([text.strip() for text in pages], [first["text"], "", last["text"]])
            self.assertNotIn("%PDF-", source)
        self.assertEqual(self.client.post(f"/api/documents/{doc}/index").status_code, 200)
        self.store.upsert.assert_called_once_with(doc, [(first["id"], first["text"]), (last["id"], last["text"])])
        results = self.client.post("/api/search", json={"query": "needle", "mode": "sparse"}).json()["results"]
        self.assertEqual(results, [{"chunk_id": last["id"], "document_id": doc, "filename": "안내.PDF",
                                    "page_number": 3, "start_line": 1, "end_line": 1, "text": "unique needle"}])

    @linux_worker
    def test_encrypted_damaged_image_only_and_page_limit(self):
        for data, code, reason in (
            (make_pdf([FIRST], password=""), 400, "암호화"),
            (make_pdf([FIRST])[:30], 400, "손상"),
            (make_pdf([IMAGE]), 400, "OCR"),
            (make_pdf([FIRST] + [None] * 100), 413, "100페이지"),
        ):
            with self.subTest(reason=reason):
                response = self.upload("예시.pdf", data)
                self.assertEqual(response.status_code, code)
                self.assertIn(reason, response.json()["detail"])
        self.assertEqual(self.client.get("/api/documents").json(), [])
        ok = self.upload("예시.pdf", make_pdf([None] * 99 + [LAST]))
        self.assertEqual(ok.status_code, 201, ok.text)
        chunks = self.client.get(f"/api/documents/{ok.json()['id']}/chunks").json()
        self.assertEqual([chunk["page_number"] for chunk in chunks], [100])

    @linux_worker
    def test_large_extracted_text_and_timeout_never_publish(self):
        # 압축 후 작은 파일이어도 추출 결과의 별도 상한을 우회하지 못한다.
        content = b"BT /F1 12 Tf (" + b"a" * (pdf.MAX_TEXT_BYTES + 1) + b") Tj ET"
        data = make_pdf([content], compress=True)
        self.assertLess(len(data), 32 * 1024)
        self.assertEqual(self.upload("합성.pdf", data).status_code, 413)
        with patch.object(pdf.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            self.assertEqual(self.upload("합성.pdf", make_pdf([LAST])).status_code, 413)
        self.assertEqual(self.client.get("/api/documents").json(), [])
        self.assertEqual(self.upload("다음.pdf", make_pdf([LAST])).status_code, 201)

    @linux_worker
    def test_chunk_budget_rolls_back_partial_document(self):
        with patch.object(documents, "MAX_PDF_CHUNKS", 1):
            self.assertEqual(self.upload("예시.pdf", make_pdf([FIRST, LAST])).status_code, 413)
        with documents.connection(self.path) as db:
            for table in ("documents", "chunks"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(tuple(db.execute("SELECT document_id, chunk_id FROM ingest_sequence").fetchone()), (0, 0))

    def test_unsupported_platform_refuses_pdf_only(self):
        with patch.object(pdf.sys, "platform", "darwin"), patch.object(pdf.subprocess, "run") as start:
            self.assertEqual(self.upload("안내.pdf", b"%PDF-1.7").status_code, 503)
            self.assertEqual(self.upload("안내.md", b"markdown").status_code, 201)
        start.assert_not_called()

    def test_pending_document_cannot_be_indexed_or_previewed(self):
        doc = self.upload("안내.md", b"markdown").json()["id"]
        with documents.connection(self.path) as db:
            db.execute("UPDATE documents SET status = 'delete_pending' WHERE id = ?", (doc,))
        documents.initialize(self.path)
        self.assertEqual(self.client.get("/api/documents").json()[0]["status"], "delete_pending")
        self.assertEqual(self.client.post(f"/api/documents/{doc}/index").status_code, 409)
        self.assertEqual(self.client.get(f"/api/documents/{doc}/chunks").status_code, 404)
        self.assertEqual(self.store.mock_calls, [])


class MigrationTest(unittest.TestCase):
    def test_old_rows_fts_and_snapshots_survive_and_ids_are_never_reused(self):
        path = str(Path(self.enterContext(TemporaryDirectory())) / "old.sqlite3")
        snapshot = json.dumps([{"chunk_id": 201, "document_id": 101, "filename": "과거.md", "text": "그때",
                                "start_line": 2, "end_line": 2}])
        with documents.connection(path) as db:
            db.executescript("""
                CREATE TABLE documents (id INTEGER PRIMARY KEY, filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'registered', source TEXT NOT NULL);
                CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
                    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, text TEXT NOT NULL);
                CREATE VIRTUAL TABLE chunks_fts USING fts5(text, tokenize = "unicode61 tokenchars '_'");
                INSERT INTO documents VALUES (17, '기존.md', 'ready', '그대로');
                INSERT INTO chunks VALUES (43, 17, 1, 1, '그대로');
                INSERT INTO chunks_fts (rowid, text) VALUES (43, '그대로');
            """)
        chat.initialize(path)
        with documents.connection(path) as db:
            db.execute("INSERT INTO chat_sessions VALUES (1)")
            db.execute("INSERT INTO chat_messages(session_id, role, status, sources) VALUES (1, 'assistant', 'completed', ?)",
                       (snapshot,))
        documents.initialize(path)
        with documents.connection(path) as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM documents").fetchone()), (17, "기존.md", "ready", "그대로"))
            self.assertEqual(search.sparse_ids(db, "그대로"), [43])
            self.assertIsNone(search.read_results(db, [43])[0].page_number)
            self.assertEqual(db.execute("SELECT sources FROM chat_messages").fetchone()[0], snapshot)
        app = FastAPI()
        app.state.db_path = path
        app.include_router(chat.router)
        with TestClient(app) as client:
            past = client.get("/api/chat/sessions/1/messages").json()[0]["sources"][0]
            self.assertEqual(past, {**json.loads(snapshot)[0], "page_number": None})

        def register(_):
            return documents.register(path, "새 문서.md", bytearray(b"one\n\ntwo")).id

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = sorted(pool.map(register, range(2)))
        self.assertEqual(ids, [102, 103])
        with documents.connection(path) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT id FROM chunks WHERE document_id >= 102 ORDER BY id")],
                             [202, 203, 204, 205])
            # 임시 DB의 가장 큰 두 행을 없앤 뒤 재기동하는 경우도 스냅샷만 보는 초기화로 되돌리지 않는다.
            db.execute("DELETE FROM chunks WHERE document_id = ?", (ids[-1],))
            db.execute("DELETE FROM documents WHERE id = ?", (ids[-1],))
        documents.initialize(path)
        next_doc = register(None)
        self.assertEqual(next_doc, 104)
        with documents.connection(path) as db:
            self.assertEqual([row[0] for row in db.execute("SELECT id FROM chunks WHERE document_id = ? ORDER BY id", (next_doc,))],
                             [206, 207])
