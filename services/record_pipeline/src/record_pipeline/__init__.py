"""Process source records one at a time, each one carried the whole way through.

The batch services each own one stage and run it across a whole file. This
package owns one *record* and runs every stage on it — normalize, validate,
quarantine, resolve, golden — so that when a record is done, everything derived
from it is current and it can be handed to another system immediately.

It reimplements none of that logic. Every decision still comes from the module
that owns it, so the two paths cannot drift apart.
"""
