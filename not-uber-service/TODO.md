# Known issues to come back to

## city_zones_source has 260 zones, not the real TLC count of 263

`h-bootstrap/lion-prepare/taxi-zones.sql` now does `GROUP BY locationid`
with `ST_Union` to handle TLC zone ids that appear as more than one
feature in the raw import (confirmed live: locationid 56 duplicated,
which used to break `ADD PRIMARY KEY`). That fix landed and is correct
for a zone genuinely split into multiple polygon parts - but it was never
confirmed that's what's actually happening for all of them.

After the fix, a real run produced 260 zones instead of TLC's official
263 - a deficit of exactly 3, which is consistent with 3 ids each having
had 2 raw rows merged into 1. Not yet confirmed whether that's really 3
zones each legitimately split into 2 parts (the fix is correct), or
whether 2 of those 3 are actually 2 *different* real zones that happen to
share a locationid by a data error - in which case GROUP BY is silently
merging two real zones into one and losing a whole zone.

**Next step**: on the live stack, run
```sql
SELECT locationid, count(*) FROM taxi_zones_raw GROUP BY locationid HAVING count(*) > 1;
```
against the throwaway lion-pg container's data (or re-derive from
`city_zones_source` vs a fresh TLC LocationID list) to see exactly which
ids collapsed and whether their merged geometry is one sensible
multi-part shape or two unrelated polygons in different parts of the
city. If it's the latter for any of them, `taxi-zones.sql` needs a real
per-id decision, not a blanket GROUP BY.
