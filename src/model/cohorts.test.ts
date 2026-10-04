import { describe, expect, it } from 'vitest'
import { eventAvailability, findCohort, hasObservedModel, overallAgeRange, surfacesFor } from './cohorts'
import { generatedData } from './cohorts'
import { EVENTS } from './events'
import type { GridCohort, Sex } from './types'

const cohorts: GridCohort[] = generatedData.events

describe('installed corpus', () => {
  it('has a fitted model', () => {
    expect(hasObservedModel).toBe(true)
    expect(cohorts.length).toBeGreaterThan(10)
  })

  it('stores quantiles in a sane order at every grid point', () => {
    for (const cohort of cohorts) {
      for (const point of cohort.grid) {
        expect(point[1], `${cohort.eventId} fast boundary`).toBeLessThan(point[2])
        expect(point[2], `${cohort.eventId} median`).toBeLessThan(point[3])
        expect(point[1]).toBeGreaterThan(0)
      }
    }
  })

  it('keeps every median monotone in age', () => {
    for (const cohort of cohorts) {
      for (let index = 1; index < cohort.grid.length; index += 1) {
        expect(
          cohort.grid[index][2],
          `${cohort.eventId}/${cohort.sex}/${cohort.surface} got slower with age`,
        ).toBeLessThanOrEqual(cohort.grid[index - 1][2] + 1e-9)
      }
    }
  })

  it('orders grid ages strictly increasing and inside the supported range', () => {
    for (const cohort of cohorts) {
      const [low, high] = cohort.supportedAgeDays
      for (let index = 0; index < cohort.grid.length; index += 1) {
        const age = cohort.grid[index][0]
        expect(age).toBeGreaterThanOrEqual(low)
        expect(age).toBeLessThanOrEqual(high)
        if (index > 0) expect(age).toBeGreaterThan(cohort.grid[index - 1][0])
      }
    }
  })

  it('keeps sigma inside the fitted bounds', () => {
    for (const cohort of cohorts) {
      for (const point of cohort.grid) {
        expect(point[4]).toBeGreaterThanOrEqual(0.02)
        expect(point[4]).toBeLessThanOrEqual(0.35)
      }
    }
  })

  it('extends the observed span by no more than the validated extrapolation limit', () => {
    const minAge = Math.floor(10 * 365.2425)
    const maxAge = Math.floor(20 * 365.2425) - 1
    const limit = generatedData.corpus.maxBackExtrapolationYears * 365.2425
    for (const cohort of cohorts) {
      const [observedLow, observedHigh] = cohort.observedAgeDays
      const [supportedLow, supportedHigh] = cohort.supportedAgeDays
      expect(supportedLow).toBeGreaterThanOrEqual(minAge)
      expect(supportedHigh).toBeLessThanOrEqual(maxAge)
      expect(supportedLow).toBeLessThanOrEqual(supportedHigh)
      // The observed span is always answered.
      expect(supportedLow).toBeLessThanOrEqual(observedLow)
      expect(supportedHigh).toBeGreaterThanOrEqual(observedHigh)
      // Beyond it, no further than the measured extrapolation limit.
      expect(observedLow - supportedLow).toBeLessThanOrEqual(limit + 7)
      expect(supportedHigh - observedHigh).toBeLessThanOrEqual(limit + 7)
      // And the grid spans exactly the window the app gates on.
      expect(cohort.grid.length).toBeGreaterThan(20)
      expect(cohort.grid[0][0]).toBe(supportedLow)
      expect(cohort.grid[cohort.grid.length - 1][0]).toBe(supportedHigh)
    }
  })

  it('stays monotone through the join between fitted and extrapolated regions', () => {
    // The extrapolated arms are anchored to the curve's end values; if that anchor
    // ever drifts, the join becomes a step and the curve reverses.
    for (const cohort of cohorts) {
      const medians = cohort.grid.map((point) => point[2])
      for (let index = 1; index < medians.length; index += 1) {
        expect(medians[index]).toBeLessThanOrEqual(medians[index - 1] + 1e-9)
      }
    }
  })

  it('only claims confidence that matches its support', () => {
    for (const cohort of cohorts) {
      expect(['low', 'moderate', 'high']).toContain(cohort.confidence)
      if (cohort.confidence === 'high') {
        expect(cohort.observations).toBeGreaterThanOrEqual(5000)
        expect(cohort.medianLocalN).toBeGreaterThanOrEqual(150)
      }
    }
  })
})

describe('availability gating', () => {
  it('offers a cohort for every event it claims', () => {
    for (const cohort of cohorts) {
      expect(findCohort(cohort.eventId, cohort.sex, cohort.surface)).toBeDefined()
    }
  })

  it('marks events with no cohort as benchmark-only rather than hiding them', () => {
    const sex: Sex = 'female'
    const unobserved = EVENTS.filter((event) => !eventAvailability(event.id, sex, event.surfaces).observed)
    // The corpus has no 5 km or mile data at all; those must stay reachable via
    // the published benchmark, clearly flagged, rather than vanishing.
    expect(unobserved.map((event) => event.id)).toContain('5k')
    for (const event of unobserved) {
      const availability = eventAvailability(event.id, sex, event.surfaces)
      expect(availability.observed).toBe(false)
      expect(availability.observations).toBe(0)
      expect(availability.supportedAgeDays).toBeNull()
      // Declared surfaces are offered so the benchmark can answer.
      expect(availability.surfaces).toEqual(event.surfaces)
    }
  })

  it('never offers road or cross-country when a cohort exists', () => {
    const sex: Sex = 'female'
    for (const event of EVENTS) {
      if (!eventAvailability(event.id, sex, event.surfaces).observed) continue
      for (const surface of surfacesFor(event.id, sex, event.surfaces)) {
        expect(['outdoor-track', 'indoor-track']).toContain(surface)
      }
    }
  })

  it('reports an overall age range inside 10 to 20 years', () => {
    const [low, high] = overallAgeRange()
    expect(low).toBeGreaterThanOrEqual(Math.floor(10 * 365.2425))
    expect(high).toBeLessThanOrEqual(Math.floor(20 * 365.2425))
  })
})