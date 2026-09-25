"""The root assessment contract must stay aligned with shipped checks.

Reads ASSESSMENT.md and the SQL / Avro / DDL / geo / Makefile sources it
cites. Numbers, closed-set symbols, id widths, topic names, and Makefile
target names are taken from those files, not copied into this test as
literals.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# this file lives at not-uber-service/z-lib/nus-common/tests/
NUS = TESTS.parents[2]
ROOT = TESTS.parents[3]
STANDARD = ROOT / "ASSESSMENT.md"


def _standard() -> str:
    assert NUS.name == "not-uber-service", NUS
    assert STANDARD.is_file(), f"missing assessment contract: {STANDARD}"
    return STANDARD.read_text()


def _on_network_sql() -> str:
    path = NUS / "z-config" / "check-on-network.sql"
    return path.read_text()


def _geo_source() -> str:
    return (NUS / "z-lib" / "nus-common" / "nus_common" / "geo.py").read_text()


def _makefile_targets() -> set[str]:
    root_make = NUS / "Makefile"
    texts = [root_make.read_text()]
    for inc in re.finditer(r"^include\s+(\S+)", texts[0], re.M):
        path = NUS / inc.group(1)
        if path.is_file():
            texts.append(path.read_text())
    names: set[str] = set()
    for text in texts:
        for match in re.finditer(r"^\.PHONY:\s*(.+)$", text, re.M):
            names.update(match.group(1).split())
        for match in re.finditer(r"^([a-zA-Z][a-zA-Z0-9_-]*):", text, re.M):
            names.add(match.group(1))
    return names


def test_standard_lists_every_lettered_component():
    text = _standard()
    dirs = sorted(
        p.name
        for p in NUS.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and (re.match(r"^[a-o]-", p.name) or p.name in {"z-config", "z-lib"})
    )
    assert dirs, f"no lettered components under {NUS}"
    missing = [name for name in dirs if name not in text]
    assert missing == [], f"ASSESSMENT.md missing components: {missing}"


def test_off_network_metres_match_shipped_sql():
    sql = _on_network_sql()
    pickup = re.search(r"pickup_m > (\d+)", sql)
    dropoff = re.search(r"dropoff_m > (\d+)", sql)
    assert pickup and dropoff, sql
    metres = pickup.group(1)
    assert metres == dropoff.group(1)
    text = _standard()
    assert f"{metres} m" in text or f"{metres}m" in text
    assert metres in text


def test_long_two_point_route_matches_shipped_sql():
    sql = _on_network_sql()
    npoints = re.search(r"ST_NPoints\(route\)\s*<=\s*(\d+)", sql)
    length_m = re.search(r"ST_Length\(route::geography\)\s*>\s*(\d+)", sql)
    assert npoints and length_m, sql
    text = _standard()
    assert npoints.group(1) in text
    assert "ST_NPoints" in text or "NPoints" in text
    metres = length_m.group(1)
    assert metres in text
    km = int(metres) / 1000
    assert km == int(km)
    assert f"{int(km)} km" in text


def test_chord_min_km_matches_shipped_geo():
    geo = _geo_source()
    match = re.search(
        r"def is_chord_path\([^)]*min_km:\s*float\s*=\s*([0-9.]+)",
        geo,
    )
    assert match, geo
    min_km = match.group(1)
    text = _standard()
    assert min_km in text
    assert "is_chord_path" in text
    assert f"{min_km} km" in text or f"min_km={min_km}" in text or f"min_km = {min_km}" in text


def test_named_make_targets_exist():
    text = _standard()
    named = set(re.findall(r"make ([a-z][a-z0-9_-]*)", text))
    assert named, "ASSESSMENT.md names no make targets"
    targets = _makefile_targets()
    missing = sorted(name for name in named if name not in targets)
    assert missing == [], f"ASSESSMENT.md names make targets that do not exist: {missing}"


def test_live_walk_and_tile_terms_are_in_the_sources():
    text = _standard()
    ids = (NUS / "z-lib" / "nus-common" / "nus_common" / "ids.py").read_text()
    redis = (NUS / "z-lib" / "nus-common" / "nus_common" / "redis_client.py").read_text()
    driver = (NUS / "j-service-driver" / "driver_service" / "__main__.py").read_text()
    haproxy = (NUS / "z-config" / "haproxy" / "haproxy.cfg.template").read_text()
    tiles_make = (NUS / "f-infra-grafana" / "Makefile").read_text()

    assert "drv-" in ids and "drv-" in text
    assert "psg-" in ids and "psg-" in text
    assert "trp-" in ids and "trp-" in text
    assert "SIM_TIMEZONE" in _geo_source() and "SIM_TIMEZONE" in text
    assert "trip_active" in redis and "trip_active" in text
    assert "path_chord" in driver and "path_p50" in driver
    assert "path_chord" in text and "path_p50" in text
    assert "/tiles/" in haproxy and "/tiles/" in text
    assert "tiles-health" in tiles_make and "make tiles-health" in text
    assert "image/png" in tiles_make and "image/png" in text


def test_studio_contract_sections_and_roles():
    text = _standard()
    for heading in (
        "## 4. Schema contract",
        "## 5. Message-broker contract",
        "## 6. Cache contract",
        "## 7. Data-generation contract",
        "## 8. Simulation-accuracy contract",
        "## 9. Version 1",
    ):
        assert heading in text, heading
    for role in (
        "`oltp`",
        "`cache`",
        "`broker`",
        "`cdc`",
        "`warehouse`",
        "`live-ui`",
        "`analytic-ui`",
        "`bootstrap`",
        "`cache-updater`",
        "`generator`",
        "`coordinator`",
        "`warehouse-sink`",
        "`archiver`",
        "`stack-config`",
        "`shared-lib`",
    ):
        assert role in text, role


def _migrations() -> list[Path]:
    """Every migration, in the order the bootstrap applies them."""
    found = sorted((NUS / "h-bootstrap" / "migrations").glob("*.sql"))
    assert found, "no migrations found"
    return found


def _pg_check_symbols(sql: str, constraint: str) -> list[str]:
    match = re.search(
        rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\(\s*\w+\s+IN\s*\(([^)]+)\)",
        sql,
        re.I | re.S,
    )
    assert match, f"no CHECK list for {constraint}"
    return re.findall(r"'([^']+)'", match.group(1))


def _pg_effective_check(constraint: str) -> list[str]:
    """The CHECK list as it stands after every migration has run.

    A later migration may drop a constraint and add it back with another
    symbol - 013 does exactly that to trips_status_check when it appends
    'arrived'. Reading only the file that first created the table would
    compare the warehouse against a set that no longer exists.
    """
    symbols: list[str] | None = None
    for path in _migrations():
        text = path.read_text()
        for match in re.finditer(
            rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\(.*?IN\s*\(([^)]+)\)",
            text,
            re.I | re.S,
        ):
            symbols = re.findall(r"'([^']+)'", match.group(1))
    assert symbols, f"no CHECK list for {constraint} in any migration"
    return symbols


def _pg_trips_columns() -> set[str]:
    """Every column trips has once all migrations have run."""
    columns: set[str] = set()
    for path in _migrations():
        text = path.read_text()
        create = re.search(
            r"CREATE TABLE IF NOT EXISTS trips\s*\((.*?)\n\);", text, re.S
        )
        if create:
            for line in create.group(1).splitlines():
                name = re.match(r"\s{4}(\w+)\s+\S", line)
                if name and name.group(1).upper() != "CONSTRAINT":
                    columns.add(name.group(1))
        for add in re.finditer(
            r"ALTER TABLE trips ADD COLUMN IF NOT EXISTS (\w+)", text, re.I
        ):
            columns.add(add.group(1))
        for drop in re.finditer(
            r"ALTER TABLE trips DROP COLUMN IF EXISTS (\w+)", text, re.I
        ):
            columns.discard(drop.group(1))
    assert "trip_id" in columns, columns
    return columns


def _avro_field_names(path: Path) -> set[str]:
    return {field["name"] for field in json.loads(path.read_text())["fields"]}


def _ch_columns(sql: str, table: str) -> set[str]:
    start = sql.index(f"{table} ON CLUSTER")
    body = sql[start : sql.index("ENGINE =", start)]
    names: set[str] = set()
    for line in body.splitlines():
        match = re.match(r"\s{4}(\w+)\s+\S", line)
        if match:
            names.add(match.group(1))
    assert names, table
    return names


def _avro_enum_symbols(path: Path, enum_name: str) -> list[str]:
    def walk(node: object) -> list[str] | None:
        if isinstance(node, dict):
            nested = node.get("type")
            if nested == "enum" and node.get("name") == enum_name:
                return list(node["symbols"])
            if isinstance(nested, dict):
                found = walk(nested)
                if found is not None:
                    return found
            for value in node.values():
                found = walk(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = walk(value)
                if found is not None:
                    return found
        return None

    found = walk(json.loads(path.read_text()))
    assert found, f"no Avro enum {enum_name} in {path}"
    return found


def _ch_enum8_symbols(sql: str, after: str) -> list[str]:
    idx = sql.index(after)
    match = re.search(r"Enum8\s*\((.*?)\)", sql[idx:], re.S)
    assert match, f"no Enum8 after {after}"
    return re.findall(r"'([^']+)'", match.group(1))


def test_closed_status_sets_align_across_stores():
    core = (NUS / "h-bootstrap" / "migrations" / "002_core_tables.sql").read_text()
    city = (NUS / "h-bootstrap" / "migrations" / "003_city.sql").read_text()
    positions = (NUS / "e-infra-clickhouse" / "ddl" / "002_positions.sql").read_text()
    trips = (NUS / "e-infra-clickhouse" / "ddl" / "003_trips.sql").read_text()
    hotspots = (NUS / "e-infra-clickhouse" / "ddl" / "004_hotspots.sql").read_text()
    geo = _geo_source()

    driver_pg = _pg_check_symbols(core, "drivers_status_check")
    driver_avro = _avro_enum_symbols(
        NUS / "c-infra-kafka" / "schemas" / "driver_location.avsc",
        "DriverStatus",
    )
    driver_ch = _ch_enum8_symbols(positions, "driver_positions_local")
    assert driver_pg == driver_avro == driver_ch

    # The effective set, not 002's: 013 drops and re-adds this constraint
    # to append 'arrived'.
    trip_pg = _pg_effective_check("trips_status_check")
    trip_avro = _avro_enum_symbols(
        NUS / "c-infra-kafka" / "schemas" / "trip_lifecycle.avsc",
        "TripStatus",
    )
    trip_ch = _ch_enum8_symbols(trips, "trip_events_local")
    assert trip_pg == trip_avro == trip_ch
    # 'arrived' must stay last in all three. An Enum8's numbers are what
    # ClickHouse stores, so moving a symbol into its lifecycle position
    # would renumber every symbol after it and reinterpret existing rows.
    assert trip_pg[-1] == "arrived", trip_pg

    lifecycle = NUS / "c-infra-kafka" / "schemas" / "trip_lifecycle.avsc"
    for constraint, enum_name, column in (
        ("trips_payment_method_check", "PaymentMethod", "payment_method"),
        ("trips_cancellation_reason_check", "CancellationReason", "cancellation_reason"),
        ("trips_requested_vehicle_type_check", "VehicleType", "requested_vehicle_type"),
    ):
        pg = _pg_effective_check(constraint)
        avro = _avro_enum_symbols(lifecycle, enum_name)
        ch = _ch_enum8_symbols(trips, f"    {column} ")
        assert pg == avro == ch, (constraint, pg, avro, ch)

    offers_pg = _pg_effective_check("dispatch_offers_status_check")
    offers_avro = _avro_enum_symbols(
        NUS / "c-infra-kafka" / "schemas" / "dispatch_offers.avsc", "OfferStatus"
    )
    offers_ch = _ch_enum8_symbols(
        (NUS / "e-infra-clickhouse" / "ddl" / "010_dispatch_offers.sql").read_text(),
        "dispatch_offers_local",
    )
    assert offers_pg == offers_avro == offers_ch

    period_pg = _pg_check_symbols(city, "segment_traffic_period_check")
    period_avro = _avro_enum_symbols(
        NUS / "c-infra-kafka" / "schemas" / "city_hotspots.avsc",
        "DayPeriod",
    )
    period_ch = _ch_enum8_symbols(hotspots, "hotspot_history_local")
    periods = re.search(r"DAY_PERIODS\s*=\s*\(([^)]+)\)", geo)
    assert periods, geo
    period_py = re.findall(r'"([^"]+)"', periods.group(1))
    assert period_pg == period_avro == period_ch == period_py

    text = _standard()
    assert "CHECK" in text
    assert "Enum8" in text
    assert "Avro" in text


def test_every_trip_field_on_the_wire_has_a_home_in_both_stores():
    """A column in one store and nowhere else is the failure this catches.

    driver_payout, payment_method, cancellation_reason and
    requested_vehicle_type sat in Postgres for months while being absent
    from the Avro record and from ClickHouse, so the warehouse - the only
    store dashboards may read - could not answer take rate, payment mix,
    why trips cancel, or anything per tier. The closed-set test above did
    not see it: it compares symbol lists for sets that exist in all three
    places, not the existence of the field itself.
    """
    avro = _avro_field_names(NUS / "c-infra-kafka" / "schemas" / "trip_lifecycle.avsc")
    pg = _pg_trips_columns()
    ch = _ch_columns(
        (NUS / "e-infra-clickhouse" / "ddl" / "003_trips.sql").read_text(),
        "nus.trip_events_local",
    )

    # The envelope describes the message, not the trip, so it has no trips
    # column - except event_id, which the warehouse stores to dedupe on.
    envelope_only = {"event_version", "producer", "correlation_id", "event_id"}
    # event_time is when the status change happened; the trip's own clock
    # is the six milestone columns, which trips does have.
    wire_only = {"event_time"}

    missing_in_pg = sorted((avro - envelope_only - wire_only) - pg)
    assert missing_in_pg == [], (
        f"on trip_lifecycle but not in trips: {missing_in_pg}"
    )

    missing_in_ch = sorted((avro - {"event_version", "producer", "correlation_id"}) - ch)
    assert missing_in_ch == [], (
        f"on trip_lifecycle but not in trip_events_local: {missing_in_ch}"
    )

    # And the reverse: a trips column that never reaches the wire is the
    # same defect pointing the other way. rider_id is named rider_id on
    # both sides; route and the two points are geometry, which CDC
    # deliberately excludes (see test_cdc_excludes_oltp_geometry).
    not_shipped = {
        "created_at", "updated_at", "attributes",
        "pickup_point", "dropoff_point", "route",
    }
    stranded = sorted(pg - avro - not_shipped)
    assert stranded == [], f"in trips but on no topic: {stranded}"


def test_the_two_warehouse_writers_agree_on_every_column():
    """h-bootstrap and clickhouse-sink write the same tables.

    The seeded history and the live stream both insert into nus.trip_events
    and friends, from two separate column lists in two separate packages. A
    column added to one and not the other produces rows that disagree about
    what they contain, which is the same class of drift that hid
    driver_payout from the warehouse in the first place.
    """
    sink = (
        NUS / "n-service-clickhouse-sink" / "clickhouse_sink" / "batches.py"
    ).read_text()
    bootstrap = (NUS / "h-bootstrap" / "bootstrap" / "warehouse.py").read_text()

    def sink_columns(table: str) -> list[str]:
        match = re.search(rf'"{re.escape(table)}":\s*\[(.*?)\]', sink, re.S)
        assert match, table
        return re.findall(r'"([^"]+)"', match.group(1))

    def bootstrap_columns(name: str) -> list[str]:
        match = re.search(rf"^{name}\s*=\s*\[(.*?)^\]", bootstrap, re.S | re.M)
        assert match, name
        return re.findall(r'"([^"]+)"', match.group(1))

    for table, constant in (
        ("nus.trip_events", "TRIP_EVENT_COLUMNS"),
        ("nus.dispatch_offers", "DISPATCH_OFFER_COLUMNS"),
        ("nus.driver_positions", "DRIVER_POSITION_COLUMNS"),
        ("nus.rider_positions", "RIDER_POSITION_COLUMNS"),
        ("nus.hotspot_history", "HOTSPOT_COLUMNS"),
    ):
        assert sink_columns(table) == bootstrap_columns(constant), table


def test_every_warehouse_table_carries_an_event_id():
    """Uniqueness has to hold before ClickHouse, so every table needs the id.

    ClickHouse does not deduplicate on its own, which is the whole reason
    the envelope exists. A table without event_id is a table whose counts
    cannot be checked, and the quality bar in step 7 would silently pass it.
    """
    ddl_dir = NUS / "e-infra-clickhouse" / "ddl"
    missing = []
    for path in sorted(ddl_dir.glob("*.sql")):
        for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (nus\.\w+_local)", path.read_text()):
            table = match.group(1)
            # A rollup holds aggregates of many events, not one event, so
            # it has no single id to carry - and it inherits the guarantee
            # from the table it is built on.
            if table.endswith(("_hourly_local", "_daily_local")):
                continue
            if "event_id" not in _ch_columns(path.read_text(), table):
                missing.append(table)
    assert missing == [], f"warehouse tables with no event_id: {missing}"


def test_minted_id_widths_match_warehouse():
    ids = (NUS / "z-lib" / "nus-common" / "nus_common" / "ids.py").read_text()
    ddl = "\n".join(
        path.read_text()
        for path in sorted((NUS / "e-infra-clickhouse" / "ddl").glob("*.sql"))
    )

    def _length(name: str) -> str:
        match = re.search(rf"^{name}\s*=\s*(\d+)", ids, re.M)
        assert match, name
        return match.group(1)

    driver_n = _length("DRIVER_ID_LENGTH")
    passenger_n = _length("PASSENGER_ID_LENGTH")
    trip_n = _length("TRIP_ID_LENGTH")
    assert re.search(rf"driver_id\s+FixedString\({driver_n}\)", ddl)
    assert re.search(rf"rider_id\s+FixedString\({passenger_n}\)", ddl)
    assert re.search(rf"trip_id\s+FixedString\({trip_n}\)", ddl)
    text = _standard()
    assert "FixedString" in text
    assert driver_n in text and passenger_n in text and trip_n in text


def test_declared_topics_have_avro_schemas():
    tsv = (NUS / "c-infra-kafka" / "topics" / "topics.tsv").read_text()
    names: list[str] = []
    for line in tsv.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name = line.split("\t")[0].strip()
        if name:
            names.append(name)
    assert names, tsv
    assert all(not name.startswith("cdc.") for name in names)
    schemas = NUS / "c-infra-kafka" / "schemas"
    missing = [name for name in names if not (schemas / f"{name}.avsc").is_file()]
    assert missing == [], missing
    extra = sorted(path.stem for path in schemas.glob("*.avsc") if path.stem not in names)
    assert extra == [], extra
    text = _standard()
    for name in names:
        assert name in text, name
    assert "topics.tsv" in text
    create = (NUS / "c-infra-kafka" / "topics" / "create-topics.sh").read_text()
    assert "--replication-factor 3" in create
    assert "min.insync.replicas=2" in create
    assert "replication-factor 3" in text
    assert "min.insync.replicas=2" in text


def test_cdc_excludes_oltp_geometry():
    connector = json.loads(
        (NUS / "d-infra-debezium" / "connectors" / "nus-pg.json").read_text()
    )
    excluded = connector["config"]["column.exclude.list"]
    assert "nus.trips.route" in excluded
    text = _standard()
    assert "nus.trips.route" in text
    assert "column.exclude.list" in text


def test_oltp_uses_jsonb_not_a_document_db():
    core = (NUS / "h-bootstrap" / "migrations" / "002_core_tables.sql").read_text()
    assert "jsonb" in core.lower()
    compose = "\n".join(
        path.read_text().lower() for path in NUS.rglob("docker-compose.yaml")
    )
    assert "mongo" not in compose
    text = _standard()
    assert "jsonb" in text.lower()


def test_cache_updater_skips_bootstrap_wait():
    updater = (NUS / "i-service-cache-updater" / "cache_updater" / "__main__.py").read_text()
    assert "wait_for_bootstrap" not in updater
    assert "does not wait" in updater.lower()
    must_wait = (
        NUS / "j-service-driver" / "driver_service" / "__main__.py",
        NUS / "k-service-passenger" / "passenger_service" / "__main__.py",
        NUS / "l-service-dispatch" / "dispatch_service" / "__main__.py",
        NUS / "m-service-city" / "city_service" / "__main__.py",
        NUS / "n-service-clickhouse-sink" / "clickhouse_sink" / "__main__.py",
        NUS / "o-service-archiver" / "archiver_service" / "__main__.py",
    )
    for path in must_wait:
        source = path.read_text()
        assert "wait_for_bootstrap" in source, path
    text = _standard()
    assert "wait_for_bootstrap" in text
    assert "does not wait" in text.lower()


def test_store_live_state_before_announce_is_required():
    dispatch = (NUS / "l-service-dispatch" / "dispatch_service" / "__main__.py").read_text()
    store = dispatch.index("def store_live_state")
    announce = dispatch.index("def announce")
    # Both exist; call order on the match path is store then announce.
    match_block = dispatch[dispatch.index("store_live_state(redis_trip, trip, now, active_ttl)") :]
    first_store = match_block.index("store_live_state")
    first_announce = match_block.index("announce(")
    assert first_store < first_announce
    text = _standard()
    assert "store_live_state" in text
    assert "announce" in text
