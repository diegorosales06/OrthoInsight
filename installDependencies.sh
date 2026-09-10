#!/bin/bash


# change to whatever directory you want to install the wheels from
WHEEL_DIR="./offline_wheels"

pip install `
    --no-index `
    --find-links "$WHEEL_DIR" `
    "$WHEEL_DIR"/*.whl