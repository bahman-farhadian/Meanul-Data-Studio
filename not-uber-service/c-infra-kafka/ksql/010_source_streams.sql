-- One stream per topic this stack declares.
--
-- Until this file existed, ksqlDB was a running server with nothing in it:
-- DBeaver connected, listed the topics, and showed an empty Streams folder -
-- correctly, because a topic is bytes and a stream is the schema over it.
--
-- THE VALUE COLUMNS ARE NOT LISTED HERE, AND THAT IS THE POINT.
--
-- Each statement declares the KEY column and stops. ksqlDB reads the value
-- columns out of Schema Registry, from the very schema the producers
-- registered from c-infra-kafka/schemas/*.avsc. A column list here would be
-- a fourth copy of a schema this repository already keeps in three places
-- (Avro, Postgres, ClickHouse) and would go stale the first time a field is
-- added. Inference cannot go stale.
--
-- What that inference produces, confirmed against cp-ksqldb-server:8.3.1
-- with the real schemas registered, not assumed:
--
--   Avro enum                  -> STRING   (TripStatus, DriverStatus, ...)
--   timestamp-millis           -> TIMESTAMP
--   bytes/decimal(10,2)        -> DECIMAL(10, 2)
--   ["null", X]                -> nullable X
--
-- The decimal line is the one worth reading twice: the money fields stay
-- DECIMAL(10, 2) here, the same representation they hold in Postgres
-- (numeric(10,2)), on the wire, and in ClickHouse (Decimal64(2)). There is
-- no float hop anywhere in the chain, including this one.
--
-- The KEY column is named <entity>_key rather than <entity>_id because the
-- value already carries the id under its own name, and ksqlDB refuses a
-- schema whose key and value columns collide. It is the same string either
-- way - the producers key with StringSerializer('utf_8').
--
-- TIMESTAMP=... makes ksqlDB use the event's own time as the row time
-- rather than when the broker happened to receive it. Everything windowed
-- downstream depends on it, and a late message then lands in the window it
-- belongs to instead of the window it arrived in.
--
-- Note for anyone reaching for INSERT INTO: it will not work on these
-- streams, and that is correct. ksqlDB would serialize with a schema it
-- derived from the column types, which is not the schema the producers
-- registered, and Schema Registry rejects it. These streams are read-only
-- views of topics owned by the services. Confirmed by trying it.

CREATE STREAM IF NOT EXISTS driver_location (
    driver_key STRING KEY
) WITH (
    KAFKA_TOPIC  = 'driver_location',
    KEY_FORMAT   = 'KAFKA',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP    = 'event_time'
);

CREATE STREAM IF NOT EXISTS rider_location (
    rider_key STRING KEY
) WITH (
    KAFKA_TOPIC  = 'rider_location',
    KEY_FORMAT   = 'KAFKA',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP    = 'event_time'
);

-- requested_at, not event_time: a request has no separate outcome time, and
-- the envelope on this topic carries the request itself.
CREATE STREAM IF NOT EXISTS trip_requests (
    trip_key STRING KEY
) WITH (
    KAFKA_TOPIC  = 'trip_requests',
    KEY_FORMAT   = 'KAFKA',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP    = 'requested_at'
);

CREATE STREAM IF NOT EXISTS trip_lifecycle (
    trip_key STRING KEY
) WITH (
    KAFKA_TOPIC  = 'trip_lifecycle',
    KEY_FORMAT   = 'KAFKA',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP    = 'event_time'
);

CREATE STREAM IF NOT EXISTS dispatch_offers (
    trip_key STRING KEY
) WITH (
    KAFKA_TOPIC  = 'dispatch_offers',
    KEY_FORMAT   = 'KAFKA',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP    = 'event_time'
);

CREATE STREAM IF NOT EXISTS city_hotspots (
    zone_key STRING KEY
) WITH (
    KAFKA_TOPIC  = 'city_hotspots',
    KEY_FORMAT   = 'KAFKA',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP    = 'computed_at'
);

CREATE STREAM IF NOT EXISTS segment_traffic_updates (
    zone_key STRING KEY
) WITH (
    KAFKA_TOPIC  = 'segment_traffic_updates',
    KEY_FORMAT   = 'KAFKA',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP    = 'computed_at'
);
