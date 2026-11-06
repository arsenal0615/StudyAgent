#!/bin/sh
# /data/studyagent/bin/start-api.sh
# Expose the tripo-cli AIHUB keys (systemd credentials) only to the API process.
set -e
key=$(tr -d "\r\n" < "$CREDENTIALS_DIRECTORY/aihub.key")
[ -n "$key" ] || { echo "aihub key is empty" >&2; exit 1; }
export AIHUB_API_KEY="$key" OPENAI_API_KEY="$key"
if [ -s "$CREDENTIALS_DIRECTORY/image.key" ]; then
  export STUDY_IMAGE_API_KEY="$(tr -d "\r\n" < "$CREDENTIALS_DIRECTORY/image.key")"
fi
exec /data/studyagent/tools/node/bin/node apps/api/dist/apps/api/src/main.js
