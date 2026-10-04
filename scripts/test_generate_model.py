#!/usr/bin/env python3
"""Tests for the exact-DOB estimator.

Run with: python3 scripts/test_generate_model.py

These exist because the estimator's central claim - that the published curve is
monotone in age - used to be false, and a type error or a badly merged fit would
otherwise only be noticed by looking at a chart.
"""

import math
import sys
import unittest
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import generate_model as gm


def nearest(grid, age_days):
    return min(grid, key=lambda point: abs(point[0] - age_days))


def make_observations(median_at, sigma=0.05, ages=range(5000, 7300, 7), per_age=20, seed_shift=0.0):
    """Synthetic performances whose true median improves with age."""
    rows = []
    born = date(2005, 1, 1)
    for index, age_days in enumerate(ages):
        age_years = age_days / 365.2425
        median = median_at(age_years) * math.exp(seed_shift * index / max(1, len(ages)))
        for k in range(per_age):
            # Deterministic spread across +/-1.5 sigma, no RNG needed.
            offset = (k / max(1, per_age - 1) - 0.5) * 3 * sigma
            rows.append({
                "competition_id": 1,
                "athlete_id": f"a{index}-{k}",
                "event_id": "100m",
                "sex": "female",
                "birth_date": (born + timedelta(days=7305 - age_days)).isoformat(),
                "race_date": "2025-06-15",
                "seconds": median * math.exp(offset),
                "surface": "outdoor-track",
                "timing_method": "electronic",
                "country": "NOR",
                "level": "national",
                "season_year": 2025,
                "age_days": age_days,
                "log_seconds": math.log(median * math.exp(offset)),
            })
    return rows


class IsotonicTest(unittest.TestCase):
    def test_preserves_a_decreasing_sequence(self):
        fitted = gm.isotonic_decreasing([3.0, 2.0, 1.0], [1.0, 1.0, 1.0])
        self.assertEqual(fitted, [3.0, 2.0, 1.0])

    def test_pools_an_increase(self):
        self.assertEqual(gm.isotonic_decreasing([1.0, 3.0, 2.0], [1, 1, 1]), [2.0, 2.0, 2.0])

    def test_returns_one_point_per_input(self):
        # Regression: an earlier version expanded blocks by observation weight,
        # so a weighted fit silently returned thousands of values and the caller
        # truncated the curve to its first few bins.
        values = [3.0, 1.0, 2.0, 0.5, 4.0]
        weights = [2.0, 500.0, 1.0, 300.0, 7.0]
        fitted = gm.isotonic_decreasing(values, weights)
        self.assertEqual(len(fitted), len(values))
        for index in range(1, len(fitted)):
            self.assertGreaterEqual(fitted[index - 1], fitted[index] - 1e-12)

    def test_weighting_pulls_toward_the_heavier_value(self):
        fitted = gm.isotonic_decreasing([1.0, 3.0], [999.0, 1.0])
        self.assertAlmostEqual(fitted[0], fitted[1])


class NormalInverseTest(unittest.TestCase):
    def test_round_trips_known_quantiles(self):
        for probability in (0.001, 0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 0.999):
            self.assertAlmostEqual(gm.normal_cdf(gm.normal_inverse(probability)), probability, places=6)


class InterpolationTest(unittest.TestCase):
    def test_clamps_outside_the_anchor_range(self):
        anchors = [(10.0, 5.0), (20.0, 3.0)]
        self.assertEqual(gm.interpolate_anchors(anchors, 1.0), 5.0)
        self.assertEqual(gm.interpolate_anchors(anchors, 99.0), 3.0)

    def test_interpolates_linearly_inside(self):
        anchors = [(10.0, 5.0), (20.0, 3.0)]
        self.assertAlmostEqual(gm.interpolate_anchors(anchors, 15.0), 4.0)


class CohortFitTest(unittest.TestCase):
    def test_fit_is_monotone_and_close_to_the_truth(self):
        def truth(age):
            return 13.5 - 0.35 * (age - 13.5)  # faster with age

        rows = make_observations(truth)
        cohort = gm.fit_cohort("100m", "female", "outdoor-track", rows, grid_days=7)
        self.assertIsNotNone(cohort)
        medians = [point[2] for point in cohort["grid"]]
        for index in range(1, len(medians)):
            self.assertLessEqual(medians[index], medians[index - 1] + 1e-9, "median got slower with age")

        for age in (15, 17, 19):
            expected = truth(age)
            actual = nearest(cohort["grid"], age * 365.2425)[2]
            self.assertLess(abs(actual - expected) / expected, 0.01,
                            f"fitted median at {age}y is {actual:.3f}, truth {expected:.3f}")

    def test_quantile_order_holds(self):
        rows = make_observations(lambda age: 13.5 - 0.35 * (age - 13.5))
        cohort = gm.fit_cohort("100m", "female", "outdoor-track", rows, grid_days=7)
        for point in cohort["grid"]:
            self.assertLess(point[1], point[2])
            self.assertLess(point[2], point[3])

    def test_sigma_lands_inside_the_fitted_bounds(self):
        rows = make_observations(lambda age: 13.5, sigma=0.4)
        cohort = gm.fit_cohort("100m", "female", "outdoor-track", rows, grid_days=7)
        for point in cohort["grid"]:
            self.assertGreaterEqual(point[4], gm.SIGMA_FLOOR)
            self.assertLessEqual(point[4], gm.SIGMA_CEILING)

    def test_curve_is_not_published_beyond_the_support(self):
        def truth(age):
            return 13.5 - 0.35 * (age - 13.5)

        rows = make_observations(truth, ages=range(6000, 7300, 7))
        cohort = gm.fit_cohort("100m", "female", "outdoor-track", rows, grid_days=7)
        low, high = cohort["observedAgeDays"]
        self.assertGreaterEqual(low, 6000)
        self.assertLessEqual(high, 7299)
        # The published range may extend by the margin but no further.
        self.assertLessEqual(cohort["supportedAgeDays"][0], low)
        self.assertGreaterEqual(cohort["supportedAgeDays"][1], high)
        for point in cohort["grid"]:
            self.assertGreaterEqual(point[0], cohort["supportedAgeDays"][0])
            self.assertLessEqual(point[0], cohort["supportedAgeDays"][1])

    def test_local_counts_are_honest(self):
        ages = range(6000, 7300, 7)
        per_age = 20
        rows = make_observations(lambda age: 13.5, ages=ages, per_age=per_age)
        cohort = gm.fit_cohort("100m", "female", "outdoor-track", rows, grid_days=7)
        # +/-180 days holds about 360/7 distinct ages, each with per_age results.
        expected = (360 // 7 + 1) * per_age
        for point in cohort["grid"]:
            self.assertLessEqual(point[5], expected)
        self.assertGreater(cohort["medianLocalN"], 0)


class NormaliseTest(unittest.TestCase):
    def test_rejects_ages_outside_the_modelled_range(self):
        # Born 2000-01-01, raced 2009-06-01: 3,423 days, below the 10-year floor.
        rows = [{
            "athlete_id": "x", "event_id": "100m", "sex": "female",
            "birth_date": "2000-01-01", "race_date": "2009-06-01", "seconds": 12.0,
            "surface": "outdoor-track", "timing_method": "electronic",
            "country": "NOR", "level": "national", "season_year": 2009,
        }]
        kept, rejected, young = gm.normalise(rows)
        self.assertEqual(kept, [])
        self.assertEqual(rejected["range"], 1)
        self.assertEqual(young, {})

    def test_accepts_ages_inside_the_modelled_range(self):
        # Born 2009-06-16, raced 2025-06-15: 6,210 days, well inside.
        rows = [{
            "athlete_id": "x", "event_id": "100m", "sex": "female",
            "birth_date": "2009-06-16", "race_date": "2025-06-15", "seconds": 12.0,
            "surface": "outdoor-track", "timing_method": "electronic",
            "country": "NOR", "level": "national", "season_year": 2025,
        }]
        kept, rejected, young = gm.normalise(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected["range"], 0)
        self.assertEqual(young, {})

    def test_rejects_impossible_surfaces(self):
        rows = [{
            "athlete_id": "x", "event_id": "60m", "sex": "female",
            "birth_date": "2010-01-01", "race_date": "2025-01-01", "seconds": 8.0,
            "surface": "cross-country", "timing_method": "electronic",
            "country": "NOR", "level": "national", "season_year": 2025,
        }]
        kept, rejected, young = gm.normalise(rows)
        self.assertEqual(kept, [])
        self.assertEqual(rejected["surface"], 1)


class CappingTest(unittest.TestCase):
    def test_cap_limits_a_dominant_federation(self):
        rows = []
        for country, count in (("CHN", 500), ("NOR", 10)):
            for index in range(count):
                template = make_observations(lambda age: 13.5, ages=[6000], per_age=1)[0]
                rows.append({**template, "athlete_id": f"{country}-{index}", "country": country})
        kept, dropped, largest = gm.cap_sources(rows, 100)
        counts = defaultdict(int)
        for row in kept:
            counts[row["country"]] += 1
        self.assertEqual(counts["CHN"], 100)
        self.assertEqual(counts["NOR"], 10)
        self.assertEqual(dropped, 400)
        self.assertEqual(largest, 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)