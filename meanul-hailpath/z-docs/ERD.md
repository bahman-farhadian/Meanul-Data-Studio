# OLTP entity diagram — version 1

Tables are the ones left after `h-bootstrap/migrations` run in order.
Edges are the foreign keys those migrations declare. A column with
no `REFERENCES` is not drawn, even when the name looks like one.

```mermaid
erDiagram
    drivers
    passengers
    trips
    city_zones
    segment_traffic
    vehicles
    trip_ratings
    driver_sessions
    zone_demand_calibration
    od_pair_calibration
    dispatch_offers
    passengers ||--|{ trips : rider_id
    drivers ||--o{ trips : driver_id
    drivers ||--|{ vehicles : driver_id
    trips ||--|{ trip_ratings : trip_id
    drivers ||--|{ driver_sessions : driver_id
    city_zones ||--|{ zone_demand_calibration : zone_id
    city_zones ||--|{ od_pair_calibration : pickup_zone_id
    city_zones ||--|{ od_pair_calibration : dropoff_zone_id
    trips ||--|{ dispatch_offers : trip_id
    drivers ||--|{ dispatch_offers : driver_id
```
