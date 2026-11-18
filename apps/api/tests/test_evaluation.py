import unittest

from app.search import SearchResult
from evaluation.dataset import CASES, DOCUMENTS
from evaluation.scoring import score, summarize


class EvaluationScoringTest(unittest.TestCase):
    def setUp(self):
        self.case = CASES[0]
        self.source = SearchResult(chunk_id=19, document_id=7, filename="달빛도서관.md",
                                   start_line=3, end_line=3, text="달빛도서관 자료 대출 기간: 14일.")
        self.other = self.source.model_copy(update={"filename": "다른도서관.md"})

    def test_gold_lines_in_corpus_and_question_mix(self):
        self.assertEqual(len(CASES), 10)
        self.assertEqual(len({case.id for case in CASES}), 10)
        self.assertEqual(len({case.query for case in CASES}), 10)
        self.assertEqual({case.kind for case in CASES}, {"exact", "paraphrase", "unanswerable"})
        for case in CASES:
            with self.subTest(case=case.id):
                if case.kind == "unanswerable":
                    self.assertIsNone(case.evidence)
                    self.assertFalse(case.answers)
                else:
                    filename, line = case.evidence
                    self.assertIn(case.answers[0], "\n\n".join(DOCUMENTS[filename]).splitlines()[line - 1])

    def test_exact_answer_or_accepted_variant_with_correct_citation(self):
        for answer in ("대출 기간은 14 일입니다. [2]", "2주 동안입니다. [2]"):
            scores = score(self.case, "completed", answer, [self.other, self.source], [self.other, self.source])
            self.assertTrue(scores["hit_at_3"])
            self.assertTrue(scores["citation_accuracy"])
            self.assertFalse(scores["insufficient_rate"])

    def test_number_in_range_is_not_evidence_and_wrong_answer_fails(self):
        for answer, cited in (
            ("14일 [1]", self.other),
            ("14일 [1]", self.source.model_copy(update={"start_line": 5, "end_line": 5})),
            ("14일 [1]", self.source.model_copy(update={"text": "서가 배치 안내"})),
            ("114일 [1]", self.source),
            ("평일 [1]", self.source),
            ("14일", self.source),
            ("14일 [2]", self.source),
        ):
            with self.subTest(answer=answer, source=cited):
                scores = score(self.case, "completed", answer, [cited], [self.source])
                self.assertFalse(scores["citation_accuracy"])
        mixed = score(self.case, "completed", "14일 [1][2]", [self.source, self.other], [self.source])
        self.assertFalse(mixed["citation_accuracy"])

    def test_retrieval_and_answer_evidence_are_separate(self):
        # 근거 부족 응답은 인용 출처가 비어도 마지막 실제 검색의 정답을 평가한다.
        s = score(self.case, "insufficient", "", [], [self.source])
        self.assertTrue(s["hit_at_3"])
        self.assertIsNone(s["citation_accuracy"])
        misses = [self.other.model_copy(update={"chunk_id": i}) for i in range(3)]
        s = score(self.case, "completed", "14일 [1]", [self.source], [*misses, self.source])
        self.assertFalse(s["hit_at_3"])
        self.assertTrue(s["citation_accuracy"])

    def test_failed_runs_and_questions_without_evidence_have_distinct_denominators(self):
        missing = next(case for case in CASES if case.evidence is None)
        rows = []
        for case, status in ((self.case, "completed"), (self.case, "insufficient"),
                             (self.case, "failed"), (missing, "insufficient"), (missing, "completed")):
            rows.append({"status": status, "scores": score(case, status, "14일 [1]", [self.source], [self.source])})
        totals = summarize(rows)
        self.assertEqual((totals["attempted"], totals["failed"]), (5, 1))
        self.assertEqual(totals["hit_at_3"], {"numerator": 2, "denominator": 2, "rate": 1.0})
        self.assertEqual(totals["citation_accuracy"], {"numerator": 1, "denominator": 2, "rate": 0.5})
        self.assertEqual(totals["insufficient_rate"], {"numerator": 2, "denominator": 4, "rate": 0.5})
        self.assertEqual(totals["unanswerable_abstention_rate"], {"numerator": 1, "denominator": 2, "rate": 0.5})
        self.assertIsNone(summarize([rows[2]])["hit_at_3"]["rate"])
