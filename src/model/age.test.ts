import { describe, expect, it } from 'vitest'
import { ageInDays, formatAge, parseDate } from './age'

describe('ageInDays', () => {
  it('distinguishes adjacent birthdays by one day', () => {
    expect(ageInDays('2014-12-31', '2026-01-01') - ageInDays('2015-01-01', '2026-01-01')).toBe(1)
  })

  it('handles leap days', () => {
    expect(ageInDays('2016-02-29', '2025-02-28')).toBe(3287)
  })

  it('rejects impossible dates', () => {
    expect(() => parseDate('2025-02-30')).toThrow('real calendar date')
  })

  it('rejects a race before birth', () => {
    expect(() => ageInDays('2025-01-01', '2024-12-31')).toThrow('Race date must be after')
  })

  it('formats a useful exact age', () => {
    expect(formatAge(ageInDays('2014-12-22', '2026-01-01'))).toMatch(/11 years/)
  })
})
