import { describe, expect, it } from 'vitest'
import { normalCdf, normalInverse } from './statistics'

describe('normal distribution helpers', () => {
  it('maps zero to the median', () => {
    expect(normalCdf(0)).toBeCloseTo(0.5, 6)
  })

  it('inverts common percentiles', () => {
    expect(normalInverse(0.5)).toBeCloseTo(0, 5)
    expect(normalInverse(0.75)).toBeCloseTo(0.67449, 4)
    expect(normalInverse(0.975)).toBeCloseTo(1.95996, 4)
  })

  it('round-trips the normal CDF', () => {
    const value = -1.234
    expect(normalInverse(normalCdf(value))).toBeCloseTo(value, 4)
  })
})
