"""Core simulator module (no external fake‑data libraries required)."""

import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

import yaml


class CabLogSimulator:
    """Generate synthetic cab logs driven entirely by YAML configuration.

    Parameters
    ----------
    log_cfg_path : str | Path
        Path to *log_configuration_cab.yaml* (global generation behaviour).
    key_cfg_path : str | Path
        Path to *log_keys_cab.yaml* (per‑key provider definitions).
    values_cfg_path : str | Path | None
        Path to *log_values_cab.yaml* (enumerations for random_element providers).
    delimiter : str | None
        Override delimiter (otherwise uses log_cfg['log_delimiter']).

    Notes
    -----
    The simulator is intentionally generic: every field is produced according
    to its declared ``provider`` in *log_keys_cab.yaml*.  Only the built‑in
    providers listed in ``_PROVIDERS`` below are supported out‑of‑the‑box,
    but it is trivial to add new ones.
    """

    # ------------------------------------------------------------------ #
    # SUPPORTED PROVIDERS
    # ------------------------------------------------------------------ #
    _PROVIDERS = {
        "incremental",
        "date",
        "time",
        "uuid4",
        "latitude",
        "longitude",
        "random_number",
        "random_element",
    }

    def __init__(
        self,
        log_cfg_path: str | Path,
        key_cfg_path: str | Path,
        values_cfg_path: str | Path | None = None,
        *,
        delimiter: str | None = None,
    ) -> None:
        self.log_cfg: Dict[str, Any] = self._load_yaml(log_cfg_path)
        self.key_cfg: Dict[str, Any] = self._load_yaml(key_cfg_path)

        self.values_cfg: Dict[str, Any] = (
            self._load_yaml(values_cfg_path) if values_cfg_path else {}
        )

        # Generator state
        self._incremental_state: Dict[str, int] = {}
        self._delimiter = delimiter or self.log_cfg.get("log_delimiter", " ")

        # Pre‑parse date range
        self._date_start = datetime.strptime(
            self.log_cfg["log_generation_start_date"], "%Y-%m-%d"
        )
        self._date_end = datetime.strptime(
            self.log_cfg["log_generation_end_date"], "%Y-%m-%d"
        )
        if self._date_end < self._date_start:
            raise ValueError("log_generation_end_date must be ≥ start date")

        # Cached weight tables
        self._monthly_weights = self.log_cfg["monthly_weights"]
        self._daily_weights = self.log_cfg["daily_weights"]
        self._hourly_weights = self.log_cfg["hourly_weights"]

        # Weekend / holiday modifiers
        self._special_dates = self.log_cfg.get("special_dates", {})
        self._weekend_days = {
            d.lower() for d in self.log_cfg.get("weekend_days", [])
        }
        self._weekend_modifier = float(self.log_cfg.get("weekend_modifier", 1.0))
        self._holiday_factor = float(
            self.log_cfg.get("holiday_adjustment_factor", 1.0)
        )

        # Sanity‑check providers
        for key, spec in self.key_cfg["log_keys"].items():
            provider = spec.get("provider")
            if provider not in self._PROVIDERS:
                raise NotImplementedError(f"Provider {provider!r} not supported")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def generate(self, n: int, *, as_dict: bool = False) -> List[str] | List[dict]:
        """Generate *n* log entries.

        Returns a list of strings (default) or dicts (``as_dict=True``).
        """
        out: list = []
        for _ in range(n):
            record: dict = {}
            for field, spec in self.key_cfg["log_keys"].items():
                provider = spec["provider"]
                fx = getattr(self, f"_p_{provider}")
                record[field] = fx(field, spec)

            out.append(record if as_dict else self._fmt_record(record))
        return out

    # ------------------------------------------------------------------ #
    # Provider implementations
    # ------------------------------------------------------------------ #
    def _p_incremental(self, field: str, spec: dict) -> int:
        start = int(spec.get("start", 0))
        inc = int(spec.get("increment", 1))
        current = self._incremental_state.get(field, start)
        self._incremental_state[field] = current + inc
        return current

    def _p_date(self, _field: str, spec: dict) -> str:
        # --- weighted month / day sampling --------------------------------
        date = self._sample_weighted_date()
        return date.strftime("%Y-%m-%d")

    def _p_time(self, _field: str, spec: dict) -> str:
        fmt = spec.get("format", "HH:MM:SS")
        time_obj = self._sample_weighted_time()
        if fmt == "HH:MM:SS.MS":
            return time_obj.strftime("%H:%M:%S.") + f"{time_obj.microsecond//1000:03}"
        return time_obj.strftime("%H:%M:%S")

    def _p_uuid4(self, _field: str, _spec: dict) -> str:
        return str(uuid.uuid4())

    def _p_latitude(self, _field: str, spec: dict) -> float:
        return round(
            random.uniform(float(spec["min"]), float(spec["max"])), 6
        )

    def _p_longitude(self, _field: str, spec: dict) -> float:
        return round(
            random.uniform(float(spec["min"]), float(spec["max"])), 6
        )

    def _p_random_number(self, _field: str, spec: dict) -> int:
        return random.randint(int(spec["min"]), int(spec["max"]))

    def _p_random_element(self, field: str, spec: dict) -> str:
        # 1) Get choices
        if field in self.values_cfg:
            choices = self.values_cfg[field]
        elif "elements" in spec:
            choices = spec["elements"]
        else:
            raise KeyError(
                f"No 'elements' defined for random_element field {field!r}"
            )
        # 2) Weights (optional)
        weights = spec.get("weights", [1] * len(choices))
        return random.choices(choices, weights=weights, k=1)[0]

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_yaml(path: str | Path) -> dict:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _fmt_record(self, record: dict) -> str:
        return self._delimiter.join(f"{k}={v}" for k, v in record.items())

    # ----------- date / time sampling with weights ---------------------- #
    def _sample_weighted_date(self) -> datetime:
        start, end = self._date_start, self._date_end

        # Build month‑weighted list once per call for simplicity
        months, month_wts = zip(*[(m, w) for m, w in self._monthly_weights.items()])
        while True:
            month_choice = random.choices(months, weights=month_wts, k=1)[0]
            # Candidate dates for that month
            dates_in_month = [
                d
                for d in self._daterange(start, end)
                if d.strftime("%B").lower() == month_choice
            ]
            if not dates_in_month:
                continue
            date = random.choice(dates_in_month)
            # Daily weight modifier
            day_name = date.strftime("%A").lower()
            day_weight = self._daily_weights.get(day_name, 1.0)

            # Weekend modifier
            if day_name in self._weekend_days:
                day_weight *= self._weekend_modifier

            # Holiday modifier
            date_key = date.strftime("%Y_%m_%d")
            if date_key in self._special_dates:
                day_weight *= self._holiday_factor

            # Accept‑reject sampling
            if random.random() < (day_weight / 10.0):
                return date

    def _sample_weighted_time(self) -> datetime:
        # Hour slot weighted choice
        slots, slot_wts = zip(*[(s, w) for s, w in self._hourly_weights.items()])
        slot_choice = random.choices(slots, weights=slot_wts, k=1)[0]
        h1, m1, h2, m2 = map(int, slot_choice.split("_"))
        delta_minutes = ((h2 * 60 + m2) - (h1 * 60 + m1)) or 60
        minute_offset = random.randint(0, delta_minutes - 1)
        second_offset = random.randint(0, 59)
        micro_offset = random.randint(0, 999_999)
        dt = datetime(2000, 1, 1, h1, m1) + timedelta(
            minutes=minute_offset, seconds=second_offset, microseconds=micro_offset
        )
        return dt

    @staticmethod
    def _daterange(start: datetime, end: datetime):
        for n in range((end - start).days + 1):
            yield start + timedelta(days=n)
