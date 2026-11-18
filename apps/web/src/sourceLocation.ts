export function sourceLocation(source: {
  page_number?: number | null;
  start_line: number;
  end_line: number;
}): string {
  const lines = `${source.start_line}–${source.end_line}줄`;
  // 이전 대화의 출처에는 페이지 필드가 없으며 줄 번호는 문서 전체 기준이다.
  return source.page_number == null
    ? lines
    : `${source.page_number}페이지 · 페이지 내 ${lines}`;
}
