#!/bin/bash

# Define the path to your .env file
ENV_FILE="$HOME/agents/blue-sky-stock-market-watch/.env"

# 1. Read the .env file (safely handling spaces in paths)
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

# 2 & 3. Ensure PROMPT_SRC and PROMPT_PATH are obtained successfully
if [ -z "$PROMPT_SRC" ] || [ -z "$PROMPT_PATH" ]; then
    echo "Error: PROMPT_SRC or PROMPT_PATH is not defined in the .env file."
    exit 1
fi

# 4. Look for the newest file starting with "US Stock Market Update"
# We quote the directory and the prefix, but leave the * outside to allow expansion
NEWEST_FILE=$(ls -t "${PROMPT_SRC}/US Stock Market Update"* 2>/dev/null | head -n 1)

if [ -z "$NEWEST_FILE" ]; then
    echo "No files matching 'US Stock Market Update' found in ${PROMPT_SRC}."
    exit 1
fi

echo "Newest prompt file found: $NEWEST_FILE"

# 5. Copy the source file to PROMPT_PATH
# Quotes are critical here to handle the space in the source file name
cp "$NEWEST_FILE" "$PROMPT_PATH"
echo "Copied to destination: $PROMPT_PATH"

# 6. Read the destination file timestamp attributes (Modification Time)
TIMESTAMP=$(date -r "$PROMPT_PATH" +"%Y%m%d%H%M")

# 7. Make a backup copy of PROMPT_PATH with the timestamp suffix
BASE_PATH="${PROMPT_PATH%.md}"
BACKUP_PATH="${BASE_PATH}-${TIMESTAMP}.md"

cp "$PROMPT_PATH" "$BACKUP_PATH"
echo "Backup successfully created: $BACKUP_PATH"
