# Security policy

## Reporting a vulnerability

Please report it privately: open this repository's **Security** tab and choose
**Report a vulnerability**. That opens an advisory only the maintainers can
see. Please do not open a public issue for a security problem.

Include the version, the skill file or code that shows the problem (as small
as you can make it), and what happened. You will get an answer within a few
days. A confirmed issue is fixed in a patch release, and the advisory is
published with it, crediting you unless you ask not to be named.

## Supported versions

| Version | Supported |
|---|---|
| 0.3.x | yes |
| earlier | no (never published) |

## What counts

The promises this project makes are security promises, so breaking one is a
vulnerability, not just a bug:

- a tool that changes something runs without the `confirm:` above it, runs
  twice in one flow run, or runs with inputs other than the ones the user
  approved;
- a value the model supplied is accepted for an answer only the user may give:
  a code, a `type: secret` field, a pick from a list, a confirmation;
- a `by:` approval arrives through the conversation instead of the approvals
  store;
- the loader accepts a skill file it should refuse, such as a write with no
  `confirm:` above it;
- a secret appears in a log or in the audit trail;
- with the default settings, an endpoint that writes a skill or a widget, or
  returns a skill's source, can be reached from another host.

## What does not

- The chat endpoints have no authentication of their own. That is documented:
  put your own in front before exposing them (see the README's Security
  section).
- `by: manager` says who is asked, not who answered. Checking identity is
  yours to add.
- The model misreading the user. The design makes a misread cost a turn, not a
  write; if one ever causes a write, that is in scope.
- The sample data and the mock bookings and payments in `examples/`.
