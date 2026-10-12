#!/bin/sh
# Rewrite database URLs onto the Compose network, then run the container command.
# The printed exports contain passwords. This script does not echo them.
set -eu
if [ -n "${FT_DB_HOST:-}" ]; then
  eval "$(python -c 'from ft_bot.config import shell_exports; print(shell_exports())')"
fi
exec "$@"
