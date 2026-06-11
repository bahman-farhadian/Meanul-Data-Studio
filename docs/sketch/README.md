> **Note:** this is the original throwaway sketch, kept only as a reference
> for field shapes and the YAML-driven weighted-sampling configuration style.
> The real implementation is described in the repository's main README.

# Cab Log Simulator

A fully self‑contained Python project for generating synthetic NYC‑style
cab ride logs purely from YAML configuration files – **no external faker
libraries required**.

## Project layout

```
cab_log_simulator/
    cab_log_simulator/
        __init__.py
        simulator.py
        cli.py
    config/
        log_configuration_cab.yaml
        log_keys_cab.yaml
        log_values_cab.yaml
```

See `config/` for all configuration files.  The generator is driven entirely
by these YAMLs; extend or tweak them to change behaviour or add new fields.

## Quick start

```bash
# From repo root
python -m cab_log_simulator.cli --config-dir ./config -n 500 -o logs.txt
```
