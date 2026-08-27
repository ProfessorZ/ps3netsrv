# ps3netsrv
![Build Status](https://github.com/aldostools/ps3netsrv/actions/workflows/build.yml/badge.svg)

ps3netsrv is a server application used to stream content from a remote server to the PS3.

Supports automatic decryption of encrypted PS3 ISO and folder conversion to PS3 ISO or standard ISO.

For more information: https://github.com/aldostools/webMAN-MOD/wiki/~-PS3-NET-Server

Command line syntax:
```
ps3netsrv  [rootdirectory] [port] [whitelist]

 Default port: 38008

 Whitelist: x.x.x.x, where x is 0-255 or *
 (e.g 192.168.1.* to allow only connections from 192.168.1.0-192.168.1.255)
```

ISO conversion / decryption command line syntax:
```
ps3netsrv  [game directory / encrypted iso] [PS3/ISO]
```

## Features

* Support up to 5 PS3 clients concurrently
* Configurable shared root directory (uses ps3netsrv path if the root directory is omitted)
* Configurable port (38008 is used by default if port is omitted)
* Start without command line parameters if GAMES, PS3ISO, PSXISO folders are found in ps3netsrv folder
* List local server IP addresses
* Remote IP address filtering: Whitelist IP addresses using wildcards
* Remote file operations (stat, open, create, read, close, delete, mkdir, rmdir)
* Remote directory listing (whole directory at once or by file) / include subdirectories
* List files in specified directory and all subdirectories if the path ends with //
* Merge multiple paths into a single directory (list paths in folder_name.INI)
* Streaming of ISO images (CD-ROM, CD-ROM XA, DVD, Bluray or PS3 Blurays)
* Detection of standard & non-standard CD sector sizes: 2048, 2352, 2336, 2448, 2328, 2368, 2340
* Multi-part ISO support (ISO images split as *.iso.0, *.iso.1, etc.)
* Realtime decryption of PS3 ISO images (3k3y & redump encrypted images)
* Realtime conversion of mounted folder to virtual ISO (vISO)
* Convert game folder or directory to local ISO file (drag & drop the ISO or folder for easy conversion)
* Decrypt encrypted PS3 ISO (using redump/3k3y encryption) into a new decrypted ISO

## Requirements

* A C/C++ compiler

* [Meson](https://mesonbuild.com/Getting-meson.html)

* [mbed TLS](https://tls.mbed.org/)
  * Is being searched for in system library directory (e.g. `/usr/lib`) and custom directories specified in `LIBRARY_PATH`.
  If using a custom directory, put the path to the headers in `C_INCLUDE_PATH`.

`Meson` and `mbed TLS` are available as packages for most posix environments.

`Meson` and `mbed TLS` are not needed if using the Alternate building method.


## Building
**Warning**: *If you get "buffer overflow" errors on Linux, try to use the Alternate building method*

First configure a build directory:

```bash
$ meson buildrelease --buildtype=release
```

Then build using ninja:

```bash
$ ninja -C buildrelease
```

For further information see [Running Meson](https://mesonbuild.com/Running-Meson.html).

## Alternate building
To build ps3netsrv using the bundled POLARSSL library instead of mbed TLS and without Meson:
* use `_make.bat` on Windows 
* use `Make.sh` on Linux

On Linux this will statically link ps3netsrv.

```
sudo apt-get update
sudo apt-get install make
sudo apt-get install g++
./Make.sh
```

## Docker Container
This repository ships a `Dockerfile` that builds ps3netsrv from source and runs it
as an unprivileged user. The image compiles with the *Alternate building method*
(bundled POLARSSL), so no Meson or mbed TLS is required.

Build the image:

```bash
docker build --build-arg BUILD_DATE=$(date +%Y%m%d) -t ps3netsrv .
```

Run it, sharing a directory of games on the default port:

```bash
docker run -d --name ps3netsrv -p 38008:38008 -v /path/to/games:/games:ro ps3netsrv
```

Or with Docker Compose — put your media in `./games` (or edit the volume) and run:

```bash
BUILD_DATE=$(date +%Y%m%d) docker compose up -d
```

### Configuration

The container is configured with environment variables, which map onto the
`ps3netsrv [rootdirectory] [port] [whitelist]` command line:

| Variable | Default | Description |
| --- | --- | --- |
| `PS3NETSRV_ROOT` | `/games` | Shared directory inside the container. Cannot be `/`. |
| `PS3NETSRV_PORT` | `38008` | Listening port. Must be in the 1024-65535 range. |
| `PS3NETSRV_WHITELIST` | *(unset)* | Restrict connections, e.g. `192.168.1.*`. |

Any arguments passed after the image name are handed straight to `ps3netsrv`
instead, so the variables stay a convenience rather than a restriction:

```bash
docker run --rm -v /path/to/games:/games:ro ps3netsrv /games 38008 '192.168.1.*'
```

### File permissions

The image runs as uid/gid `1000:1000`. If your media is owned by a different
user, override it so the server can read the share:

```bash
docker run -d --user $(id -u):$(id -g) -p 38008:38008 -v /path/to/games:/games:ro ps3netsrv
```

The equivalent in Compose is the `user:` key, which is already present in
`docker-compose.yml`.

### Notes

* The share is mounted read-only in the examples above. Mount it read-write if
  you want to use the remote file operations (create, delete, mkdir, rmdir).
* ps3netsrv writes its log to stdout, so use `docker logs -f ps3netsrv` to
  follow it. The entrypoint runs the server through `stdbuf` to keep that
  output line-buffered; without it the logs would stall behind a full pipe
  buffer.
* Don't pass `-i` (or set `stdin_open: true`). ps3netsrv ends its error paths
  with a "Press ENTER to continue..." prompt, which exits immediately when
  stdin is closed but would hang forever with stdin attached.
* The image uses `tini` as its init so `docker stop` shuts the server down
  promptly, and the entrypoint ignores `SIGPIPE` so a console that disconnects
  mid-transfer cannot take the server down with it.
* Compose maps `./games` into the container. The directory is in the repository
  already; make sure its contents are readable by the `user:` in
  `docker-compose.yml`.
* Third-party ps3netsrv images are also published on Docker Hub:
  https://hub.docker.com/search?q=ps3netsrv

# Other ports / forks
The ps3netsrv has been ported to multiple platforms (Windows, linux, FreeBSD, MacOS, PS3, Android, Java)<br>

ps3netsrv (webMAN MOD):<br>
https://github.com/aldostools/webMAN-MOD/tree/master/_Projects_/ps3netsrv

Docker container: https://github.com/shawly/docker-ps3netsrv

Java / Android: https://github.com/jhonathanc/ps3netsrv-android

Synology (maintained by @Hirador)
https://synocommunity.com/package/ps3netsrv <br>

QNAP NAS<br>
https://www.myqnap.org/product/ps3netsrv-ng/ <br>

Xcode (macOS / Linux / FreeBSD 10):<br>
https://github.com/klahjn/ps3netsrvXCODE <br>
https://github.com/klahjn/macOSPS3NetServerGUI

Google Go: https://github.com/xakep666/ps3netsrv-go

ps3netsrv modified for encrypted (3k3y/redump) isos: http://forum.redump.org/topic/14472

ps3netsrv modified for multiMAN:
https://github.com/aldostools/webMAN-MOD/blob/master/_Projects_/ps3netsrv/bins/old/ps3netsrv-src-deank.7z

Original ps3netsrv by Cobra for Linux / Windows:<br>
https://github.com/Joonie86/Cobra-7.00/tree/master/446/PC/ps3netsrv
