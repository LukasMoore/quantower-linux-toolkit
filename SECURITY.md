# Security and private diagnostic data

This toolkit launches software with the current user's permissions. Wine is not a
sandbox, and a dedicated prefix does not isolate an untrusted Windows executable
from that user's files. Obtain application/runtime inputs from their publishers,
verify provenance, and use only trusted installers.

Treat logs and support archives as private. Vendor output may include account IDs,
orders, symbols, session information and other sensitive data. The support collector
uses an allowlist and bounded tails, but does not guarantee redaction. Do not paste
raw archives into a public issue. Create a minimal synthetic reproduction and share
only the information needed to understand the failure.

For a suspected security defect, use the repository host's private vulnerability
reporting feature when available, or an established private maintainer channel.
Do not put secrets or a working exploit against a user's installation in a public
issue. This repository does not invent a security contact address; if no private
channel is available, start with a general request for one and omit sensitive details.

No automatic crash upload, remote telemetry, broker access, credential collection
or unattended application restart is implemented. Review executable diagnostic
startup hooks before enabling them; they run inside the Windows process.

The release checker inspects indexed source and detects a limited set of unsafe
paths, payload types and secret patterns. It cannot establish that every arbitrary
string is safe to publish. If credentials were published, remove the exposed data
and rotate them with the issuing service; deleting a current file alone does not
erase repository history or cached copies.
