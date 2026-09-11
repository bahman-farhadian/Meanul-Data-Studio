# Known issues to come back to

## ~~city_zones_source has 260 zones, not the real TLC count of 263~~ — fixed

Root-caused by downloading the real GeoJSON and checking it directly
against TLC's own `taxi_zone_lookup.csv`: ids 56/57 are both officially
"Corona, Queens" and 103/104/105 are all officially "Governor's Island/
Ellis Island/Liberty Island, Manhattan" - the shapefile just never labels
the extra polygon parts with their real official ids. The original
GROUP BY + ST_Union fix silently merged these into 260 zones instead of
263; `taxi-zones.sql` now assigns each duplicate's parts to its real
sibling ids explicitly (see the commit for the full reasoning), verified
live against the real downloaded data - 263 zones, ids 56/57/103/104/105
all present with real geometry.

Requires a fresh `make lion-prepare` to pick this up, and the existing
stale-cache check will NOT catch it on its own this time: it only checks
that `city_zones_source` exists in the cached dump's table of contents,
and it does (with the wrong, 260-zone data) in a dump built before this
fix. Delete the cached dump manually before the next `make up`:
`rm $NUS_VOLUME_ROOT/nus-lion-data/routable-graph.dump` on the host.
