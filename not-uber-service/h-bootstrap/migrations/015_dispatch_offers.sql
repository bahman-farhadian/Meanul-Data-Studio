-- Who dispatch asked, in what order, and what they said.
--
-- Dispatch assigns a driver directly today and the driver always accepts,
-- so a trip goes matched -> accepted with nothing in between. Real
-- platforms offer: one candidate at a time, with a deadline, and on a
-- decline or a timeout the request moves to the next driver. The
-- ride-sourcing literature measures that deadline at roughly 20 seconds
-- and names driver response rate as a headline platform KPI; it also finds
-- that a long pickup drive depresses acceptance and that surge raises it.
--
-- This table is the whole matching funnel: acceptance rate, offers per
-- match, time-to-match, and how deep dispatch had to search in an
-- undersupplied zone. None of those are answerable without it. See
-- docs/schema-review.md F5.
--
-- No session_id column, unlike the reference model this follows:
-- dispatch-service does not hold the driver's driver_sessions row id, and
-- the session is recoverable by joining driver_sessions on driver_id and
-- the offer's own timestamp. A column dispatch would have to look up on
-- every offer, to store something already derivable, is not worth the
-- round trip at this message rate.

CREATE TABLE IF NOT EXISTS dispatch_offers (
    offer_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id              text        NOT NULL REFERENCES trips(trip_id),
    driver_id            text        NOT NULL REFERENCES drivers(driver_id),
    -- Position in the offer chain for this trip, starting at 1. The chain
    -- is the object of interest, not the individual offer.
    sequence             smallint    NOT NULL,
    offered_at           timestamptz NOT NULL,
    -- When the offer lapses if the driver says nothing.
    expires_at           timestamptz NOT NULL,
    -- NULL for an offer that expired without an answer - which is a
    -- different outcome from a decline and is kept apart on purpose.
    responded_at         timestamptz,
    status               text        NOT NULL,
    -- Driving time and distance from the driver to the pickup point at the
    -- moment of the offer. Both are inputs to whether a driver accepts, so
    -- they are stored as they were offered, not recomputed later.
    eta_seconds          integer,
    distance_to_pickup_m integer,

    CONSTRAINT dispatch_offers_status_check CHECK (status IN (
        'offered', 'accepted', 'declined', 'expired', 'cancelled'
    )),
    CONSTRAINT dispatch_offers_sequence_check CHECK (sequence >= 1),
    CONSTRAINT dispatch_offers_window_check CHECK (expires_at > offered_at),
    -- An offer that has an answer must have a time for it, and one that
    -- does not must not. Keeps "declined with no responded_at" out of the
    -- data rather than out of the dashboards.
    CONSTRAINT dispatch_offers_response_check CHECK (
        (status IN ('accepted', 'declined') AND responded_at IS NOT NULL)
        OR (status NOT IN ('accepted', 'declined') AND responded_at IS NULL)
    ),
    -- One offer per position per trip. A second offer at sequence 3 would
    -- be a bug in dispatch's own chain bookkeeping.
    CONSTRAINT dispatch_offers_chain_unique UNIQUE (trip_id, sequence)
);

-- At most one driver can accept a given trip. This is the same idea as
-- driver_sessions_one_open_idx: a race in dispatch that hands one trip to
-- two drivers becomes a loud constraint violation instead of two drivers
-- driving to the same rider.
CREATE UNIQUE INDEX IF NOT EXISTS dispatch_offers_one_accepted_idx
    ON dispatch_offers (trip_id) WHERE status = 'accepted';

-- Acceptance rate per driver over time - the per-driver half of the funnel.
CREATE INDEX IF NOT EXISTS dispatch_offers_driver_idx
    ON dispatch_offers (driver_id, offered_at DESC);
-- "What happened in this window" for the funnel rollups and for Grafana.
CREATE INDEX IF NOT EXISTS dispatch_offers_offered_at_idx
    ON dispatch_offers (offered_at DESC);
