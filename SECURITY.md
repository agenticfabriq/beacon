# Security policy

## Reporting a vulnerability

**Use GitHub's private reporting: [Report a vulnerability](https://github.com/agenticfabriq/beacon/security/advisories/new).** It is enabled on this repository, the report is visible only to the maintainers, and it gives us a private thread to work in until there is a fix.

Please do not open a public issue for a security report. If you have already opened one, that is fine — say so in the private report and we will take it from there.

We aim to acknowledge a report within three business days.

## A wrong number is a security report here

Most projects' threat models stop at code execution and data exposure. Beacon's cannot, because beacon exists to be trusted about numbers: it grades other systems' answers, and those grades get published. A defect that makes beacon report the wrong figure damages exactly what it is for, and it is the failure an external reader is most likely to find first — checking a published number against the artifacts is the whole point of the thing being open.

So the following are in scope, and we would rather hear them privately first:

- A suite that scores incorrectly — an answer graded PASS that is wrong, or FAIL that is right.
- A tolerance or comparison rule that admits an answer it should reject, or vice versa. The conformance contract in `tests/conformance/` is where these rules are pinned; a case it gets wrong is a report.
- A grading path that can be influenced by the system under test. Beacon executes nothing to grade and compares pushed output against gold it already holds, so anything that lets a submission affect its own verdict is a serious finding.
- A published figure that cannot be reproduced from the run records beacon holds.

Alongside the ordinary ones: tenant isolation (one team reading or writing another's runs, suites or results), authentication and credential handling, privilege escalation between roles, and anything that lets a caller act as another user.

## What helps a report land

We are more interested in a reproduction than a severity rating. What we can act on fastest:

- The smallest input that shows it — for a grading defect, the gold and the pushed output, which is usually a few rows.
- What you expected, what happened, and which of the two you are more sure about.
- Whether you are describing something you ran or something you read. Both are welcome and we treat them differently.

If you are unsure whether something is a security issue or just a bug, report it privately and let us decide. We would rather triage a bug than miss a vulnerability.

## Scope

This repository, and the deployed instance at `beacon.agenticfabriq.com`.

Please do not run scans, load tests, or automated exploitation against the deployed instance — it is small, it is shared, and a denial of service there is not a finding we need demonstrated. If you need to prove something against a live system, say so in the report and we will arrange it.

## Supported versions

Beacon has no releases yet. The default branch is what is deployed and what we fix.
