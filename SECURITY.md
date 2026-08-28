# Security policy

## Supported versions

LenkRaster has not published a stable release. Security fixes are made on the default
branch and will be included in the next source release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature in the repository's
**Security** tab. Do not open a public issue for a suspected vulnerability, include
private artwork in a report, or paste credentials into logs or fixtures.

Include the affected commit, a minimal synthetic reproducer, expected impact, and the
operating system/Python version. Reports should use generated or openly licensed inputs.

## Maintained threat boundary

LenkRaster is a trusted-local library, CLI, and stdio MCP process. It does not implement
authentication, a network listener, or a public HTTP transport.

The maintained boundary includes:

- strict trusted-root containment after path resolution;
- strict schema, byte, color-count, and trusted-root limits for user-owned palette JSON;
- fixed path-free MCP and CLI failures;
- encoded-byte, decoded-pixel, frame, discovery, JSON, request, and aggregate-work limits;
- create-only generated outputs;
- no shell command construction or caller-controlled Aseprite flags;
- isolated per-invocation Aseprite profiles, bounded version probing, and optional
  executable SHA-256 pinning;
- hidden complete staging followed by a single create-only directory publication for
  Aseprite exports;
- no API keys, network calls, model downloads, or telemetry.

The optional Aseprite bridge launches the operator's separately installed native
executable. LenkRaster validates its inputs and generated outputs, but does not sandbox
that process. The bridge redirects Aseprite's user folder and common profile variables so
normal user extensions and preferences are not loaded, but this is process isolation, not
an operating-system security sandbox. Treat Aseprite documents as trusted local input or
run them inside an operating-system sandbox. Pin the executable with
`LENKRASTER_ASEPRITE_SHA256` when practical and update the pin only after verifying an
intentional application update.

## Out of scope

- A public or remotely exposed wrapper around the stdio process.
- Vulnerabilities in Aseprite, Pillow, NumPy, Python, or an MCP client itself.
- Deliberately disabling limits or trusted-root containment in a downstream fork.
- Artwork quality disagreements; LenkRaster reports are advisory.
