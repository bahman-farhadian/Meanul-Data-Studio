# The one-shot that turns a real month of NYC TLC trip records into the
# two small calibration tables real demand generation reads.
#
# Built once, run once by `make zone-demand-prepare`, then gone - unlike
# lion-prepare, this step needs no database at all: it is pure tabular
# aggregation (group by, count, mean) over Parquet columns, never a
# geometry operation, so a plain Python image with pandas/pyarrow is the
# whole dependency, not a throwaway Postgres alongside it.

ARG PYTHON_IMAGE=python:3.13-slim

FROM ${PYTHON_IMAGE}

RUN pip install --no-cache-dir pandas pyarrow

COPY h-bootstrap/zone-demand-prepare/prepare.py /prepare.py

ENTRYPOINT ["python", "/prepare.py"]
