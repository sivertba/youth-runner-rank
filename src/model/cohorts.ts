import generated from './generated.json'
import { baselineData } from './baseline'
import type { Confidence, GeneratedData, GridCohort, ModelData, Sex, Surface } from './types'

const observed = generated as unknown as GeneratedData

export const generatedData: GeneratedData = observed
export const baseline: ModelData = baselineData
export const hasObservedModel: boolean = observed.events.length > 0

/** Age window the published baseline can answer for, used when no cohort covers it. */
export const BASELINE_MIN_AGE_DAYS = Math.floor(10 * 365.2425)
export const BASELINE_MAX_AGE_DAYS = Math.floor(20 * 365.2425) - 1

const byKey = new Map<string, GridCohort>()
for (const cohort of observed.events) {
  byKey.set(`${cohort.eventId}|${cohort.sex}|${cohort.surface}`, cohort)
}

export function cohortKey(eventId: string, sex: Sex, surface: Surface): string {
  return `${eventId}|${sex}|${surface}`
}

export function findCohort(eventId: string, sex: Sex, surface: Surface): GridCohort | undefined {
  return byKey.get(cohortKey(eventId, sex, surface))
}

export function observedCohorts(): GridCohort[] {
  return observed.events
}

/** The widest age range any cohort can answer for, in days, including extrapolation. */
export function overallAgeRange(): [number, number] {
  if (!hasObservedModel) return [Math.floor(10 * 365.2425), Math.floor(20 * 365.2425) - 1]
  let low = Infinity
  let high = -Infinity
  for (const cohort of observed.events) {
    low = Math.min(low, cohort.supportedAgeDays[0])
    high = Math.max(high, cohort.supportedAgeDays[1])
  }
  return [low, high]
}

/**
 * Surfaces offered for this event and sex.
 *
 * Where a cohort exists, only surfaces with observed data are offered. Where none
 * exists, the event's declared surfaces are offered so the published benchmark can
 * answer rather than the interface refusing to run at all. That is the situation
 * for every athlete under about 13: no public source publishes exact dates of
 * birth for children in bulk, so the observed corpus simply has nothing there.
 */
export function surfacesFor(eventId: string, sex: Sex, declared: Surface[]): Surface[] {
  const withData = declared.filter((surface) => Boolean(findCohort(eventId, sex, surface)))
  return withData.length > 0 ? withData : declared
}

export interface EventAvailability {
  eventId: string
  /** True when an observed cohort backs this event and sex. */
  observed: boolean
  surfaces: Surface[]
  confidence: Confidence | null
  observations: number
  athletes: number
  minMedianLocalN: number
  supportedAgeDays: [number, number] | null
}

/**
 * What the installed corpus can support for one event, given the sex currently
 * selected. The interface uses this to label benchmark-only combinations, rather
 * than presenting them as if they were data-derived.
 */
export function eventAvailability(eventId: string, sex: Sex, declared: Surface[]): EventAvailability {
  const cohorts = declared
    .map((surface) => findCohort(eventId, sex, surface))
    .filter((cohort): cohort is GridCohort => Boolean(cohort))
  if (cohorts.length === 0) {
    return {
      eventId,
      observed: false,
      surfaces: declared,
      confidence: null,
      observations: 0,
      athletes: 0,
      minMedianLocalN: 0,
      supportedAgeDays: null,
    }
  }
  const rank: Record<Confidence, number> = { low: 0, moderate: 1, high: 2 }
  const best = cohorts.reduce((a, b) => (rank[b.confidence] > rank[a.confidence] ? b : a))
  return {
    eventId,
    observed: true,
    surfaces: cohorts.map((cohort) => cohort.surface),
    confidence: best.confidence,
    observations: cohorts.reduce((total, cohort) => total + cohort.observations, 0),
    athletes: cohorts.reduce((total, cohort) => total + cohort.athletes, 0),
    minMedianLocalN: Math.min(...cohorts.map((cohort) => cohort.medianLocalN)),
    supportedAgeDays: [Math.min(...cohorts.map((c) => c.supportedAgeDays[0])), Math.max(...cohorts.map((c) => c.supportedAgeDays[1]))],
  }
}

/** The widest age range with direct observations behind it, in days. */
export function observedAgeRange(): [number, number] {
  let low = Infinity
  let high = -Infinity
  for (const cohort of observed.events) {
    low = Math.min(low, cohort.observedAgeDays[0])
    high = Math.max(high, cohort.observedAgeDays[1])
  }
  if (!hasObservedModel) return [BASELINE_MIN_AGE_DAYS, BASELINE_MAX_AGE_DAYS]
  return [low, high]
}

export function baselineEvent(eventId: string): ModelData['events'][number] | undefined {
  return baseline.events.find((event) => event.eventId === eventId)
}