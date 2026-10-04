export type Sex = 'male' | 'female'
export type Surface = 'outdoor-track' | 'indoor-track' | 'road' | 'cross-country'
export type TimingMethod = 'electronic' | 'hand'
export type Confidence = 'low' | 'moderate' | 'high'
export type ModelKind = 'observed' | 'baseline'

export interface Anchor {
  age: number
  median: number
  sigma: number
}

export interface EventDefinition {
  id: string
  name: string
  distance: number
  unit: 'm' | 'mile' | 'km'
  surfaces: Surface[]
  windAdjusted: boolean
}

export interface SexModel {
  anchors: Anchor[]
  sampleSize: number
}

export interface EventModel {
  eventId: string
  surfaces: Record<Surface, Record<Sex, SexModel>>
}

export interface ModelData {
  version: string
  generatedAt: string
  kind: ModelKind
  confidence: Confidence
  sourceLabel: string
  events: EventModel[]
}

/**
 * One point on a fitted age-performance curve.
 *
 * Index 1 is the *fast* boundary, i.e. the 10th percentile of times, which is
 * the time an athlete must beat to be inside the fastest 10%. Index 3 is the
 * slow boundary. Naming them explicitly matters: the earlier artifact used the
 * bare names p10/p90, which is exactly the kind of ambiguity that made a fast
 * target look like a slow one.
 */
export type GridPoint = [
  ageDays: number,
  fastBoundarySeconds: number,
  medianSeconds: number,
  slowBoundarySeconds: number,
  sigma: number,
  localObservations: number,
]

export interface GridCohort {
  eventId: string
  sex: Sex
  surface: Surface
  observations: number
  athletes: number
  sigma: number
  /** Age range in days where this cohort actually has observations. */
  observedAgeDays: [number, number]
  /** Age range in days the model will answer for (observed range plus a margin). */
  supportedAgeDays: [number, number]
  medianLocalN: number
  confidence: Confidence
  grid: GridPoint[]
}

export interface CorpusSummary {
  observations: number
  athletes: number
  startDate: string
  endDate: string
  sources: string[]
  federations: number
  cohorts: number
  levels: Record<string, number>
  heldOutSelectionObservations: number
  youngAgeHistogram: Record<string, number>
  gridDays: number
  /** Local observation count below which an age is flagged as thinly observed. */
  supportFloorN: number
  maxBackExtrapolationYears: number
  method: string
}

export interface GeneratedData {
  version: string
  generatedAt: string
  kind: 'observed'
  confidence: Confidence
  sourceLabel: string
  corpus: CorpusSummary
  events: GridCohort[]
}

export interface EvaluationInput {
  birthDate: string
  raceDate: string
  sex: Sex
  eventId: string
  surface: Surface
  seconds: number
  timingMethod: TimingMethod
  wind: number | null
}

export interface EvaluationResult {
  /** Rank from the fast end: 90 means faster than about 90% of comparable results. */
  percentile: number
  percentileLow: number
  percentileHigh: number
  median: number
  fastBoundary: number
  slowBoundary: number
  targets: Record<50 | 75 | 90, number>
  paceSecondsPerKm: number
  speedMetersPerSecond: number
  adjustedSeconds: number
  ageDays: number
  confidence: Confidence
  modelKind: ModelKind
  timingAdjustment: number
  windAdjustment: number
  /** Observations within the local age window behind this point. */
  sampleSize: number
  observations: number
  athletes: number
  observedAgeDays: [number, number] | null
  /** False when the athlete's age falls in the extrapolation margin, not the observed span. */
  ageWithinObservedSpan: boolean
}