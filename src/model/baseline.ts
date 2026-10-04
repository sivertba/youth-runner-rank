import type { Anchor, Sex, SexModel, Surface } from './types'

const ages = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]

function makeMedians(values: number[]): number[] {
  return values
}

function makeAnchors(medians: number[], sigma: number): Anchor[] {
  return ages.map((age, index) => ({ age, median: makeMedians(medians)[index], sigma: sigma * (1.08 - (index / ages.length) * 0.12) }))
}

const baseline: Record<string, { male: number[]; female: number[]; sigma: number }> = {
  '60m': { male: [9.8, 9.2, 8.7, 8.3, 7.9, 7.6, 7.4, 7.3, 7.3, 7.4], female: [10.4, 9.8, 9.3, 8.8, 8.4, 8.1, 7.9, 7.8, 7.8, 7.9], sigma: 0.1 },
  '100m': { male: [15.0, 14.1, 13.3, 12.7, 12.2, 11.8, 11.5, 11.3, 11.2, 11.2], female: [16.2, 15.3, 14.5, 13.8, 13.2, 12.8, 12.5, 12.3, 12.2, 12.2], sigma: 0.105 },
  '200m': { male: [32.0, 30.0, 28.3, 26.8, 25.5, 24.5, 23.7, 23.1, 22.8, 22.8], female: [35.0, 32.8, 31.0, 29.4, 28.0, 26.9, 26.0, 25.4, 25.0, 24.9], sigma: 0.105 },
  '300m': { male: [50.0, 47.0, 44.4, 42.0, 40.0, 38.3, 37.0, 36.0, 35.4, 35.2], female: [54.0, 50.8, 48.0, 45.5, 43.3, 41.5, 40.2, 39.2, 38.6, 38.4], sigma: 0.1 },
  '400m': { male: [69.0, 64.5, 60.8, 57.6, 54.9, 52.7, 51.0, 49.8, 49.0, 48.7], female: [76.0, 71.0, 66.8, 63.2, 60.1, 57.6, 55.6, 54.2, 53.3, 52.9], sigma: 0.1 },
  '600m': { male: [100.0, 93.0, 87.4, 82.7, 78.8, 75.6, 73.0, 71.1, 69.8, 69.2], female: [110.0, 102.2, 95.8, 90.4, 86.0, 82.4, 79.6, 77.5, 76.1, 75.4], sigma: 0.1 },
  '800m': { male: [140.0, 129.0, 120.0, 112.4, 106.0, 101.0, 97.2, 94.5, 92.7, 91.8], female: [155.0, 142.0, 131.5, 122.5, 115.2, 109.2, 104.5, 101.0, 98.5, 97.0], sigma: 0.095 },
  '1000m': { male: [175.0, 160.5, 148.5, 138.5, 130.0, 123.3, 118.0, 113.9, 110.9, 109.0], female: [195.0, 178.0, 164.0, 152.0, 142.3, 134.4, 128.0, 123.0, 119.3, 117.0], sigma: 0.095 },
  '1500m': { male: [280.0, 254.0, 232.0, 214.0, 198.8, 186.0, 175.5, 167.0, 160.4, 156.0], female: [300.0, 272.0, 249.0, 230.0, 214.0, 200.5, 189.5, 180.5, 173.5, 169.0], sigma: 0.09 },
  'mile': { male: [340.0, 308.0, 281.0, 259.0, 240.5, 225.0, 212.0, 201.5, 193.0, 187.0], female: [370.0, 335.0, 306.0, 282.0, 262.0, 245.5, 231.5, 220.0, 211.0, 205.0], sigma: 0.09 },
  '3000m': { male: [620.0, 557.0, 505.0, 462.0, 426.0, 396.0, 371.0, 350.0, 333.0, 321.0], female: [680.0, 611.0, 553.0, 505.0, 465.0, 432.0, 405.0, 383.0, 365.0, 352.0], sigma: 0.085 },
  '5k': { male: [1080.0, 970.0, 879.0, 804.0, 742.0, 690.0, 647.0, 611.0, 582.0, 562.0], female: [1180.0, 1060.0, 960.0, 879.0, 812.0, 755.0, 709.0, 670.0, 638.0, 617.0], sigma: 0.08 },
  '10k': { male: [2280.0, 2050.0, 1857.0, 1700.0, 1568.0, 1458.0, 1368.0, 1292.0, 1231.0, 1190.0], female: [2480.0, 2230.0, 2020.0, 1849.0, 1705.0, 1585.0, 1487.0, 1404.0, 1337.0, 1292.0], sigma: 0.075 },
}

function model(medians: number[], sigma: number): SexModel {
  return { anchors: makeAnchors(medians, sigma), sampleSize: 0 }
}

function allSurfaces(eventId: string): Record<Surface, Record<Sex, SexModel>> {
  const value = model(baseline[eventId].male, baseline[eventId].sigma)
  const female = model(baseline[eventId].female, baseline[eventId].sigma)
  return {
    'outdoor-track': { male: value, female },
    'indoor-track': { male: value, female },
    road: { male: value, female },
    'cross-country': { male: value, female },
  }
}

export const baselineData = {
  version: '2026.09.1',
  generatedAt: '2026-09-24T00:00:00.000Z',
  kind: 'baseline' as const,
  confidence: 'low' as const,
  sourceLabel: 'Published age-group benchmarks and a conservative youth-competition model; no representative open exact-DOB corpus',
  events: [
    { eventId: '60m', surfaces: allSurfaces('60m') },
    { eventId: '100m', surfaces: allSurfaces('100m') },
    { eventId: '200m', surfaces: allSurfaces('200m') },
    { eventId: '300m', surfaces: allSurfaces('300m') },
    { eventId: '400m', surfaces: allSurfaces('400m') },
    { eventId: '600m', surfaces: allSurfaces('600m') },
    { eventId: '800m', surfaces: allSurfaces('800m') },
    { eventId: '1000m', surfaces: allSurfaces('1000m') },
    { eventId: '1500m', surfaces: allSurfaces('1500m') },
    { eventId: 'mile', surfaces: allSurfaces('mile') },
    { eventId: '3000m', surfaces: allSurfaces('3000m') },
    { eventId: '5k', surfaces: allSurfaces('5k') },
    { eventId: '10k', surfaces: allSurfaces('10k') },
  ],
}

export type BaselineData = typeof baselineData
