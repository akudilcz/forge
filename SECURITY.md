# Security Policy

## Reporting a vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Report them privately through
[GitHub Security Advisories](https://github.com/akudilcz/forge/security/advisories/new),
which lets us discuss and fix the issue before it becomes public.

Please include:

- The type of issue and the affected component
- Steps to reproduce, or a proof-of-concept
- The impact you think an attacker could achieve

FORGE is maintained by one person as a side project, so response times are
best-effort — expect an initial acknowledgement within a week.

## Scope and threat model

FORGE is designed as a **single-user tool bound to localhost**. It is not
hardened for multi-tenant or public-internet deployment. Two properties follow
from that, and are by design rather than vulnerabilities:

- **Agents execute code and shell commands** inside the configured workspace.
  That is the entire point of the tool — treat any whitepaper you feed it with
  the same caution you'd apply to running a script someone sent you.
- **There is no user model or authorisation layer.** The optional HTTP Basic
  auth (`FORGE_AUTH_USER` / `FORGE_AUTH_PASS`) is a thin gate for exposing an
  instance beyond localhost, not a multi-user permission system.

Reports that *are* in scope include: credential leakage (API keys written to
logs, traces, or the deliverables bundle), agent sandbox escapes that write
outside the configured workspace, authentication bypass when Basic auth is
enabled, and injection paths reachable from the Control Station API.

## Handling credentials

Provider API keys are read from the environment or entered in Settings and
persisted to the project's local SQLite database. That database is covered by
`.gitignore` (`*.db`, `.forge/`) — if you relocate your workspace, make sure it
still isn't committed. Never put real keys in `.env.example`.
