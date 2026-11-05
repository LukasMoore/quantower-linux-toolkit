# Contributing

Changes should keep the source checkout independent of proprietary application
state. Use a new disposable installation for manual integration work and synthetic
fixtures for automated tests. Do not test process cleanup against a user's running
application.

Run the isolated test suite, syntax checks and public-source checker described in
README.md. Add a regression test when behavior changes or a reproduced fault is
corrected. CI exercises supported Python versions without Wine, a display or a
trading account. Native builds and hardware integration require separate evidence;
state the actual tested configuration and the limits of each measurement.

Preserve exact process UID, prefix, session token, PID start-time and pidfd checks.
An active or unidentifiable Starter prevents cleanup. Broad process-name kills,
global Wine-server commands and automatic restarts are outside this design.
Changing these guards requires ownership and PID-reuse regression tests.

Native patches should be small, apply to pinned official source and retain upstream
copyright/license notices. Include a synthetic reproducer, exact source identity,
build procedure and validation status. Keep signed vendor application binaries and
licensing behavior intact. Do not suppress errors to claim an issue is fixed.

Keep public commits free of installation paths, credentials, runtime binaries,
fonts, artwork, raw logs, private settings and platform performance reports. Review
all indexed files before publishing; `.gitignore` does not remove previously staged
or committed content. The automated release checker is an additional check, not a
substitute for reviewing a public diff.

A contribution retains the license applicable to the file it changes. Original
new toolkit code should use the project's MIT license. Changes to upstream-derived
files retain the upstream terms. Explain any new dependency and its redistribution
requirements in THIRD_PARTY.md.
