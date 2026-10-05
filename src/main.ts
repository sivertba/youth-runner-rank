import '@fontsource/barlow-condensed/latin-600.css'
import '@fontsource/barlow-condensed/latin-700.css'
import '@fontsource/barlow-condensed/latin-800.css'
import '@fontsource/barlow-condensed/latin-700-italic.css'
import '@fontsource/dm-sans/latin-400.css'
import '@fontsource/dm-sans/latin-500.css'
import '@fontsource/dm-sans/latin-600.css'
import '@fontsource/dm-sans/latin-700.css'
import './style.css'
import { formatAge, ageInDays } from './model/age'
import { EVENTS } from './model/events'
import { evaluateBaseline, evaluateGrid } from './model/evaluate'
import { BASELINE_MAX_AGE_DAYS, BASELINE_MIN_AGE_DAYS, baselineEvent, eventAvailability, findCohort, generatedData, observedAgeRange, overallAgeRange, surfacesFor } from './model'
import type { Confidence, EvaluationInput, EvaluationResult, Sex, Surface, TimingMethod } from './model/types'

const STORAGE_KEY = 'fairlap-form'
const URL_PARAMS = ['sex', 'birthDate', 'raceDate', 'event', 'surface', 'minutes', 'seconds', 'timing', 'wind'] as const
type UrlParam = typeof URL_PARAMS[number]

function getUrlParams(): Partial<Record<UrlParam, string>> {
  const params = new URLSearchParams(window.location.search)
  const result: Partial<Record<UrlParam, string>> = {}
  for (const key of URL_PARAMS) {
    const value = params.get(key)
    if (value !== null) result[key] = value
  }
  return result
}

function setUrlParams(params: Partial<Record<UrlParam, string>>): void {
  const url = new URL(window.location.href)
  for (const key of URL_PARAMS) {
    if (params[key]) url.searchParams.set(key, params[key])
    else url.searchParams.delete(key)
  }
  window.history.replaceState({}, '', url)
}

function loadFormState(): Partial<Record<UrlParam, string>> {
  const urlParams = getUrlParams()
  const stored = localStorage.getItem(STORAGE_KEY)
  const storedParams = stored ? JSON.parse(stored) : {}
  return { ...storedParams, ...urlParams }
}

function saveFormState(params: Partial<Record<UrlParam, string>>): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(params))
  setUrlParams(params)
}

function copyToClipboard(text: string): Promise<void> {
  return navigator.clipboard.writeText(text)
}

function showToast(message: string): void {
  const existing = document.querySelector('.toast')
  if (existing) existing.remove()
  const toast = document.createElement('div')
  toast.className = 'toast'
  toast.textContent = message
  document.body.appendChild(toast)
  requestAnimationFrame(() => toast.classList.add('show'))
  setTimeout(() => {
    toast.classList.remove('show')
    setTimeout(() => toast.remove(), 200)
  }, 2500)
}

const surfaceLabels: Record<Surface, string> = {
  'outdoor-track': 'Outdoor track',
  'indoor-track': 'Indoor track',
  road: 'Road race',
  'cross-country': 'Cross-country',
}

function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds)
  const minutes = Math.floor(safe / 60)
  const remaining = safe - minutes * 60
  return minutes > 0 ? `${minutes}:${remaining.toFixed(2).padStart(5, '0')}` : remaining.toFixed(2)
}

function formatPace(secondsPerKm: number): string {
  const safe = Math.max(0, secondsPerKm)
  const minutes = Math.floor(safe / 60)
  const remaining = Math.round((safe - minutes * 60) * 100) / 100
  return `${minutes}:${remaining.toFixed(2).padStart(5, '0')} /km`
}

function resultLabel(percentile: number): string {
  if (percentile >= 95) return 'Outstanding'
  if (percentile >= 80) return 'Strong'
  if (percentile >= 60) return 'Above median'
  if (percentile >= 40) return 'Around median'
  if (percentile >= 20) return 'Developing'
  return 'Building the basics'
}

function confidenceLabel(confidence: Confidence): string {
  if (confidence === 'high') return 'High confidence'
  if (confidence === 'moderate') return 'Moderate confidence'
  return 'Low confidence'
}

/**
 * Every event stays available, but ones the corpus cannot evaluate are labelled
 * as benchmark-only so nobody mistakes a published age-group figure for a result
 * derived from real performances.
 */
function availableEvents(sex: Sex): typeof EVENTS {
  return EVENTS.filter((event) => eventAvailability(event.id, sex, event.surfaces).observed || baselineEvent(event.id))
}

function eventOptions(sex: Sex): string {
  return availableEvents(sex)
    .map((event) => {
      const observed = eventAvailability(event.id, sex, event.surfaces).observed
      return `<option value="${event.id}">${event.name}${observed ? '' : ' — benchmark only'}</option>`
    })
    .join('')
}

function years(days: number): string {
  return (days / 365.2425).toFixed(1)
}

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header class="site-header">
    <a class="brand" href="#top" aria-label="FairLap home">
      <span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span>
      <span>FairLap</span>
    </a>
    <nav aria-label="Main navigation">
      <a href="#how-it-works">How it works</a>
      <a href="#method">Data & method</a>
    </nav>
    <a class="header-cta" href="#calculator">Compare a run</a>
  </header>

  <main id="top">
    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-grid"></div>
      <div class="hero-copy">
        <div class="eyebrow"><span></span> Fair comparison. Every day.</div>
        <h1 id="hero-title">Your age.<br><em>Your race.</em><br>Your number.</h1>
        <p class="hero-lead">See how a run compares with youth athletes at nearly the same age—not just the same year group.</p>
        <a class="primary-button" href="#calculator">Compare my run <span aria-hidden="true">→</span></a>
        <div class="privacy-note"><span aria-hidden="true">◇</span> Your birth date and result stay in your browser.</div>
      </div>
      <div class="hero-visual" aria-label="Example age-adjusted comparison">
        <div class="track-card">
          <div class="track-lines"><i></i><i></i><i></i><i></i></div>
          <div class="runner"><span></span><b>1</b></div>
          <div class="track-card-top"><span>100 metres</span><span>Outdoor</span></div>
          <div class="track-card-time">13.42<small>s</small></div>
          <div class="mini-gauge"><i style="width: 78%"></i><b style="left: 78%"></b></div>
          <div class="mini-labels"><span>Needs building</span><span>Elite</span></div>
        </div>
        <div class="floating-pill pill-one"><strong>15y 364d</strong><span>Exact age</span></div>
        <div class="floating-pill pill-two"><strong>78th</strong><span>Percentile</span></div>
      </div>
    </section>

    <section class="age-proof" aria-label="One-day age adjustment">
      <div><span>Born 01 Jan</span><strong>Age 15y 359d</strong></div>
      <div class="age-arrow" aria-hidden="true">→</div>
      <div><span>Born 22 Dec</span><strong>Age 15y 10d</strong></div>
      <div class="age-proof-message"><span>Same category.</span><strong>Different reference points.</strong></div>
    </section>

    <section class="calculator-section" id="calculator" aria-labelledby="calculator-title">
      <div class="section-intro">
        <div>
          <div class="eyebrow dark"><span></span> Your result</div>
          <h2 id="calculator-title">Compare a race</h2>
        </div>
<p>Enter four details. We’ll handle the age adjustment and show both the estimate and its uncertainty.</p>
        </div>
        <p class="coverage-note" id="coverage-note"></p>

      <div class="calculator-card">
        <form id="run-form" novalidate>
          <fieldset>
            <legend><span>1</span> About you</legend>
            <div class="field-grid two">
              <label>Gender
                <select id="sex" name="sex">
                  <option value="female">Female</option>
                  <option value="male">Male</option>
                </select>
              </label>
              <label>Date of birth
                <input id="birth-date" name="birthDate" type="date" required min="2006-01-01" />
              </label>
              <label>Race date
                <input id="race-date" name="raceDate" type="date" required />
              </label>
              <div class="age-display" aria-live="polite">
                <span>Exact age on race day</span>
                <strong id="age-preview">Enter both dates</strong>
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend><span>2</span> The race</legend>
            <div class="field-grid three">
              <label>Event
                <select id="event" name="event">${eventOptions('female')}</select>
              </label>
              <label>Surface
                <select id="surface" name="surface"></select>
              </label>
              <label>Finish time
                <div class="time-inputs">
                  <input id="minutes" name="minutes" type="number" inputmode="numeric" min="0" max="59" value="0" aria-label="Minutes" />
                  <b>:</b>
                  <input id="seconds" name="seconds" type="number" inputmode="numeric" min="0" max="59.99" step="0.01" value="0.00" aria-label="Seconds" />
                  <span>min : sec</span>
                </div>
              </label>
            </div>
          </fieldset>

          <details class="conditions">
            <summary><span><b>+</b> Timing, wind & conditions</span><small>Optional</small></summary>
            <div class="field-grid three">
              <label>Timing method
                <select id="timing" name="timing">
                  <option value="electronic">Automatic electronic</option>
                  <option value="hand">Hand timed</option>
                </select>
              </label>
              <label id="wind-field">Wind <span>m/s</span>
                <input id="wind" name="wind" type="number" min="-5" max="5" step="0.1" placeholder="e.g. +1.2" />
              </label>
              <div class="conditions-note">Use + for tailwind and − for headwind. Wind is used for legal 60m and 100m comparisons.</div>
            </div>
          </details>

          <div id="form-error" class="form-error" role="alert" hidden></div>
          <button class="submit-button" type="submit">Calculate my result <span aria-hidden="true">↗</span></button>
        </form>
        <div id="result" class="result-panel" aria-live="polite"></div>
      </div>
    </section>

    <section class="how-section" id="how-it-works" aria-labelledby="how-title">
      <div class="section-intro centered">
        <div class="eyebrow dark"><span></span> Made for clarity</div>
        <h2 id="how-title">One run. Clear context.</h2>
        <p>We keep the calculation rigorous and the language human.</p>
      </div>
      <div class="feature-grid">
        <article><span>01</span><div class="feature-icon">◷</div><h3>Age to the day</h3><p>A December-born athlete and a January-born athlete never share the same reference point.</p></article>
        <article><span>02</span><div class="feature-icon">≈</div><h3>Like-for-like pools</h3><p>Gender, event, surface, timing method, and legal wind are kept separate.</p></article>
        <article><span>03</span><div class="feature-icon">⌁</div><h3>Honest uncertainty</h3><p>Weak data produces a low-confidence result—not false precision.</p></article>
      </div>
    </section>

    <section class="method-section" id="method" aria-labelledby="method-title">
      <div>
        <div class="eyebrow"><span></span> Transparency</div>
        <h2 id="method-title">Know what the number means.</h2>
      </div>
      <div class="method-copy">
        <p>FairLap reports an estimated percentile among comparable recorded performances. It is not an official national or European ranking.</p>
        <p id="coverage-summary"></p>
        <p>Chronological age does not capture biological maturity, training, injuries, tactics, weather, terrain, or course accuracy. A percentile is useful context—not a verdict on potential.</p>
        <details>
          <summary>Current model release</summary>
          <p id="model-release"></p>
        </details>
      </div>
    </section>
  </main>

  <footer>
    <a class="brand footer-brand" href="#top"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span><span>FairLap</span></a>
    <p>Built for young runners, parents, and the people who support them.</p>
    <span>No accounts · No tracking · No result storage</span>
  </footer>
`

const form = document.querySelector<HTMLFormElement>('#run-form')!
const sexInput = document.querySelector<HTMLSelectElement>('#sex')!
const birthInput = document.querySelector<HTMLInputElement>('#birth-date')!
const raceInput = document.querySelector<HTMLInputElement>('#race-date')!
const eventInput = document.querySelector<HTMLSelectElement>('#event')!
const surfaceInput = document.querySelector<HTMLSelectElement>('#surface')!
const minutesInput = document.querySelector<HTMLInputElement>('#minutes')!
const secondsInput = document.querySelector<HTMLInputElement>('#seconds')!
const timingInput = document.querySelector<HTMLSelectElement>('#timing')!
const windInput = document.querySelector<HTMLInputElement>('#wind')!
const windField = document.querySelector<HTMLElement>('#wind-field')!
const agePreview = document.querySelector<HTMLElement>('#age-preview')!
const errorBox = document.querySelector<HTMLElement>('#form-error')!
const resultPanel = document.querySelector<HTMLElement>('#result')!
const release = document.querySelector<HTMLElement>('#model-release')!

const today = new Date()
const maxRaceDate = `${today.getUTCFullYear()}-${String(today.getUTCMonth() + 1).padStart(2, '0')}-${String(today.getUTCDate()).padStart(2, '0')}`
raceInput.max = maxRaceDate

// Accept the full 10-19 window. Ages below the corpus's support fall back to the
// published benchmark with a visible caveat, which is far more useful to a
// 10-year-old's parent than a date field that silently refuses their input.
const publishedLow = overallAgeRange()[0]
const observedLow = observedAgeRange()[0]
const observedHigh = observedAgeRange()[1]
const isoDaysAgo = (days: number): string => new Date(today.getTime() - days * 86_400_000).toISOString().slice(0, 10)
birthInput.min = isoDaysAgo(BASELINE_MAX_AGE_DAYS)
birthInput.max = isoDaysAgo(BASELINE_MIN_AGE_DAYS)

const currentEvent = (): (typeof EVENTS)[number] => EVENTS.find((event) => event.id === eventInput.value)!

function refreshOptions(preferredEvent?: string): void {
  const sex = sexInput.value as Sex
  const events = availableEvents(sex)
  if (!events.some((event) => event.id === eventInput.value)) {
    eventInput.value = (preferredEvent && events.some((event) => event.id === preferredEvent)
      ? preferredEvent
      : events[0]?.id) ?? ''
  }
  updateSurfaces()
}

function updateSurfaces(): void {
  const event = currentEvent()
  if (!event) {
    surfaceInput.innerHTML = ''
    windField.hidden = true
    return
  }
  const surfaces = surfacesFor(event.id, sexInput.value as Sex, event.surfaces)
  if (!surfaces.some((surface) => surface === surfaceInput.value)) surfaceInput.value = surfaces[0] ?? ''
  surfaceInput.innerHTML = surfaces.map((surface) => `<option value="${surface}">${surfaceLabels[surface]}</option>`).join('')
  windField.hidden = !event.windAdjusted
  if (!event.windAdjusted) windInput.value = ''
}

function updateAge(): void {
  try {
    if (!birthInput.value || !raceInput.value) throw new Error()
    agePreview.textContent = formatAge(ageInDays(birthInput.value, raceInput.value))
  } catch {
    agePreview.textContent = 'Enter both dates'
  }
}

function renderResult(result: EvaluationResult, eventName: string): void {
  const percentile = Math.round(result.percentile)
  const marker = Math.max(1, Math.min(99, percentile))
  const timingText = result.timingAdjustment > 0 ? `Hand timing: +${result.timingAdjustment.toFixed(2)}s` : 'Electronic timing'
  const windText = Math.abs(result.windAdjustment) > 0 ? `Wind: ${result.windAdjustment > 0 ? '+' : ''}${result.windAdjustment.toFixed(2)}s` : 'No wind adjustment'
  const notes: string[] = []
  if (result.modelKind === 'observed') {
    notes.push(
      result.athletes > 0
        ? `${result.observations.toLocaleString()} performances by ${result.athletes.toLocaleString()} athletes aged to the day`
        : 'Exact-date-of-birth corpus',
    )
    notes.push(
      result.sampleSize > 0
        ? `${result.sampleSize.toLocaleString()} of them within ±6 months of this age`
        : 'No observations within ±6 months of this age — value interpolated from the age trend',
    )
    if (result.observedAgeDays) {
      notes.push(`Directly observed at ages ${years(result.observedAgeDays[0])}–${years(result.observedAgeDays[1])}`)
    }
  } else {
    notes.push('Published age-group benchmark — no observed-data cohort for this combination')
  }
  const floor = corpus?.supportFloorN ?? 20
  const caveats: string[] = []
  if (result.modelKind === 'baseline') {
    caveats.push('No observed cohort covers this combination, so it uses a published benchmark curve rather than recorded performances.')
  } else {
    const span = result.observedAgeDays
    if (span && (result.ageDays < span[0] || result.ageDays > span[1])) {
      caveats.push(`The corpus has no results at this exact age. The figure is interpolated from the fitted age trend for ${eventName}, so treat it as an estimate of the estimate.`)
    } else if (result.sampleSize < floor) {
      caveats.push(`Only ${result.sampleSize} comparable result${result.sampleSize === 1 ? '' : 's'} exist near this age, so the percentile is indicative rather than precise.`)
    }
  }
  resultPanel.innerHTML = `
    <div class="result-header">
      <div class="result-kicker">Your estimated result</div>
      <button class="copy-button" type="button" aria-label="Copy result to clipboard">Copy</button>
    </div>
    <div class="result-rank"><strong>${percentile}<sup>th</sup></strong><span>percentile</span></div>
    <p class="result-summary">Faster than approximately <strong>${percentile}%</strong> of comparable recorded performances at this age.</p>
    <div class="result-gauge" style="--position: ${marker}%">
      <div class="gauge-track"><i></i><b style="left: ${marker}%"></b></div>
      <div><span>Needs building</span><span>Elite</span></div>
    </div>
    <div class="result-band"><span>${resultLabel(percentile)}</span><span class="confidence-${result.confidence}">${confidenceLabel(result.confidence)}</span></div>
    <div class="result-times">
      <div><span>Typical (50th)</span><strong>${formatTime(result.median)}s</strong></div>
      <div><span>Fast 10% (90th)</span><strong>${formatTime(result.targets[90])}s</strong></div>
      <div><span>Average pace</span><strong>${formatPace(result.paceSecondsPerKm)}</strong></div>
    </div>
    <div class="result-context">
      <strong>${eventName} · ${surfaceLabels[surfaceInput.value as Surface]}</strong>
      <span>Estimated range: ${Math.round(result.percentileLow)}th–${Math.round(result.percentileHigh)}th percentile</span>
      ${notes.map((note) => `<span>${note}</span>`).join('')}
      <span>${timingText} · ${windText}</span>
    </div>
    ${caveats.map((caveat) => `<p class="result-warning"><b>!</b> ${caveat}</p>`).join('')}
    <p class="result-warning"><b>!</b> This is an estimate, not an official ranking. Biological maturity, tactics, terrain and course accuracy are not accounted for.</p>
  `
  resultPanel.scrollIntoView({ behavior: 'smooth', block: 'center' })

  const copyButton = resultPanel.querySelector<HTMLButtonElement>('.copy-button')!
  copyButton.addEventListener('click', async () => {
    const res = lastResult
    const evName = lastEventName
    const fs = lastFinishSeconds
    if (!res || !evName) return
    const text = `FairLap result: ${Math.round(res.percentile)}th percentile in ${evName} (${surfaceLabels[surfaceInput.value as Surface]})
Time: ${formatTime(fs)}s
Typical (50th): ${formatTime(res.median)}s
Fast 10% (90th): ${formatTime(res.targets[90])}s
Pace: ${formatPace(res.paceSecondsPerKm)}
Range: ${Math.round(res.percentileLow)}th–${Math.round(res.percentileHigh)}th percentile
Confidence: ${confidenceLabel(res.confidence)}
${notes.join('. ')}
${caveats.join('. ')}`
    try {
      await copyToClipboard(text)
      copyButton.textContent = 'Copied!'
      copyButton.classList.add('copied')
      setTimeout(() => {
        copyButton.textContent = 'Copy'
        copyButton.classList.remove('copied')
      }, 2000)
    } catch {
      showToast('Failed to copy')
    }
  })
}

sexInput.addEventListener('change', () => refreshOptions())
eventInput.addEventListener('change', updateSurfaces)
birthInput.addEventListener('input', updateAge)
raceInput.addEventListener('input', updateAge)
updateSurfaces()

const corpus = generatedData.corpus
release.textContent = corpus
  ? `Observed-data model ${generatedData.version} · ${corpus.observations.toLocaleString()} exact-date-of-birth performances by ${corpus.athletes.toLocaleString()} athletes across ${corpus.federations} federations (${corpus.startDate.slice(0, 4)}–${corpus.endDate.slice(0, 4)}). ${generatedData.sourceLabel}. Elite international results are held out of the fit and used only to check it.`
  : `Conservative baseline model ${generatedData.version} · ${generatedData.sourceLabel}.`

const youngEntries = Object.entries(corpus?.youngAgeHistogram ?? {})
const youngAgeSummary = youngEntries.length === 0
  ? 'none at all'
  : youngEntries.map(([age, count], index) => `${index > 0 ? ' and ' : ''}${Number(count).toLocaleString()} aged ${age}`).join('')

// State the covered range instead of implying the whole 10-19 band is data-backed.
const coverageText = `Based on ${corpus?.observations.toLocaleString() ?? 0} performances with an exact date of birth, across ${corpus?.federations ?? 0} federations. Results are directly observed at ages ${years(observedLow)}–${years(observedHigh)}, and estimated from the fitted age trend down to ${years(publishedLow)}. Below that, and for five events with no cohort at all, the app falls back to a published benchmark curve and says so. No public source publishes exact dates of birth for younger children in bulk — across all ${corpus?.federations ?? 0} federations and ten seasons the corpus contains ${youngAgeSummary}.`
const coverageNote = document.querySelector<HTMLElement>('#coverage-note')
const coverageSummary = document.querySelector<HTMLElement>('#coverage-summary')
if (coverageNote) coverageNote.textContent = coverageText
if (coverageSummary) coverageSummary.textContent = coverageText

let lastFinishSeconds = 0
let lastEventName = ''
let lastResult: EvaluationResult | null = null
let lastSubmittedState: string | null = null

function getFormState(): string {
  return JSON.stringify({
    sex: sexInput.value,
    birthDate: birthInput.value,
    raceDate: raceInput.value,
    event: eventInput.value,
    surface: surfaceInput.value,
    minutes: minutesInput.value,
    seconds: secondsInput.value,
    timing: timingInput.value,
    wind: windInput.value,
  })
}

function updateDirtyIndicator(): void {
  const currentState = getFormState()
  const isDirty = lastSubmittedState !== null && currentState !== lastSubmittedState
  form.classList.toggle('dirty', isDirty)
  submitButton.textContent = isDirty ? 'Recalculate' : 'Calculate my result'
}

const submitButton = form.querySelector<HTMLButtonElement>('.submit-button')!
;[sexInput, birthInput, raceInput, eventInput, surfaceInput, minutesInput, secondsInput, timingInput, windInput].forEach((input) => {
  input.addEventListener('input', updateDirtyIndicator)
  input.addEventListener('change', updateDirtyIndicator)
})

form.addEventListener('submit', (event) => {
  event.preventDefault()
  errorBox.hidden = true
  submitButton.disabled = true
  submitButton.textContent = 'Calculating…'
  try {
    const selectedEvent = currentEvent()
    if (!selectedEvent) throw new Error('No event is available for the selected gender.')
    const finishSeconds = Number(minutesInput.value) * 60 + Number(secondsInput.value)
    lastFinishSeconds = finishSeconds
    const wind = selectedEvent.windAdjusted && windInput.value !== '' ? Number(windInput.value) : null
    const input: EvaluationInput = {
      birthDate: birthInput.value,
      raceDate: raceInput.value,
      sex: sexInput.value as Sex,
      eventId: selectedEvent.id,
      surface: surfaceInput.value as Surface,
      seconds: finishSeconds,
      timingMethod: timingInput.value as TimingMethod,
      wind,
    }
    if (!Number.isFinite(finishSeconds) || finishSeconds <= 0) throw new Error('Enter a finish time greater than zero.')
    const cohort = findCohort(selectedEvent.id, input.sex, input.surface)
    let result: EvaluationResult
    if (cohort) {
      result = evaluateGrid(cohort, input, selectedEvent.distance, selectedEvent.windAdjusted)
    } else {
      const fallback = baselineEvent(selectedEvent.id)
      const model = fallback?.surfaces[input.surface][input.sex]
      if (!model) throw new Error('No model is available for this combination.')
      result = evaluateBaseline(model, input, selectedEvent.distance, selectedEvent.windAdjusted, 'low')
    }
    lastEventName = selectedEvent.name
    lastResult = result
    lastSubmittedState = getFormState()
    renderResult(result, selectedEvent.name)
    updateDirtyIndicator()

    const formState: Partial<Record<UrlParam, string>> = {
      sex: sexInput.value,
      birthDate: birthInput.value,
      raceDate: raceInput.value,
      event: eventInput.value,
      surface: surfaceInput.value,
      minutes: minutesInput.value,
      seconds: secondsInput.value,
      timing: timingInput.value,
      wind: windInput.value,
    }
    saveFormState(formState)
  } catch (error) {
    errorBox.textContent = error instanceof Error ? error.message : 'Check the entered details and try again.'
    errorBox.hidden = false
  } finally {
    submitButton.disabled = false
    submitButton.innerHTML = 'Calculate my result <span aria-hidden="true">↗</span>'
  }
})

const savedState = loadFormState()
if (Object.keys(savedState).length > 0) {
  if (savedState.sex) sexInput.value = savedState.sex
  if (savedState.birthDate) birthInput.value = savedState.birthDate
  if (savedState.raceDate) raceInput.value = savedState.raceDate
  if (savedState.event) eventInput.value = savedState.event
  if (savedState.surface) surfaceInput.value = savedState.surface
  if (savedState.minutes) minutesInput.value = savedState.minutes
  if (savedState.seconds) secondsInput.value = savedState.seconds
  if (savedState.timing) timingInput.value = savedState.timing
  if (savedState.wind) windInput.value = savedState.wind
  refreshOptions(savedState.event)
  updateAge()
}

