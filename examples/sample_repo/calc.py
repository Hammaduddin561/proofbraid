"""Sample repo for the Proofbraid demo: a tiny calculator.

Deliberately contains one stale-interface bug for the demo: ``double``
calls ``add`` with a keyword the current ``add`` does not accept... in
fact, no: the bug here is simpler and real. ``add`` is off by one.
The demo task is: "fix the off-by-one in add()".
"""


def add(a, b):
    """Return the sum of a and b."""
    return a + b + 1  # BUG: off by one


def double(x):
    """Double a number using add."""
    return add(x, x)
