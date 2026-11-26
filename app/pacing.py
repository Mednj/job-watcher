"""LinkedIn pacing shared by every search, without storing account information."""

RECOVERY_SUCCESSES = 8
MAX_GAP_SECONDS = 120


def restricted(gap, minimum):
    return min(max(MAX_GAP_SECONDS, minimum), max(gap, minimum) * 2)


def recovered(gap, successes, minimum):
    gap = max(gap, minimum)
    successes += 1
    if successes >= RECOVERY_SUCCESSES:
        return max(minimum, gap * 0.75), 0
    return gap, successes
