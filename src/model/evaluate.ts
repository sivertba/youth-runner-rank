import { ageInDays } from './age'
import { normalCdf, normalInverse } from './statistics'
import type { Anchor, Confidence, EvaluationInput, EvaluationResult, GridCohort, GridPoint, SexModel, TimingMethod } from './types'

/**
 * Smallest local count used when converting spread into a standard error. Kept low
 * on purpose: now that thinly observed ages are published, a single nearby result
 * must produce a visibly wide band rather than false precision.
 */
const MIN_EFFECTIVE_N = 2
/** Cap on the reported percentile band, so a one-observation age cannot imply absurd precision. */
const MAX_BAND = 30

function interpolate(anchors: Anchor[], age: number): { median: number; sigma: number } {
  if (age <= anchors[0].age) return anchors[0]
  const last = anchors[anchors.length - 1]
  if (age >= last.age) return last
  const upperIndex = anchors.findIndex((anchor) => anchor.age > age)
  const lower = anchors[upperIndex - 1]
  const upper = anchors[upperIndex]
  const fraction = (age - lower.age) / (upper.age - lower.age)
  return {
    median: lower.median + (upper.median - lower.median) * fraction,
    sigma: lower.sigma + (upper.sigma - lower.sigma) * fraction,
  }
}

export function timingCorrection(distance: number, method: TimingMethod): number {
  if (method === 'electronic') return 0
  if (distance <= 800) return 0.24
  if (distance <= 1500) return 0.3
  return 0.5
}

export function windCorrection(distance: number, wind: number | null, windAdjusted: boolean): number {
  if (!windAdjusted || wind === null) return 0
  return wind * (distance / 100) * 0.1
}

/**
 * Rank from the fast end: 0.5 means exactly the median, 0.9 means faster than
 * about 90% of comparable results. Ranking from the slow end would invert every
 * label the interface shows, so it is worth stating explicitly.
 */
export function percentile(adjustedSeconds: number, median: number, sigma: number): number {
  return 1 - normalCdf(Math.log(adjustedSeconds / median) / sigma)
}

/**
 * Percentile band driven by the sampling error of the local median rather than
 * by sigma alone. A cohort with a handful of observations must look uncertain.
 */
function percentileBand(adjustedSeconds: number, median: number, sigma: number, localN: number) {
  const effectiveN = Math.max(localN, MIN_EFFECTIVE_N)
  const standardError = sigma / Math.sqrt(effectiveN)
  const shift = normalInverse(0.975) * standardError
  const optimistic = percentile(adjustedSeconds, median * Math.exp(-shift), sigma)
  const pessimistic = percentile(adjustedSeconds, median * Math.exp(shift), sigma)
  return { optimistic, pessimistic }
}

function assemble(
  adjustedSeconds: number,
  distance: number,
  ageDays: number,
  median: number,
  sigma: number,
  fastBoundary: number,
  slowBoundary: number,
  confidence: Confidence,
  modelKind: EvaluationResult['modelKind'],
  timingAdjustment: number,
  windAdjustment: number,
  sampleSize: number,
  observations: number,
  athletes: number,
  observedAgeDays: [number, number] | null,
): EvaluationResult {
  const value = percentile(adjustedSeconds, median, sigma)
  const band = percentileBand(adjustedSeconds, median, sigma, sampleSize)
  const spread = Math.min(MAX_BAND, Math.max(0, Math.round((band.pessimistic - band.optimistic) * 50)))
  const rank = value * 100
  return {
    percentile: rank,
    percentileLow: Math.max(0, rank - spread),
    percentileHigh: Math.min(100, rank + spread),
    median,
    fastBoundary,
    slowBoundary,
    targets: {
      50: median,
      75: median * Math.exp(sigma * normalInverse(0.25)),
      90: fastBoundary,
    },
    paceSecondsPerKm: 1000 / (adjustedSeconds / distance),
    speedMetersPerSecond: distance / adjustedSeconds,
    adjustedSeconds,
    ageDays,
    confidence,
    modelKind,
    timingAdjustment,
    windAdjustment,
    sampleSize,
    observations,
    athletes,
    observedAgeDays,
    ageWithinObservedSpan: observedAgeDays
      ? ageDays >= observedAgeDays[0] && ageDays <= observedAgeDays[1]
      : false,
  }
}

export function evaluateBaseline(
  model: SexModel,
  input: EvaluationInput,
  distance: number,
  windAdjusted: boolean,
  confidence: Confidence,
): EvaluationResult {
  const ageDays = ageInDays(input.birthDate, input.raceDate)
  const age = ageDays / 365.2425
  if (age < 10 || age >= 20) throw new Error('Age must be from 10 years through the day before the 20th birthday.')
  const curve = interpolate(model.anchors, age)
  const timingAdjustment = timingCorrection(distance, input.timingMethod)
  const windAdjustment = windCorrection(distance, input.wind, windAdjusted)
  const adjustedSeconds = input.seconds + timingAdjustment + windAdjustment
  return assemble(
    adjustedSeconds,
    distance,
    ageDays,
    curve.median,
    curve.sigma,
    curve.median * Math.exp(curve.sigma * normalInverse(0.1)),
    curve.median * Math.exp(curve.sigma * normalInverse(0.9)),
    confidence,
    'baseline',
    timingAdjustment,
    windAdjustment,
    model.sampleSize,
    model.sampleSize,
    0,
    null,
  )
}

function interpolateGrid(points: GridPoint[], ageDays: number): GridPoint {
  const first = points[0]
  const last = points[points.length - 1]
  if (ageDays <= first[0]) return first
  if (ageDays >= last[0]) return last
  let low = 0
  let high = points.length - 1
  while (high - low > 1) {
    const middle = (low + high) >> 1
    if (points[middle][0] <= ageDays) low = middle
    else high = middle
  }
  const left = points[low]
  const right = points[high]
  const fraction = (ageDays - left[0]) / (right[0] - left[0])
  return [
    ageDays,
    left[1] + (right[1] - left[1]) * fraction,
    left[2] + (right[2] - left[2]) * fraction,
    left[3] + (right[3] - left[3]) * fraction,
    left[4] + (right[4] - left[4]) * fraction,
    Math.round(left[5] + (right[5] - left[5]) * fraction),
  ]
}

export function evaluateGrid(
  cohort: GridCohort,
  input: EvaluationInput,
  distance: number,
  windAdjusted: boolean,
): EvaluationResult {
  const ageDays = ageInDays(input.birthDate, input.raceDate)
  const [supportedLow, supportedHigh] = cohort.supportedAgeDays
  if (ageDays < supportedLow || ageDays > supportedHigh) {
    throw new Error('This age is outside the range this cohort covers.')
  }
  const point = interpolateGrid(cohort.grid, ageDays)
  const timingAdjustment = timingCorrection(distance, input.timingMethod)
  const windAdjustment = windCorrection(distance, input.wind, windAdjusted)
  const adjustedSeconds = input.seconds + timingAdjustment + windAdjustment
  return assemble(
    adjustedSeconds,
    distance,
    ageDays,
    point[2],
    point[4],
    point[1],
    point[3],
    cohort.confidence,
    'observed',
    timingAdjustment,
    windAdjustment,
    point[5],
    cohort.observations,
    cohort.athletes,
    cohort.observedAgeDays,
  )
}