"""Plain-Python statistics helpers for rent values. Deterministic, no dependencies."""
import math


def _percentile(sorted_values: list[float], q: float) -> float:
    """Percentile by linear interpolation between closest ranks (q between 0 and 1)."""
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def calculate_percentiles(rents: list[float]) -> dict[str, float]:
    if not rents:
        raise ValueError("rents must not be empty")
    values = sorted(rents)
    return {
        "p25": _percentile(values, 0.25),
        "median": _percentile(values, 0.50),
        "p75": _percentile(values, 0.75),
    }


def calculate_average(rents: list[float]) -> float:
    if not rents:
        raise ValueError("rents must not be empty")
    return sum(rents) / len(rents)


def calculate_std_dev(rents: list[float]) -> float:
    """Sample standard deviation (n - 1). A single value has a deviation of 0."""
    if not rents:
        raise ValueError("rents must not be empty")
    if len(rents) == 1:
        return 0.0
    mean = calculate_average(rents)
    variance = sum((r - mean) ** 2 for r in rents) / (len(rents) - 1)
    return math.sqrt(variance)


def remove_outliers(rents: list[float]) -> list[float]:
    """Drop values below Q1 - 1.5*IQR or above Q3 + 1.5*IQR. Keeps the original order.

    With fewer than 4 values there is too little data to judge outliers, so all are kept.
    """
    if len(rents) < 4:
        return list(rents)
    percentiles = calculate_percentiles(rents)
    q1, q3 = percentiles["p25"], percentiles["p75"]
    iqr = q3 - q1
    low = q1 - 1.5 * iqr
    high = q3 + 1.5 * iqr
    return [r for r in rents if low <= r <= high]
