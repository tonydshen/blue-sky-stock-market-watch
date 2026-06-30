#!/bin/bash
# 03/30/26
# Gemini revised
# 04/10/26
# Cloude reviewed
# 04/17/26
# updated for bssetdb, a branch of bsset
# Exit immediately if a command exits with a non-zero status
# TS revised to suit blue-sky-post
#
set -e

echo "--- Starting Deployment ---"

# 1. Fetch the latest metadata
git fetch origin main

# 2. Hard Reset: This is the 'Secret Sauce'
# It forces the server to match GitHub exactly, throwing away
# any accidental local edits or merge conflicts.
echo "Syncing code with GitHub (Force Reset)..."
git reset --hard origin/main

echo "--------------------------------------"
echo "✅ Deployment Successful at $(date)"
echo "--------------------------------------"
