# Contained execution environment for the target repository's code and tests.
# The image holds only the interpreter and third-party packages; the repository
# copy is mounted at /work at run time (with no network and no credentials).
FROM python:3.12-slim

COPY requirements-target.txt /tmp/requirements-target.txt
RUN pip install --no-cache-dir -r /tmp/requirements-target.txt \
    && rm /tmp/requirements-target.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/work/src \
    HOME=/tmp
WORKDIR /work
