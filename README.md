# FairLap

A static, privacy-friendly web calculator that compares youth running performances using age to the day. It compares a result against other recorded performances of nearly the same age to the day and reports an estimated percentile.

## Run locally

```bash
npm install
npm run dev
```

## Verify and build

```bash
npm run check
npm run build
npm run preview
```

The production build is written to `dist/` and works on GitHub Pages without a server, database, account, analytics, or runtime API.

`npm run check` runs everything CI does: typecheck, byte-compile both Python scripts, the crawler parser self-test, the estimator tests, the unit tests, and the build.

## Deploy to GitHub Pages

Push to `main` and the workflow in `.github/workflows/deploy.yml` verifies and publishes. Before the first push:

```bash
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

`vite.config.ts` sets `base: './'`, so the bundle references its assets relatively and works from any Pages subpath (`https://<you>.github.io/<repo>/`) or a domain root without further configuration. The app uses in-page anchors rather than client-side routing, so relative URLs are sufficient.

## Data corpus

The model is fitted from a corpus of **exact-date-of-birth** performance results. An age group or a birth year is not enough to place an athlete on an age-to-the-day curve, so sources without exact dates of birth are never approximated into the fit.

Current corpus (`src/model/generated.json`, generated 2026-10-02):

| | |
| --- | --- |
| Competitions crawled | 7,759 |
| Flat-distance result rows with an exact date of birth | 151,658 |
| Exact-DOB observations in the modelled 10–19 range | 74,238 |
| Distinct athletes | 68,710 |
| Federations | 152 |
| Race dates | 2016-04-22 → 2026-09-26 |
| Fitted cohorts (event × sex × surface) | 26 (2 high, 11 moderate, 13 low confidence) |
| Held-out elite observations used only for validation | 31,526 |
| Marks rejected as implausible | 108 |
| Ages answerable (including interpolation) | 10.2 – 20.0 |

Events with data: 60 m, 100 m, 200 m, 400 m, 800 m, 1500 m, 3000 m, 10,000 m — on outdoor track, most also indoors. 300 m, 600 m, 1000 m, the mile and 5 km have too few exact-DOB results to fit and are therefore not offered.

Two limits are worth stating plainly rather than hiding:

- **Road and cross-country have no data at all.** The World Athletics calendar results feed returns zero events for every cross-country and marathon competition in the calendar. Those surfaces are never offered for a data-derived answer.
- **Exact dates of birth are almost nonexistent below about 13.** This was investigated rather than assumed. World Athletics is the only public source that publishes exact dates of birth at scale, and it has been crawled exhaustively: across all 152 federations and ten seasons the entire corpus contains **1** result aged 10, **7** aged 11 and **45** aged 12. The Nordic federations' usual host (OpenTrack) publishes an age *class*, not a date. Austria's and Germany's athletics database (ATHMIN) has U10 and U12 classes but records only *Jahrgang*, the year of birth. Children's athletics in these countries happens in school and age-group competitions, which is exactly the category that never records a date of birth.

Sparse ages are therefore **interpolated from the fitted age trend rather than refused**, and the limit is measured rather than chosen. Holding out ages and refitting on older data only, the mean error of the extrapolated curve three and four years below the training range is about **3% of finish time** — no larger than the age-to-age spread at those ages, which is 3–5%. Extrapolating a straight line from the *oldest* anchors instead costs about 5%, so the slope is always taken from the young end of the curve.

The result is that the app answers **10.2 to 20.0 years** from the corpus instead of 13.1 to 20. Every age reached this way is flagged in the result panel as interpolated rather than observed, and the percentile band widens accordingly: about **7 percentile points** wide where an age has 200+ nearby observations, and the full **60 points** where there is essentially nothing. The five events with no cohort at all (300 m, 600 m, 1000 m, mile, 5 km) are offered but marked "benchmark only".

The interface states the observed and supported age ranges for each cohort rather than claiming 10–19 universally, and marks benchmark-only answers so a published age-group figure is never presented as if it came from real performances.

### Method

1. Keep observations whose age at the race date falls in the modelled range; deduplicate per athlete and event.
2. Split the elite **selection** tier (World Athletics / Olympic / FISU) out of the fit. Those results are a small, heavily filtered slice of the sport, so they are held out and used to check the model instead of train it.
3. Cap each `country × event × sex × season` cell so one large federation cannot dominate a cohort.
4. Pool observations into half-year age bins and fit a **monotone (isotonic) decreasing** curve to log-seconds against age in days. Youth running performance improves with age throughout the modelled range, so monotonicity is a defensible physical constraint; it removes the non-monotonic steps a raw sliding-window quantile fit produces. Bins are anchored at the mean age they actually contain, so a partial first or last bin does not shift the curve.
5. **Trim implausible marks.** Some feeds publish mislabelled rows: points scores from a multi-event presented as a time, or a mark parsed from the wrong event. Marks beyond 4.5 robust sigmas (median-absolute-deviation scale) of the fitted curve are dropped. The threshold is derived from the data, so it needs no hand-picked physiological bounds per event and cannot be inflated by the outliers themselves. Combined-events competitions are excluded outright, because their 800 m and 1500 m legs are not comparable with standalone races.
6. Estimate sigma from **pooled residuals** around that curve, shrunk toward the cohort-wide spread when a local bin is thin, with a floor and ceiling.
7. Publish the curve across the observed span *and* up to three years beyond it at each end, where the extrapolated arm is anchored to the nearest well-supported age and continued along the local slope. The arms are clamped so they can never imply that a younger athlete is faster than the data supports, and are anchored to the curve's own end value so the join is continuous. Three years is the distance over which extrapolation was measured to cost no more than the intrinsic spread.
8. Carry the local observation count on every grid point, and derive both the confidence label and the percentile band from it, so a thinly observed age looks uncertain instead of precise.

### Regenerating

```bash
export WORLD_ATHLETICS_GRAPHQL_KEY=...   # never stored in the repo
npm run model:fetch                       # enumerate + crawl into data/raw/wa.sqlite
npm run model:report                      # corpus statistics and coverage gaps
npm run model:build                       # fit the model and write the coverage report
```

`model:fetch` is resumable: progress lives in `data/raw/wa.sqlite`, and re-running continues where it stopped. Use `--limit N` and `--db <path>` for a smoke test, `--reset-failed` to retry competitions that errored.

`data/processed/coverage-report.md` is regenerated by `model:build` and records per-cohort observation counts, how many marks were dropped, observed and published age ranges, sigma, confidence, and the held-out validation results. Read it before trusting a number.

Two limits on the fit are deliberate rather than accidental. Hurdles, steeplechase, race walks and field events are excluded so an `event_id` never pools two different physiological tasks. Combined-events competitions are excluded outright because their 800 m and 1500 m legs are not comparable with standalone races.

`npm run lint` runs a parser self-test (`fetch_world_athletics.py --selftest`) and `npm run test:model` runs the estimator tests. Both exist because silent failure is the real risk in a data pipeline: a case-sensitivity bug once made the parser reject every event while still reporting success.

### Source CSV schema

The estimator also accepts CSV files instead of the SQLite spool:

```text
required: event_id,sex,birth_date,race_date,seconds,surface,timing_method
optional: athlete_id,country,level,season_year
```

Dates must be ISO `YYYY-MM-DD`. Allowed event and surface combinations are defined in `scripts/generate_model.py`. Only add sources whose terms permit local research and redistribution of derived aggregate models.

## Data policy

- The raw corpus lives in `data/raw/`, which is git-ignored, and is never published.
- Do not add scraped or licensed data without a documented usage basis.
- Do not publish raw athlete records. Only aggregate curves ship.
- Keep `data/sources/manifest.json` current, including known coverage gaps.
- Keep top-list and championship data separate from broad participation results.
- Weight or cap sources so large federations do not dominate.
- Treat results without exact DOB as validation or contextual data, not exact-age observations.

## Project structure

- `src/main.ts` — accessible form, interaction, and result presentation
- `src/model/` — event definitions, age arithmetic, corrections, cohort lookup, percentile calculation
- `src/model/cohorts.ts` — cohort index and availability gating that drives the interface
- `src/model/baseline.ts` — transparent low-confidence fallback for uncovered combinations
- `src/model/generated.json` — fitted model artifact (committed; aggregates only)
- `scripts/fetch_world_athletics.py` — calendar crawler and corpus inspector
- `scripts/generate_model.py` — exact-DOB estimator and coverage report writer
- `vite.config.ts` — relative asset base for GitHub Pages
- `data/sources/manifest.json` — source policy, provenance, and known gaps
- `.github/workflows/deploy.yml` — Pages deployment

## Interpretation

FairLap reports an estimated percentile among comparable recorded performances. It is not an official federation, national, or European ranking. Chronological age does not account for biological maturity, training, injury, tactics, terrain, or course accuracy.