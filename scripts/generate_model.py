#!/usr/bin/env python3
"""Fit daily age-performance curves from the crawled World Athletics corpus.

Input is the local SQLite spool produced by ``fetch_world_athletics.py`` (never
committed - see the README data policy). Output is the model artifact consumed
by the web app plus a human-readable coverage report.

Method
------
1. Keep only observations whose age at the race date falls in the modelled range.
2. Separate the elite *selection* tier (World Athletics / Olympic / FISU) from the
   broad participation tier used for the fit, per the README data policy.
3. Cap each ``country x event x sex x season`` cell so a single large federation
   cannot dominate a cohort.
4. Pool observations into fixed age bins, then fit a **monotone (isotonic)
   decreasing** curve to log-seconds versus age in days. Youth running
   performance improves with age almost everywhere in 10-19, so monotonicity is
   a defensible physical constraint and removes the non-monotonic steps that
   plague a raw sliding-window quantile fit.
5. Estimate sigma from **pooled residuals** around that curve, shrunk toward the
   cohort-wide residual spread when a local bin is thin. A two-point p10/p90 fit
   is unusable at small n; this is not.
6. Publish the curve on a fixed age grid with an honest local observation count.

Usage:
    python3 scripts/generate_model.py
    python3 scripts/generate_model.py --include-international --grid-days 7
"""

import argparse
import bisect
import csv
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "raw" / "wa.sqlite"
OUTPUT = ROOT / "src" / "model" / "generated.json"
REPORT = ROOT / "data" / "processed" / "coverage-report.md"

REQUIRED = {"event_id", "sex", "birth_date", "race_date", "seconds", "surface", "timing_method"}

EVENT_SURFACES = {
    "60m": {"indoor-track", "outdoor-track"},
    "100m": {"outdoor-track"},
    "200m": {"outdoor-track", "indoor-track"},
    "300m": {"outdoor-track", "indoor-track"},
    "400m": {"outdoor-track", "indoor-track"},
    "600m": {"outdoor-track", "indoor-track"},
    "800m": {"outdoor-track", "indoor-track"},
    "1000m": {"outdoor-track", "indoor-track"},
    "1500m": {"outdoor-track", "indoor-track"},
    "mile": {"road", "outdoor-track", "indoor-track"},
    "3000m": {"outdoor-track", "cross-country", "indoor-track"},
    "5k": {"road", "cross-country", "outdoor-track"},
    "10k": {"road", "cross-country", "outdoor-track"},
}

MIN_AGE_YEARS = 10.0
MAX_AGE_YEARS = 20.0
MIN_AGE_DAYS = int(MIN_AGE_YEARS * 365.2425)
MAX_AGE_DAYS = int(MAX_AGE_YEARS * 365.2425)
# The crawler retains ages 8-22, so this is the youngest age that can reach the fit.
# Ages below this are reported in the coverage report so the interface can say
# exactly how thin the youngest end is, rather than guessing.
YOUNG_AGE_CUTOFF_DAYS = int(13 * 365.2425)

BIN_DAYS = 182# ~ half-year age bins
LOCAL_WINDOW_DAYS = 180# honest local observation window for the reported n
EXTRAPOLATION_MARGIN_DAYS = 180  # how far past the observed age span the curve may be published
MIN_COHORT_OBSERVATIONS = 120
MIN_BIN_OBSERVATIONS = 8
MIN_PUBLISH_LOCAL_N = 20  # below this local count an age is flagged as thinly observed

# How far the curve may be carried beyond the youngest / oldest *well-supported*
# anchor. Measured, not guessed: predicting held-out ages three and four years
# below a truncated fit costs about 3% of finish time, which is no larger than the
# age-to-age spread at those ages. Extrapolating a linear slope from the oldest
# anchors instead costs ~5%, so the slope is always taken from the young end.
MAX_BACK_EXTRAPOLATION_DAYS = int(3 * 365.2425)
SLOPE_ANCHOR_POINTS = 4
# Index 1 is the *fast* boundary, i.e. the 10th percentile of times, so it carries
# the negative normal quantile. Index 3 is the slow boundary.
FAST_TAIL = -1.2815515655446004  # normal 10th percentile of times
SLOW_TAIL = -FAST_TAIL
SHRINKAGE_PRIOR = 30.0  # pseudo-observations pulling a thin bin toward the cohort sigma
SIGMA_FLOOR = 0.02
SIGMA_CEILING = 0.35
TRIM_SIGMAS = 4.5  # residual threshold, in robust sigmas, for dropping bad marks
SOURCE_CAP = 250  # max observations per country x event x sex x season

SELECTION_TIERS = {"international"}


# --------------------------------------------------------------------------- #
# Small numeric helpers (stdlib only)
# --------------------------------------------------------------------------- #

def normal_inverse(p):
    """Acklam's rational approximation of the standard normal quantile function."""
    if not 0.0 < p < 1.0:
        raise ValueError("probability out of range")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    low, high = 0.02425, 1 - 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def isotonic_decreasing(values, weights):
    """Weighted pool-adjacent-violators fit of a non-increasing sequence.

    Returns one fitted value per input element. Each block tracks how many
    *elements* it covers separately from its statistical weight, because the
    weight is an observation count and must never be used to expand the output.
    """
    blocks = []  # [sum(w*y), sum(w), number of elements]
    for value, weight in zip(values, weights):
        blocks.append([value * weight, weight, 1])
        while len(blocks) > 1 and (blocks[-2][0] / blocks[-2][1]) < (blocks[-1][0] / blocks[-1][1]) - 1e-12:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append([left[0] + right[0], left[1] + right[1], left[2] + right[2]])
    fitted = []
    for total, weight, elements in blocks:
        fitted.extend([total / weight] * elements)
    if len(fitted) != len(values):
        raise AssertionError("isotonic fit returned the wrong number of points")
    return fitted


def quantile(sorted_values, probability):
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def interpolate_anchors(anchors, age_days):
    """Linear interpolation over (age_days, value) anchors."""
    if age_days <= anchors[0][0]:
        return anchors[0][1]
    if age_days >= anchors[-1][0]:
        return anchors[-1][1]
    low, high = 0, len(anchors) - 1
    while high - low > 1:
        middle = (low + high) // 2
        if anchors[middle][0] <= age_days:
            low = middle
        else:
            high = middle
    left_age, left_value = anchors[low]
    right_age, right_value = anchors[high]
    fraction = (age_days - left_age) / (right_age - left_age)
    return left_value + (right_value - left_value) * fraction


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_rows(db_path):
    connection = sqlite3.connect(db_path)
    columns = ("competition_id", "event_id", "sex", "birth_date", "race_date", "seconds", "surface",
               "timing_method", "athlete_id", "country", "level", "season_year")
    rows = [dict(zip(columns, values)) for values in connection.execute(
        "SELECT %s FROM results" % ",".join(columns))]
    connection.close()
    return rows


def derive_country(row):
    """Derive a federation code from the source URL when no country column exists."""
    url = (row.get("source_url") or "").strip()
    if not url:
        return "ZZZ"
    match = re.search(r"\(([A-Z]{3})\)\s*$", url)
    return match.group(1) if match else "ZZZ"


def derive_level(row):
    """Map a competition group name to a selection tier, defaulting to the fit tier."""
    name = (row.get("competition_group") or "").strip().lower()
    if not name:
        return "national"
    if name.startswith(("world athletics", "olympic", "fisu")):
        return "international"
    if "permit" in name:
        return "permit"
    if name.startswith("area"):
        return "regional"
    if name.startswith("national"):
        return "national"
    return "meet"


def load_csv(paths):
    rows = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"{path}: missing columns {sorted(missing)}")
            for row in reader:
                try:
                    season = int(str(row.get("season_year") or row["race_date"])[:4])
                except ValueError:
                    season = int(row["race_date"][:4])
                rows.append({
                    "competition_id": 0,
                    "athlete_id": row.get("athlete_id", ""),
                    "event_id": row["event_id"],
                    "sex": row["sex"],
                    "birth_date": row["birth_date"],
                    "race_date": row["race_date"],
                    "seconds": float(row["seconds"]),
                    "surface": row["surface"],
                    "timing_method": row["timing_method"],
                    "country": (row.get("country") or "").strip() or derive_country(row),
                    "level": (row.get("level") or "").strip() or derive_level(row),
                    "season_year": season,
                })
    return rows


def normalise(rows):
    """Validate, restrict to the modelled age range and deduplicate."""
    kept = []
    rejected = {"range": 0, "invalid": 0, "duplicate": 0, "surface": 0}
    young = defaultdict(int)
    seen = set()
    for row in rows:
        event_id = row["event_id"]
        surface = row["surface"]
        sex = row["sex"]
        if event_id not in EVENT_SURFACES or surface not in EVENT_SURFACES[event_id] or sex not in {"male", "female"}:
            rejected["surface"] += 1
            continue
        try:
            born = date.fromisoformat(row["birth_date"])
            raced = date.fromisoformat(row["race_date"])
            seconds = float(row["seconds"])
        except (ValueError, TypeError):
            rejected["invalid"] += 1
            continue
        if seconds <= 0 or raced < born:
            rejected["invalid"] += 1
            continue
        age_days = (raced - born).days
        if age_days < MIN_AGE_DAYS or age_days >= MAX_AGE_DAYS:
            rejected["range"] += 1
            continue
        identity = (row.get("athlete_id") or "", event_id, sex, surface, born.isoformat(), raced.isoformat())
        if identity in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(identity)
        if age_days < YOUNG_AGE_CUTOFF_DAYS:
            young[int(age_days // 365.2425)] += 1
        kept.append({**row, "age_days": age_days, "log_seconds": math.log(seconds)})
    return kept, rejected, dict(sorted(young.items()))


def cap_sources(observations, cap):
    """Cap each country x event x sex x season cell to stop one federation dominating."""
    if cap <= 0:
        return observations, 0, 0
    cells = defaultdict(list)
    for observation in observations:
        key = (observation["country"], observation["event_id"], observation["sex"], observation["season_year"])
        cells[key].append(observation)
    kept = []
    dropped = 0
    largest = max((len(cell) for cell in cells.values()), default=0)
    for cell in cells.values():
        if len(cell) <= cap:
            kept.extend(cell)
            continue
        # Stride sample across the performance distribution so the cap does not
        # bias the tail; ordering by seconds makes this deterministic.
        cell.sort(key=lambda item: (item["log_seconds"], item["athlete_id"]))
        step = len(cell) / cap
        kept.extend(cell[int(index * step)] for index in range(cap))
        dropped += len(cell) - cap
    return kept, dropped, largest


# --------------------------------------------------------------------------- #
# Cohort fitting
# --------------------------------------------------------------------------- #

def weighted_slope(anchors, count=SLOPE_ANCHOR_POINTS):
    """Least-squares slope in log-seconds per day, taken from the given end of the curve.

    `anchors` is [(age_days, log_seconds, weight)] already ordered by age. The slope
    is measured on the end where the curve is still improving steeply, because that
    is the end being extrapolated towards.
    """
    points = [(age, value) for age, value, weight in anchors[:count] if weight >= MIN_BIN_OBSERVATIONS]
    if len(points) < 2:
        points = [(age, value) for age, value, _ in anchors[:count]]
    if len(points) < 2:
        return 0.0
    n = len(points)
    sum_x = sum(point[0] for point in points)
    sum_y = sum(point[1] for point in points)
    sum_xx = sum(point[0] * point[0] for point in points)
    sum_xy = sum(point[0] * point[1] for point in points)
    denominator = n * sum_xx - sum_x * sum_x
    if abs(denominator) < 1e-9:
        return 0.0
    return (n * sum_xy - sum_x * sum_y) / denominator


def robust_sigma(residuals):
    """Median-absolute-deviation scale. Immune to the outliers it is meant to catch."""
    if not residuals:
        return SIGMA_FLOOR
    median = quantile(sorted(residuals), 0.5)
    deviations = sorted(abs(value - median) for value in residuals)
    mad = quantile(deviations, 0.5)
    return max(SIGMA_FLOOR, min(SIGMA_CEILING, 1.4826 * mad))


def age_binning(observations):
    """Half-year bins, skipping empty ones. Returns [(mean_age, [(age, log_seconds), ...])]."""
    bins = defaultdict(list)
    for observation in observations:
        bins[observation["age_days"] // BIN_DAYS].append((observation["age_days"], observation["log_seconds"]))
    index = sorted(bins)
    if len(index) < 3:
        return []
    filled = []
    for position in range(index[0], index[-1] + 1):
        values = bins.get(position)
        if values:
            filled.append(values)
    return filled


def fit_binning(filled):
    """Anchor each bin at the mean age it actually contains, not the bin centre.

    The first and last bins are usually partial, so anchoring them at the
    geometric centre would place the anchor outside the data.
    """
    centres, means, weights = [], [], []
    for values in filled:
        centres.append(sum(age for age, _ in values) / len(values))
        means.append(sum(log_seconds for _, log_seconds in values) / len(values))
        weights.append(float(len(values)))
    return centres, means, weights


def trim_outliers(filled, centres):
    """Drop marks that cannot plausibly belong to the cohort.

    Some feeds publish mislabelled rows - a 78-second100m, or points scores
    from a multi-event published as a time. The threshold is derived from the
    data with a median-absolute-deviation scale, so it does not need hand-picked
    physiological bounds per event, and it cannot be inflated by the outliers
    themselves.
    """
    means = [sum(log for _, log in values) / len(values) for values in filled]
    weights = [float(len(values)) for values in filled]
    fitted = isotonic_decreasing(means, weights)
    anchors = list(zip(centres, fitted))
    residuals = [
        log_seconds - interpolate_anchors(anchors, age)
        for values in filled
        for age, log_seconds in values
    ]
    threshold = TRIM_SIGMAS * robust_sigma(residuals)
    kept = []
    dropped = 0
    for values in filled:
        survivors = [
            (age, log_seconds)
            for age, log_seconds in values
            if abs(log_seconds - interpolate_anchors(anchors, age)) <= threshold
        ]
        dropped += len(values) - len(survivors)
        if survivors:
            kept.append(survivors)
    return kept, dropped


def fit_cohort(event_id, sex, surface, observations, grid_days):
    filled = age_binning(observations)
    if len(filled) < 3:
        return None
    centres, _, _ = fit_binning(filled)
    filled, trimmed = trim_outliers(filled, centres)
    if len(filled) < 3:
        return None

    bin_centres, bin_means, bin_weights = fit_binning(filled)
    fitted = isotonic_decreasing(bin_means, bin_weights)

    ages = [age for values in filled for age, _ in values]
    logs = [log_seconds for values in filled for _, log_seconds in values]

    residual_anchors = list(zip(bin_centres, fitted))
    residuals = [value - interpolate_anchors(residual_anchors, age) for value, age in zip(logs, ages)]
    global_sigma = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    global_sigma = min(SIGMA_CEILING, max(SIGMA_FLOOR, global_sigma))

    bin_sigma = []
    for values, prediction in zip(filled, fitted):
        if len(values) < MIN_BIN_OBSERVATIONS:
            bin_sigma.append(global_sigma)
            continue
        spread = sum((log_seconds - prediction) ** 2 for _, log_seconds in values) / len(values)
        shrunk = math.sqrt(
            (len(values) * spread + SHRINKAGE_PRIOR * global_sigma ** 2) / (len(values) + SHRINKAGE_PRIOR)
        )
        bin_sigma.append(min(SIGMA_CEILING, max(SIGMA_FLOOR, shrunk)))

    median_anchors = [(centre, math.exp(value)) for centre, value in zip(bin_centres, fitted)]
    sigma_anchors = list(zip(bin_centres, bin_sigma))

    ordered = sorted(zip(ages, logs))
    ordered_ages = [item[0] for item in ordered]

    observed_low, observed_high = min(ages), max(ages)
    weighted = list(zip(bin_centres, fitted, bin_weights))
    young_anchors = [anchor for anchor in weighted if anchor[2] >= MIN_BIN_OBSERVATIONS]
    old_anchors = [anchor for anchor in reversed(weighted) if anchor[2] >= MIN_BIN_OBSERVATIONS]
    # Anchor the extrapolated arms to the curve's own end values so the join is
    # continuous, and never let a slope imply that a younger athlete is faster or
    # an older one slower than the data supports.
    young_junction = bin_centres[0]
    old_junction = bin_centres[-1]
    young_slope = min(0.0, weighted_slope(weighted))
    old_slope = min(0.0, weighted_slope(list(reversed(weighted))))
    young_reference = young_anchors[0] if young_anchors else weighted[0]
    old_reference = old_anchors[0] if old_anchors else weighted[-1]

    # Carry the curve beyond the observed span, anchored on the nearest
    # well-supported age and continued along the local slope. This is what lets a
    # 13-year-old curve say something useful about 11, instead of refusing.
    back_floor = max(MIN_AGE_DAYS, int(young_reference[0]) - MAX_BACK_EXTRAPOLATION_DAYS)
    forward_ceiling = min(MAX_AGE_DAYS - 1, int(old_reference[0]) + MAX_BACK_EXTRAPOLATION_DAYS)
    support_low = max(MIN_AGE_DAYS, min(observed_low, back_floor))
    support_high = min(MAX_AGE_DAYS - 1, max(observed_high, forward_ceiling))
    support_low = min(support_low, support_high - grid_days)

    def median_log(age_days):
        """Curve value in log-seconds: the isotonic fit inside it, the local slope outside."""
        if age_days < young_junction:
            return fitted[0] + young_slope * (age_days - young_junction)
        if age_days > old_junction:
            return fitted[-1] + old_slope * (age_days - old_junction)
        return interpolate_anchors(residual_anchors, age_days)

    def grid_point(age_days):
        median = math.exp(median_log(age_days))
        sigma = interpolate_anchors(sigma_anchors, age_days)
        left = bisect.bisect_left(ordered_ages, age_days - LOCAL_WINDOW_DAYS)
        right = bisect.bisect_right(ordered_ages, age_days + LOCAL_WINDOW_DAYS)
        return [
            age_days,
            round(median * math.exp(sigma * FAST_TAIL), 3),
            round(median, 3),
            round(median * math.exp(sigma * SLOW_TAIL), 3),
            round(sigma, 5),
            right - left,
        ]

    grid = [grid_point(age) for age in range(support_low, support_high + 1, grid_days)]
    if not grid:
        return None
    if grid[-1][0] < support_high:
        grid.append(grid_point(support_high))
    if not grid:
        return None

    # Confidence must reflect the ages that actually carry observations.
    in_span = [point[5] for point in grid if observed_low <= point[0] <= observed_high]
    median_local_n = sorted(in_span)[len(in_span) // 2] if in_span else 0
    # Confidence is based on what actually backs the curve, after trimming.
    total = len(ages)
    athletes = len({item["athlete_id"] for item in observations if item.get("athlete_id")})
    return {
        "eventId": event_id,
        "sex": sex,
        "surface": surface,
        "observations": total,
        "rawObservations": len(observations),
        "trimmedObservations": trimmed,
        "athletes": athletes,
        "sigma": round(global_sigma, 5),
        "observedAgeDays": [observed_low, observed_high],
        "supportedAgeDays": [support_low, support_high],
        "medianLocalN": median_local_n,
        "confidence": confidence_for(total, median_local_n),
        "grid": grid,
    }


def confidence_for(total, median_local_n):
    if total >= 5000 and median_local_n >= 150:
        return "high"
    if total >= 1000 and median_local_n >= 40:
        return "moderate"
    return "low"


def percentile_from(median, sigma, seconds):
    return normal_cdf((math.log(seconds) - math.log(median)) / sigma)


def normal_cdf(value):
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def validate(cohorts_by_key, selection_observations):
    """Score the fit against the held-out elite selection tier.

    The selection tier is deliberately excluded from the fit; if the model can
    reproduce elite results it is at least not obviously mis-scaled.
    """
    report = []
    for (event_id, sex, surface), observations in sorted(selection_observations.items()):
        cohort = cohorts_by_key.get((event_id, sex, surface))
        if cohort is None or len(observations) < 50:
            continue
        anchors = [(point[0], point[2]) for point in cohort["grid"]]
        sigmas = [(point[0], point[4]) for point in cohort["grid"]]
        errors = []
        for item in observations:
            median = interpolate_anchors(anchors, item["age_days"])
            sigma = interpolate_anchors(sigmas, item["age_days"])
            errors.append(percentile_from(median, sigma, math.exp(item["log_seconds"])))
        report.append({
            "eventId": event_id, "sex": sex, "surface": surface, "n": len(observations),
            "meanPercentile": round(100 * sum(errors) / len(errors), 1),
            "maePercentile": round(100 * sum(abs(value - 0.5) for value in errors) / len(errors), 1),
        })
    return report


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def build(db_path, csv_paths, include_international, source_cap, grid_days):
    rows = load_csv(csv_paths) if csv_paths else load_rows(db_path)
    print(f"Loaded {len(rows):,} raw result rows")
    observations, rejected, young = normalise(rows)
    print(f"  rejected: {rejected}")
    print(f"  usable observations in {MIN_AGE_YEARS:.0f}-{MAX_AGE_YEARS:.0f}y range: {len(observations):,}")

    fit_pool = [item for item in observations if include_international or item["level"] not in SELECTION_TIERS]
    selection_pool = [item for item in observations if item["level"] in SELECTION_TIERS]
    print(f"  fit tier: {len(fit_pool):,}  |  held-out selection tier: {len(selection_pool):,}")

    fit_pool, capped, largest_cell = cap_sources(fit_pool, source_cap)
    print(f"  source cap {source_cap}: largest federation cell {largest_cell:,} rows, removed {capped:,} rows")

    cohorts = defaultdict(list)
    for observation in fit_pool:
        cohorts[(observation["event_id"], observation["sex"], observation["surface"])].append(observation)
    selection = defaultdict(list)
    for observation in selection_pool:
        selection[(observation["event_id"], observation["sex"], observation["surface"])].append(observation)

    events = []
    for key, group in sorted(cohorts.items()):
        if len(group) < MIN_COHORT_OBSERVATIONS:
            print(f"  skipping {key}: only {len(group)} observations")
            continue
        cohort = fit_cohort(*key, group, grid_days)
        if cohort is None:
            print(f"  skipping {key}: too few populated age bins")
            continue
        events.append(cohort)
    if not events:
        raise SystemExit("No cohort reached the minimum observation count. See data/processed/coverage-report.md.")

    races = [item["race_date"] for item in observations]
    levels = defaultdict(int)
    for item in fit_pool:
        levels[item["level"]] += 1
    countries = sorted({item["country"] for item in observations})

    confidence_rank = {"low": 0, "moderate": 1, "high": 2}
    overall = min((event["confidence"] for event in events), key=lambda value: confidence_rank[value])

    output = {
        "version": date.today().isoformat(),
        "generatedAt": datetime.now().replace(microsecond=0).isoformat(),
        "kind": "observed",
        "confidence": overall,
        "sourceLabel": (
            f"World Athletics public competition results, {min(races)[:4]}-{max(races)[:4]}; "
            f"{len(observations):,} exact-date-of-birth performances by {len(countries)} federations"
        ),
        "corpus": {
            "observations": len(observations),
            "athletes": len({item["athlete_id"] for item in observations if item.get("athlete_id")}),
            "startDate": min(races),
            "endDate": max(races),
            "sources": ["World Athletics public results"],
            "federations": len(countries),
            "cohorts": len(events),
            "levels": dict(sorted(levels.items())),
            "heldOutSelectionObservations": len(selection_pool),
            "youngAgeHistogram": young,
            "gridDays": grid_days,
            "supportFloorN": MIN_PUBLISH_LOCAL_N,
            "maxBackExtrapolationYears": round(MAX_BACK_EXTRAPOLATION_DAYS / 365.2425, 2),
            "method": "monotone isotonic fit on log seconds vs age in days; sigma from shrunk pooled residuals",
        },
        "events": events,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, separators=(",", ":")) + "\n", encoding="utf-8")

    validation = validate({(event["eventId"], event["sex"], event["surface"]): event for event in events}, selection)
    write_report(output, validation, rejected, capped, largest_cell)
    print(f"\nWrote {OUTPUT} ({OUTPUT.stat().st_size/1024:.0f} KiB) with {len(events)} cohorts, "
          f"overall confidence '{overall}'")
    print(f"Wrote {REPORT}")


def write_report(output, validation, rejected, capped, largest_cell):
    """Render the coverage report that data/processed/coverage-report.md is made of."""
    lines = [
        "# Corpus coverage report",
        "",
        f"Generated {output['generatedAt']} by `scripts/generate_model.py`.",
        "",
        "## Corpus",
        "",
    ]
    corpus = output["corpus"]
    lines += [
        f"- Observations in modelled age range: **{corpus['observations']:,}**",
        f"- Distinct athletes: **{corpus['athletes']:,}**",
        f"- Race date range: {corpus['startDate']} .. {corpus['endDate']}",
        f"- Federations: **{corpus['federations']}**",
        f"- Cohorts fitted: **{corpus['cohorts']}**",
        f"- Held-out elite selection observations: {corpus['heldOutSelectionObservations']:,}",
        f"- Exact-DOB observations below age {YOUNG_AGE_CUTOFF_DAYS / 365.2425:.0f}, by age in years: "
        f"{corpus['youngAgeHistogram'] or 'none'}. This is why the app does not claim a data-derived curve "
        f"for those ages.",
        f"- Age grid step: {corpus['gridDays']} days",
        f"- Fit tiers used: {corpus['levels']}",
        f"- Method: {corpus['method']}",
        "",
        "## Row rejections",
        "",
        "| reason | rows |",
        "| --- | --- |",
    ]
    for reason, count in sorted(rejected.items()):
        lines.append(f"| {reason} | {count:,} |")
    lines.append(f"| capped by per-federation limit | {capped:,} |")
    lines.append("")
    lines.append(f"Largest single `country x event x sex x season` cell before capping: {largest_cell:,} rows.")

    lines += ["", "## Cohorts", "",
              "`span` is where observations exist. `published` is the range the app will answer for: the",
              f"observed span, extended by up to {MAX_BACK_EXTRAPOLATION_DAYS / 365.2425:.0f} years along the local",
              "age slope where the fit has data to anchor that slope. Ages in `published` but not in `span`",
              "are interpolated from the fitted trend rather than observed, and are flagged as such in the app.",
              "",
              "| event | sex | surface | obs | dropped | athletes | local n (median) | sigma | span (y) | published (y) | confidence |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |"]
    for event in output["events"]:
        span = event["observedAgeDays"]
        published = event["supportedAgeDays"]
        lines.append(
            f"| {event['eventId']} | {event['sex']} | {event['surface']} | {event['observations']:,} | "
            f"{event['trimmedObservations']:,} | {event['athletes']:,} | {event['medianLocalN']:,} | {event['sigma']:.4f} | "
            f"{span[0]/365.2425:.1f}-{span[1]/365.2425:.1f} | "
            f"{published[0]/365.2425:.1f}-{published[1]/365.2425:.1f} | {event['confidence']} |"
        )

    lines += ["", "## Held-out validation against the elite selection tier", "",
              "World Athletics / Olympic / FISU results are excluded from the fit and used here as a",
              "held-out check. Those athletes are far better than the general population, so a mean",
              "percentile well below 50 is the expected result; the point of the check is that the value",
              "is nowhere near zero or near the median, which would mean the model is mis-scaled or",
              "that the elite sample is not actually elite. MAE is the mean distance from the median.",
              "",
              "| event | sex | surface | n | mean percentile | MAE |", "| --- | --- | --- | ---: | ---: | ---: |"]
    for row in validation:
        lines.append(f"| {row['eventId']} | {row['sex']} | {row['surface']} | {row['n']:,} | "
                     f"{row['meanPercentile']} | {row['maePercentile']} |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("csv", nargs="*", type=Path, help="CSV sources instead of the SQLite spool")
    parser.add_argument("--include-international", action="store_true",
                        help="also use the elite selection tier in the fit (not recommended)")
    parser.add_argument("--source-cap", type=int, default=SOURCE_CAP)
    parser.add_argument("--grid-days", type=int, default=7)
    arguments = parser.parse_args()
    build(arguments.db, arguments.csv, arguments.include_international, arguments.source_cap, arguments.grid_days)


if __name__ == "__main__":
    main()