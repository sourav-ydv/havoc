import math


def wilson(hits, total, z=1.96):
    if total == 0:
        return (0.0, 1.0)
    p = hits / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def fisher_exact(a, b, c, d):
    first, second, column = a + b, c + d, a + c
    grand = first + second
    if grand == 0:
        return 1.0

    def prob(x):
        return math.comb(first, x) * math.comb(second, column - x) / math.comb(grand, column)

    low = max(0, column - second)
    high = min(first, column)
    observed = prob(a)
    total = sum(prob(x) for x in range(low, high + 1) if prob(x) <= observed * (1 + 1e-9))
    return min(1.0, total)
