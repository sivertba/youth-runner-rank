import { describe, expect, it } from 'vitest'
import { evaluateBaseline, evaluateGrid, percentile, timingCorrection, windCorrection } from './evaluate'
import type { EvaluationInput, GridCohort, SexModel } from './types'

const model: SexModel = {
  anchors: [
    { age: 10, median: 20, sigma: 0.1 },
    { age: 11, median: 18, sigma: 0.1 },
    { age: 20, median: 16, sigma: 0.1 },
  ],
  sampleSize: 1000,
}

const input: EvaluationInput = {
  birthDate: '2014-01-01',
  raceDate: '2025-01-01',
  sex: 'male',
  eventId: 'test',
  surface: 'outdoor-track',
  seconds: 18,
  timingMethod: 'electronic',
  wind: 0,
}

function interpolateMedian(cohort: GridCohort, ageDays: number): number {
  let low = 0
  let high = cohort.grid.length - 1
  while (high - low > 1) {
    const middle = (low + high) >> 1
    if (cohort.grid[middle][0] <= ageDays) low = middle
    else high = middle
  }
  const a = cohort.grid[low]
  const b = cohort.grid[high]
  const fraction = (ageDays - a[0]) / (b[0] - a[0])
  return a[2] + (b[2] - a[2]) * fraction
}

function gridCohort(overrides: Partial<GridCohort> = {}): GridCohort {
  return {
    eventId: '100m',
    sex: 'female',
    surface: 'outdoor-track',
    observations: 6000,
    athletes: 5900,
    sigma: 0.04,
    observedAgeDays: [4000, 7100],
    supportedAgeDays: [3820, 7280],
    medianLocalN: 400,
    confidence: 'high',
    grid: [
      [4000, 11.9, 12.3, 12.72, 0.04, 380],
      [4700, 11.6, 12.0, 12.41, 0.04, 520],
      [5400, 11.4, 11.78, 12.18, 0.04, 640],
      [6100, 11.3, 11.66, 12.05, 0.04, 610],
      [6800, 11.25, 11.6, 11.99, 0.04, 480],
    ],
    ...overrides,
  }
}

describe('percentile direction', () => {
  it('ranks a fast performance above the median, not below it', () => {
    const median = 12.0
    const sigma = 0.04
    // One sigma faster must land at the 84th percentile from the fast end, and
    // one sigma slower at the 16th. Getting this backwards was the single
    // worst defect in the old model: a fast run scored low.
    expect(percentile(median * Math.exp(-sigma), median, sigma)).toBeCloseTo(0.8413, 3)
    expect(percentile(median * Math.exp(sigma), median, sigma)).toBeCloseTo(0.1587, 3)
    expect(percentile(median, median, sigma)).toBeCloseTo(0.5, 6)
    expect(percentile(median * Math.exp(-2 * sigma), median, sigma)).toBeGreaterThan(0.97)
  })

  it('is monotone in the finish time', () => {
    const times = [11.0, 11.5, 12.0, 12.5, 13.0]
    const ranks = times.map((seconds) => percentile(seconds, 12.0, 0.04))
    for (let index = 1; index < ranks.length; index += 1) {
      expect(ranks[index]).toBeLessThan(ranks[index - 1])
    }
  })
})

describe('evaluateBaseline', () => {
  it('returns the median at exactly 50 percent', () => {
    const result = evaluateBaseline(model, input, 100, false, 'high')
    expect(result.percentile).toBeCloseTo(50, 2)
    expect(result.median).toBeCloseTo(18, 3)
  })

  it('reports the fast 10% target as faster than the median', () => {
    const result = evaluateBaseline(model, input, 100, false, 'high')
    expect(result.targets[90]).toBeLessThan(result.targets[50])
    expect(result.fastBoundary).toBeCloseTo(result.targets[90], 6)
    expect(result.slowBoundary).toBeGreaterThan(result.median)
  })

  it('marks the result as baseline-derived', () => {
    expect(evaluateBaseline(model, input, 100, false, 'low').modelKind).toBe('baseline')
  })

  it('applies timing and wind corrections', () => {
    const result = evaluateBaseline({ ...model, sampleSize: 0 }, { ...input, timingMethod: 'hand', wind: 2 }, 100, true, 'low')
    expect(result.timingAdjustment).toBe(0.24)
    expect(result.windAdjustment).toBeCloseTo(0.2, 5)
    expect(result.adjustedSeconds).toBeCloseTo(18.44, 5)
  })

  it('rejects ages outside the supported range', () => {
    expect(() => evaluateBaseline(model, { ...input, birthDate: '2016-01-01' }, 100, false, 'high')).toThrow('Age must be')
  })
})

describe('evaluateGrid', () => {
  const gridInput = (seconds: number): EvaluationInput => ({ ...input, sex: 'female', eventId: '100m', seconds })

  it('scores a fast run highly and a slow run poorly', () => {
    const cohort = gridCohort()
    const median = interpolateMedian(cohort, 4018)
    const fast = evaluateGrid(cohort, gridInput(median * Math.exp(-1.5 * 0.04)), 100, false)
    const slow = evaluateGrid(cohort, gridInput(median * Math.exp(1.5 * 0.04)), 100, false)
    expect(fast.percentile).toBeGreaterThan(90)
    expect(slow.percentile).toBeLessThan(10)
  })

  it('points the 90th-percentile target at the fast boundary', () => {
    const result = evaluateGrid(gridCohort(), gridInput(interpolateMedian(gridCohort(), 4018)), 100, false)
    // Guards the historical bug where the fast-10% target returned the slow boundary.
    expect(result.targets[90]).toBe(result.fastBoundary)
    expect(result.targets[90]).toBeLessThan(result.targets[50])
  })

  it('carries the cohort confidence and corpus counts', () => {
    const result = evaluateGrid(gridCohort({ confidence: 'moderate' }), gridInput(11.8), 100, false)
    expect(result.confidence).toBe('moderate')
    expect(result.modelKind).toBe('observed')
    expect(result.observations).toBe(6000)
    expect(result.athletes).toBe(5900)
  })

  it('widens the band when local observations are thin', () => {
    const dense = evaluateGrid(gridCohort(), gridInput(11.9), 100, false)
    const thin = evaluateGrid(
      gridCohort({
        grid: gridCohort().grid.map((point) => [point[0], point[1], point[2], point[3], point[4], 1] as GridCohort['grid'][number]),
        medianLocalN: 1,
      }),
      gridInput(11.9),
      100,
      false,
    )
    const width = (result: { percentileLow: number; percentileHigh: number }) => result.percentileHigh - result.percentileLow
    expect(width(thin)).toBeGreaterThan(width(dense))
  })

  it('flags ages outside the observed span as extrapolated', () => {
    const cohort = gridCohort({ observedAgeDays: [5000, 7100] })
    // 2008-01-01 -> 2025-01-01 is 6209 days, inside the observed span.
    const inside = evaluateGrid(cohort, { ...gridInput(11.8), birthDate: '2008-01-01', raceDate: '2025-01-01' }, 100, false)
    // 2014-01-01 -> 2025-01-01 is 4018 days, before it.
    const before = evaluateGrid(cohort, gridInput(11.8), 100, false)
    expect(inside.ageWithinObservedSpan).toBe(true)
    expect(before.ageWithinObservedSpan).toBe(false)
  })

  it('refuses ages outside the supported range', () => {
    expect(() => evaluateGrid(gridCohort(), { ...gridInput(11.8), birthDate: '2018-01-01' }, 100, false)).toThrow('outside the range')
  })

  it('interpolates between grid points', () => {
    // 2011-01-01 -> 2025-01-01 is 5117 days, between the 4700 and 5400 anchors.
    const result = evaluateGrid(gridCohort(), { ...gridInput(11.8), birthDate: '2011-01-01', raceDate: '2025-01-01' }, 100, false)
    expect(result.median).toBeLessThan(12.0)
    expect(result.median).toBeGreaterThan(11.78)
  })

  it('moves the expected time by one day', () => {
    const cohort = gridCohort()
    const first = evaluateGrid(cohort, { ...gridInput(11.8), birthDate: '2011-01-01', raceDate: '2025-01-01' }, 100, false)
    const second = evaluateGrid(cohort, { ...gridInput(11.8), birthDate: '2011-01-01', raceDate: '2025-01-02' }, 100, false)
    expect(first.ageDays).toBe(second.ageDays - 1)
    expect(first.median).not.toBe(second.median)
  })
})

describe('rule corrections', () => {
  it('uses event-specific hand timing corrections', () => {
    expect(timingCorrection(400, 'hand')).toBe(0.24)
    expect(timingCorrection(1500, 'hand')).toBe(0.3)
    expect(timingCorrection(5000, 'hand')).toBe(0.5)
  })

  it('ignores wind for events without a legal-wind rule', () => {
    expect(windCorrection(200, 2, false)).toBe(0)
  })
})