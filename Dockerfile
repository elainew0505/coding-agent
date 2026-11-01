# Agent image for the MVP (spec rule S1).
#
# The container runs with --network none, so anything the agent needs at run
# time must be installed here, at build time. This image only has what the toy
# repo's tests need (pytest); test commands in other repos will fail on missing
# packages, as the spec's Scope section says.
#
# Build it on your Mac (this step does need network):
#   docker build -t coding-agent:toy .

# Pinned by digest so the base can't change under you (S1). To update: pull
# python:3.12-slim and copy the digest from `docker image inspect`.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# git is not in the slim base image; the harness needs it for the baseline and
# diff (S5, D1). tar and bash are already included.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# The toy repo's test dependency, baked in because there's no network later.
RUN pip install --no-cache-dir "pytest>=8,<9"

# Copy-in unpacks into this folder, so it must exist (S1).
WORKDIR /workspace
