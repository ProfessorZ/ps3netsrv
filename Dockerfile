# ps3netsrv - streams PS3 content from a shared directory to the console.
#
# Built with the project's "Alternate building method" (bundled POLARSSL, no
# Meson/mbed TLS): the README warns that the Meson build can produce buffer
# overflow errors on Linux.

FROM alpine:3.20 AS builder

RUN apk add --no-cache build-base

WORKDIR /src
COPY . .

# Stamped into the startup banner. Defaults to the same "unknown" the source
# falls back to, so an unstamped build stays reproducible.
ARG BUILD_DATE=unknown

# Set to any non-empty value to build a diagnostic image that logs one line per
# READ_FILE_CRITICAL, for profiling a console's access pattern with
# tools/analyse-trace.py. The printf sits on the streaming hot path, so such an
# image is measurably slower -- never ship one.
ARG TRACE_READS=

# Makefile.linux is the Linux path the project's own Make.sh uses: it compiles
# VIsoFile.cpp and defines off64_t=off_t, which is what musl needs.
#
# Its release_static target adds only -static, so optimisation is supplied here
# instead -- this server does realtime AES decryption of ISO streams, so -O0
# would be felt as throughput. CFLAGS/CXXFLAGS carry the codegen flags while
# the Makefile's own CPPFLAGS still supplies the include path and defines.
#
# Deliberately NOT static: stdbuf (see the entrypoint) works by LD_PRELOAD,
# which a statically linked binary ignores, and without it ps3netsrv's stdio
# block-buffers onto the log pipe and `docker logs` shows nothing but the
# banner. The runtime stage is the same Alpine release, so linking dynamically
# costs only the libstdc++ package.
RUN make -f Makefile.linux \
        BUILD_DATE="${BUILD_DATE}" \
        CFLAGS="-Wall -std=gnu99 -O3 -s -DNDEBUG${TRACE_READS:+ -DTRACE_READS}" \
        CXXFLAGS="-Wall -O3 -s -DNDEBUG${TRACE_READS:+ -DTRACE_READS}"


FROM alpine:3.20

# libstdc++ satisfies the binary; coreutils supplies stdbuf; tini is the init.
RUN apk add --no-cache libstdc++ coreutils tini

COPY --from=builder /src/ps3netsrv /usr/local/bin/ps3netsrv
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Fail the build here rather than at first `docker run` if the runtime stage is
# missing something the binary needs to load.
RUN ps3netsrv </dev/null 2>&1 | grep -q "Usage:"

# Fixed unprivileged uid/gid. Override with `--user` (or compose `user:`) to
# match the ownership of the media you mount.
RUN addgroup -g 1000 -S ps3netsrv \
    && adduser -u 1000 -S -G ps3netsrv -H -s /sbin/nologin ps3netsrv \
    && mkdir -p /games \
    && chown ps3netsrv:ps3netsrv /games

ENV PS3NETSRV_ROOT=/games \
    PS3NETSRV_PORT=38008

VOLUME ["/games"]
EXPOSE 38008/tcp

USER ps3netsrv
WORKDIR /games

# ps3netsrv installs no signal handlers, and a process that is PID 1 ignores
# every signal it has no handler for -- so as PID 1 it would sit through
# SIGTERM and make every `docker stop` wait out the full 10s kill timeout.
# tini takes PID 1 so the server gets normal signal dispositions again.
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
