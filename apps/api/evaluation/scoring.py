"""실패와 채점 비대상은 0점으로 간주하지 않고 각각 기록한다."""

import re
import unicodedata
from collections import Counter

from app.chat import CITATION
from app.search import SearchResult

from .dataset import Case

METRICS = ("hit_at_3", "citation_accuracy", "insufficient_rate", "unanswerable_abstention_rate")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # 천 단위 쉼표와 단위 앞 공백은 채점에 영향을 주지 않는다.
    return re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", re.sub(r"\s+", "", text))


def _contains_answer(text: str, case: Case) -> bool:
    text = _normalize(text)
    for alias in case.answers:
        term = _normalize(alias)
        # '114일' 안의 '14일'이나 '22:001' 안의 '22:00'은 정답이 아니다.
        pattern = (r"(?<!\d)" if term[0].isdigit() else "") + re.escape(term)
        if re.search(pattern + (r"(?!\d)" if term[-1].isdigit() else ""), text):
            return True
    return False


def _supports(source: SearchResult, case: Case) -> bool:
    if case.evidence is None:
        return False
    filename, line = case.evidence
    return (source.filename == filename and source.start_line <= line <= source.end_line
            and _contains_answer(source.text, case))


def score(case: Case, status: str, answer: str, sources: list[SearchResult],
          retrieved: list[SearchResult]) -> dict[str, bool | None]:
    scores = dict.fromkeys(METRICS)
    if status == "failed":
        return scores
    if case.evidence is not None:
        scores["hit_at_3"] = any(_supports(row, case) for row in retrieved[:3])
    if status == "completed":
        refs = CITATION.findall(answer)
        supported = {str(i) for i, row in enumerate(sources, 1) if _supports(row, case)}
        scores["citation_accuracy"] = (bool(refs) and all(ref in supported for ref in refs)
                                       and _contains_answer(CITATION.sub("", answer), case))
    scores["insufficient_rate"] = status == "insufficient"
    if case.evidence is None:
        scores["unanswerable_abstention_rate"] = status == "insufficient"
    return scores


def summarize(rows: list[dict]) -> dict:
    statuses = Counter(row["status"] for row in rows)
    summary = {"attempted": len(rows), **{s: statuses[s] for s in ("completed", "insufficient", "failed")}}
    for metric in METRICS:
        values = [row["scores"][metric] for row in rows if row["scores"][metric] is not None]
        numerator, denominator = sum(values), len(values)
        summary[metric] = {"numerator": numerator, "denominator": denominator,
                           "rate": numerator / denominator if denominator else None}
    return summary
