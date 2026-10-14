"""The bad-data guard. Runs before any cut, in dry-run and live alike.

A bulk report can be wrong: one pull once said a brand-new ad had spent $74 on 11 impressions when
its real spend was $3.25. Acting on that pauses a working ad. So before a cut, the ad's spend and
impressions are read again for that one ad ID straight from the provider, and the cut is held when:

- the snapshot's cost per 1,000 impressions is impossible (over `max_cpm`), or there is spend
  with no impressions at all,
- the fresh read's own CPM is impossible, or
- the snapshot shows more spend than the fresh read by over `spend_tolerance` dollars.

Spend only grows between the snapshot and the re-read, so a snapshot above the fresh read means the
snapshot overstated spend, which is the error that causes false cuts. A fresh read that is higher
(the ad kept spending for a few minutes) only makes the cut more justified and does not hold it.
"""

from __future__ import annotations


def cpm(spend: float, impressions: int) -> float | None:
    return spend * 1000 / impressions if impressions else None


def check(snap_spend: float, snap_impressions: int, live_spend: float, live_impressions: int,
          spend_tolerance: float = 1.0, max_cpm: float = 500.0) -> list[str]:
    """Reasons not to trust the snapshot for this ad. Empty list means it checks out."""
    reasons = []
    c = cpm(snap_spend, snap_impressions)
    if c is not None and c > max_cpm:
        reasons.append("snapshot says $%.2f on %d impressions, a $%.0f CPM (limit $%.0f)"
                       % (snap_spend, snap_impressions, c, max_cpm))
    if snap_impressions == 0 and snap_spend > spend_tolerance:
        reasons.append("snapshot says $%.2f with 0 impressions" % snap_spend)
    lc = cpm(live_spend, live_impressions)
    if lc is not None and lc > max_cpm:
        reasons.append("fresh read says $%.2f on %d impressions, a $%.0f CPM (limit $%.0f)"
                       % (live_spend, live_impressions, lc, max_cpm))
    if snap_spend - live_spend > spend_tolerance:
        reasons.append("snapshot says $%.2f but a fresh read says $%.2f (tolerance $%.2f)"
                       % (snap_spend, live_spend, spend_tolerance))
    return reasons
