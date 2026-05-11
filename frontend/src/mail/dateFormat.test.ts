import { describe, expect, it } from 'vitest';
import { formatDateByTimezone } from './api';

describe('formatDateByTimezone', () => {
  it('按指定时区格式化日期时间', () => {
    const formatted = formatDateByTimezone('2026-05-07T09:30:00Z', {
      locale: 'zh-CN',
      timezone: 'Asia/Shanghai',
    });

    expect(formatted).toContain('2026年5月7日');
    expect(formatted).toContain('17:30');
  });

  it('无效日期原样返回', () => {
    expect(formatDateByTimezone('not-a-date', { timezone: 'Asia/Shanghai' })).toBe('not-a-date');
  });
});
