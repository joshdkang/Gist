"""Significance tests for the comparisons the results pages make, in plain Python (no SciPy).

- fisher_exact_p: two proportions, such as a theme's share of two periods' reviews.
- welch_t_p: two means, such as the average rating of reviews that mention a theme and those that
  don't.
- significant_changes: which of many such tests to mark, with the Benjamini-Hochberg correction,
  since a page tests every theme at once.
"""
import math


def fisher_exact_p(hits_a: int, total_a: int, hits_b: int, total_b: int) -> float | None:
    """The two-sided p-value of Fisher's exact test that two proportions are the same: how likely a
    split of the hits at least this lopsided is if both sides share one rate. None when a side is
    empty, since there is nothing to compare.

    Exact rather than a z-test, because themes are often raised by a handful of reviews in a period,
    where the normal approximation calls 0 of 590 against 4 of 538 a real change. It is valid at any
    count; at large counts it agrees with the z-test."""
    if total_a == 0 or total_b == 0:
        return None
    hits = hits_a + hits_b
    others = total_a + total_b - hits
    lowest, highest = max(0, total_a - others), min(hits, total_a)
    # The log probability of each possible count k for side a, with the margins fixed
    # (hypergeometric), built up from the lowest by the ratio between neighbouring terms.
    log_probabilities = [
        log_choose(hits, lowest) + log_choose(others, total_a - lowest) - log_choose(hits + others, total_a)
    ]
    for k in range(lowest, highest):
        ratio = (hits - k) * (total_a - k) / ((k + 1) * (others - total_a + k + 1))
        log_probabilities.append(log_probabilities[-1] + math.log(ratio))
    observed = log_probabilities[hits_a - lowest]
    # Every count at most as likely as the observed one counts towards the p-value (the tolerance
    # keeps counts that tie with it from being lost to rounding).
    return min(1.0, sum(math.exp(value) for value in log_probabilities if value <= observed + 1e-7))


def significant_changes(p_values: list[float | None], false_discovery_rate: float = 0.05) -> list[bool]:
    """Which of several tests are significant, controlling the false discovery rate with the
    Benjamini-Hochberg procedure: of the changes marked, at most this share are expected to be flukes.

    Testing 30 themes at 5% each would mark one or two by luck alone even if nothing changed, so the
    bar rises with the number of tests: the p-values are ranked, and the largest rank k whose p-value
    is at most k / m * rate marks it and every smaller one. A None (nothing to compare) is never
    significant and doesn't count as a test."""
    tested = sorted((p_value, index) for index, p_value in enumerate(p_values) if p_value is not None)
    cutoff = 0
    for rank, (p_value, _) in enumerate(tested, start=1):
        if p_value <= rank / len(tested) * false_discovery_rate:
            cutoff = rank
    significant = [False] * len(p_values)
    for _, index in tested[:cutoff]:
        significant[index] = True
    return significant


def log_choose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def welch_t_p(
    count_a: int, mean_a: float, variance_a: float, count_b: int, mean_b: float, variance_b: float
) -> float | None:
    """The two-sided p-value of Welch's t-test that two means are the same, from each side's count,
    mean and sample variance. Welch's version doesn't assume the two sides vary alike, which matters
    when a theme's few reviews are all one rating. None when a side has fewer than 2 values or
    neither varies (so there is no spread to judge the difference by).

    Ratings are whole numbers on a short scale, not a normal distribution, but the test is about
    the mean, which is close to normal once a side has a few dozen values; with fewer it is rough.
    """
    if count_a < 2 or count_b < 2:
        return None
    spread_a, spread_b = variance_a / count_a, variance_b / count_b
    standard_error = math.sqrt(spread_a + spread_b)
    if standard_error == 0:
        return None
    t = (mean_a - mean_b) / standard_error
    # Welch-Satterthwaite degrees of freedom.
    degrees = (spread_a + spread_b) ** 2 / (spread_a**2 / (count_a - 1) + spread_b**2 / (count_b - 1))
    # The two-sided tail of Student's t is a regularized incomplete beta function.
    return regularized_beta(degrees / 2, 0.5, degrees / (degrees + t * t))


def regularized_beta(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b), by its continued fraction (Lentz's
    method, as in Numerical Recipes), which converges quickly on the side of x it is used on."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(log_front) * _beta_fraction(a, b, x) / a
    return 1 - math.exp(log_front) * _beta_fraction(b, a, 1 - x) / b


def _beta_fraction(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    c, d = 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > tiny else tiny)
    fraction = d
    for m in range(1, 300):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1 + numerator * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + numerator / c
            c = c if abs(c) > tiny else tiny
            fraction *= c * d
        if abs(c * d - 1) < 1e-14:
            break
    return fraction
