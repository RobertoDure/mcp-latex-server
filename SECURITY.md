# Security Policy

## Supported versions

Security fixes are applied to the latest released 3.x line.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Use GitHub private
vulnerability reporting for this repository, or contact the maintainers through
the repository security advisory flow.

Include:

- a description of the issue,
- the affected tool or module,
- a minimal reproduction if possible,
- the impact on path containment, command execution, or data integrity.

## Security model

The server assumes the configured LATEX_SERVER_BASE_PATH is a trust boundary.
All file tools resolve paths inside that boundary. Compilation disables shell
escape by default. Users who enable shell_escape or configure template
directories outside the base path accept the associated trust.

The optional external tools are executed as separate processes with timeouts;
they are not sandboxed by this project. Use trusted documents and a constrained
environment when processing untrusted input.
