# Security policy

## Supported versions

LenkRaster has not published a stable release. Security fixes are made on the default
branch and will be included in the next source release.

## Reporting a vulnerability

When the repository's **Security** tab shows **Report a vulnerability**, use GitHub's
private vulnerability reporting form. GitHub exposes that form only after the repository
is public and the maintainer enables it. If the form is unavailable, open a minimal issue
that asks the maintainer for a private contact channel, but include no vulnerability
details, private artwork, credentials, logs, or fixtures in that issue.

Include the affected commit, a minimal synthetic reproducer, expected impact, and the
operating system/Python version. Reports should use generated or openly licensed inputs.

## Maintained threat boundary

LenkRaster is a trusted-local library, desktop GUI, CLI, and stdio MCP process. It does
not implement authentication, a network listener, or a public HTTP transport.

The maintained boundary includes:

- strict trusted-root containment after path resolution, including Studio's explicit
  trusted workspace;
- strict schema, byte, color-count, and trusted-root limits for user-owned palette JSON;
- fixed path-free Studio, MCP, and CLI failures;
- encoded-byte, decoded-pixel, frame, discovery, JSON, request, and aggregate-work limits;
- create-only generated outputs;
- no shell command construction or caller-controlled Aseprite flags;
- isolated per-invocation Aseprite profiles, bounded version probing, and optional
  executable SHA-256 pinning;
- hidden complete staging followed by a single create-only directory publication for
  Aseprite exports;
- one active Studio worker so repeated controls cannot multiply bounded image work;
- no API keys, network calls, model downloads, or telemetry.

Studio previews stay in memory and its PNG and Aseprite exports are create-only. File
dialogs do not expand trust for artwork or palette inputs or export destinations: a
selection outside the explicit trusted workspace is rejected. The separately installed
Aseprite executable is the explicit exception; it may live elsewhere and is hashed and
pinned when selected. The interface displays relative labels rather than machine paths,
and its `PASS` and `REVIEW` states remain advisory rather than approval decisions.

The optional Aseprite bridge launches the operator's separately installed native
executable. LenkRaster validates its inputs and generated outputs, but does not sandbox
that process. The bridge redirects Aseprite's user folder and common profile variables so
normal user extensions and preferences are not loaded, but this is process isolation, not
an operating-system security sandbox. Treat Aseprite documents as trusted local input or
run them inside an operating-system sandbox. Pin the executable with
`LENKRASTER_ASEPRITE_SHA256` when practical and update the pin only after verifying an
intentional application update.

When an operator selects an Aseprite executable in Studio, the interface hashes it and
passes the automatically computed SHA-256 pin directly to the bridge for that session.
The executable is checked again by the bridge; Studio does not persist the executable path
or hash as a recent-file setting.

## Out of scope

- A public or remotely exposed wrapper around the stdio process.
- Vulnerabilities in Aseprite, Pillow, NumPy, Python, or an MCP client itself.
- Deliberately disabling limits or trusted-root containment in a downstream fork.
- Artwork quality disagreements; LenkRaster reports are advisory.
