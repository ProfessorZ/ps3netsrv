#!/bin/sh
# Translates the container's environment into a ps3netsrv command line.
set -eu

# ps3netsrv calls send() with no MSG_NOSIGNAL and installs no SIGPIPE handler,
# so a console that drops mid-transfer would otherwise kill the server. An
# ignored disposition survives exec, so setting it here covers the binary; the
# send() calls already check their return value and will see EPIPE instead.
trap '' PIPE

# Any argument given to `docker run` is passed straight through to ps3netsrv,
# so the env-var interface stays a convenience rather than a cage.
if [ "$#" -gt 0 ]; then
	exec stdbuf -oL -eL ps3netsrv "$@"
fi

ROOT_DIR="${PS3NETSRV_ROOT:-/games}"
PORT="${PS3NETSRV_PORT:-38008}"
WHITELIST="${PS3NETSRV_WHITELIST:-}"

# ps3netsrv refuses to share / outright, and sharing it from a container would
# expose the image's own filesystem rather than the user's media.
if [ "$ROOT_DIR" = "/" ]; then
	echo "ERROR: PS3NETSRV_ROOT must not be '/'." >&2
	exit 1
fi

if [ ! -d "$ROOT_DIR" ]; then
	echo "ERROR: shared directory '$ROOT_DIR' does not exist inside the container." >&2
	echo "Mount your media there, e.g. -v /path/to/games:$ROOT_DIR" >&2
	exit 1
fi

# An unreadable share looks identical to an empty one from a bare listing, so
# check access first -- otherwise the wrong uid gets reported as a missing
# mount, which is the opposite of the actual fix.
if [ ! -r "$ROOT_DIR" ] || [ ! -x "$ROOT_DIR" ]; then
	echo "ERROR: '$ROOT_DIR' is not readable by uid $(id -u):$(id -g)." >&2
	echo "Run with --user (or compose 'user:') matching the owner of your media." >&2
	exit 1
fi

# /games exists in the image, so a forgotten -v serves an empty share rather
# than failing. Say so instead of letting it look like a working setup.
# -print -quit stops at the first entry rather than walking a whole media
# library just to answer "is there anything here at all".
if [ -z "$(find "$ROOT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
	echo "WARNING: '$ROOT_DIR' is empty - did you forget to mount your media?" >&2
fi

# On POSIX targets ps3netsrv rejects ports below 1024 before it ever binds, so
# catch it here with a message that names the variable at fault.
case "$PORT" in
	*[!0-9]*|'')
		echo "ERROR: PS3NETSRV_PORT must be a number, got '$PORT'." >&2
		exit 1
		;;
esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
	echo "ERROR: PS3NETSRV_PORT must be in the 1024-65535 range, got '$PORT'." >&2
	exit 1
fi

# The port must always be passed positionally: invoked with only a root
# directory, ps3netsrv probes for PS3_GAME/PARAM.SFO and silently switches to
# ISO-conversion mode instead of serving.
if [ -n "$WHITELIST" ]; then
	set -- "$ROOT_DIR" "$PORT" "$WHITELIST"
else
	set -- "$ROOT_DIR" "$PORT"
fi

# ps3netsrv logs through stdio, which block-buffers onto a pipe and would make
# `docker logs` look dead for minutes at a time.
exec stdbuf -oL -eL ps3netsrv "$@"
