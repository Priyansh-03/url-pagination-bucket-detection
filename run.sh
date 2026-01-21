#!/bin/bash
# Helper script to run the classifier using the local virtual environment

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Virtual environment 'venv' not found. Creating it..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

# Run the classifier with passed arguments
echo "Running classifier with local venv..."
./venv/bin/python3 classifier.py "$@"
