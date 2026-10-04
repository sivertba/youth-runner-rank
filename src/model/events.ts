import type { EventDefinition } from './types'

// Surfaces listed here are candidates. The interface narrows them to whatever the
// installed corpus can actually evaluate, so an event with no data never appears
// in the picker. Road and cross-country are listed where they are legitimate
// competitions but currently have no cohort: the World Athletics calendar results
// feed publishes no road or cross-country performances at all.
export const EVENTS: EventDefinition[] = [
  { id: '60m', name: '60 metres', distance: 60, unit: 'm', surfaces: ['indoor-track'], windAdjusted: true },
  { id: '100m', name: '100 metres', distance: 100, unit: 'm', surfaces: ['outdoor-track'], windAdjusted: true },
  { id: '200m', name: '200 metres', distance: 200, unit: 'm', surfaces: ['outdoor-track', 'indoor-track'], windAdjusted: false },
  { id: '300m', name: '300 metres', distance: 300, unit: 'm', surfaces: ['outdoor-track', 'indoor-track'], windAdjusted: false },
  { id: '400m', name: '400 metres', distance: 400, unit: 'm', surfaces: ['outdoor-track', 'indoor-track'], windAdjusted: false },
  { id: '600m', name: '600 metres', distance: 600, unit: 'm', surfaces: ['outdoor-track', 'indoor-track'], windAdjusted: false },
  { id: '800m', name: '800 metres', distance: 800, unit: 'm', surfaces: ['outdoor-track', 'indoor-track'], windAdjusted: false },
  { id: '1000m', name: '1000 metres', distance: 1000, unit: 'm', surfaces: ['outdoor-track', 'indoor-track'], windAdjusted: false },
  { id: '1500m', name: '1500 metres', distance: 1500, unit: 'm', surfaces: ['outdoor-track', 'indoor-track'], windAdjusted: false },
  { id: 'mile', name: '1 mile', distance: 1609.344, unit: 'mile', surfaces: ['road', 'outdoor-track'], windAdjusted: false },
  { id: '3000m', name: '3000 metres', distance: 3000, unit: 'm', surfaces: ['outdoor-track', 'indoor-track', 'cross-country'], windAdjusted: false },
  { id: '5k', name: '5 kilometres', distance: 5000, unit: 'km', surfaces: ['outdoor-track', 'road', 'cross-country'], windAdjusted: false },
  { id: '10k', name: '10 kilometres', distance: 10000, unit: 'km', surfaces: ['outdoor-track', 'road', 'cross-country'], windAdjusted: false },
]
