import { describe, expect, it } from 'vitest';
import { sourceLocation } from './sourceLocation';

describe('인용 원문 위치', () => {
  it('PDF의 같은 줄 번호라도 다른 페이지를 구분한다', () => {
    const lines = { start_line: 1, end_line: 3 };
    expect(sourceLocation({ ...lines, page_number: 2 })).toBe(
      '2페이지 · 페이지 내 1–3줄',
    );
    expect(sourceLocation({ ...lines, page_number: 3 })).toBe(
      '3페이지 · 페이지 내 1–3줄',
    );
  });

  it('페이지를 저장하지 않은 이전 채팅은 문서 전체 줄 범위를 표시한다', () => {
    expect(sourceLocation({ start_line: 7, end_line: 8 })).toBe('7–8줄');
  });
});
