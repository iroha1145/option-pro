# PR148 finishing review — 2026-09-08

Reviewed baseline: `c5be0860c8a48a35052aea3c2270d8c776304957`.

The basic R1/R2 cases are fixed in that baseline. This follow-up fixes the
remaining publication and retry boundary cases without changing score formulas,
frontend behavior, access control, provider configuration, or the default/recent
variant design.

## Corrections

- Input coverage uses the canonical universe minus the scanner's mutually
  exclusive data-error/insufficient-history counts, not the number of filtered
  or top-N result rows. A legitimate smaller/empty result can publish; identical
  top rows cannot conceal a same-session input outage. Universe changes are not
  compared as the same pool. Missing legacy counters remain unknown.
- Common displayed symbols cannot regress behind their prior known input dates
  simply because another symbol already made the aggregate minimum old.
- Future input in any displayed row cannot certify a snapshot as current or
  replace a good publication. A legacy future input is not a trusted lower
  bound that prevents recovery. Initial unverified snapshots retain the existing
  read-time unknown policy; this change does not claim to validate every vendor
  input or undisplayed symbol independently.
- Replacement baselines are read with the same schema/parameter validation as
  serving, with a bounded file read. Invalid, oversize or future-saved documents
  cannot permanently block a valid refresh. The worker performs this read away
  from the event loop.
- Retry state retains the exact failed parameter set and its absolute deadline.
  Manual work cannot postpone an unrelated pending retry. Successful variants
  are not rescanned during the retry; early wakes neither consume the retry nor
  erase unresolved errors. Matching manual recovery clears its pending entry.
  The limit remains one extra retry per daily cycle, at most four variants.
- Only an idle task with explicit publication evidence counts as a successful
  variant publication. Attempt counts and unresolved pending counts are separate.

## Regression evidence

`tests/test_strength_final_review.py` imports the production writers, policy and
worker class. It contains 24 cases. The initial 16-case repro suite failed 13
cases on the unmodified baseline, with three passing controls. Those assertions
were not weakened; eight further checks were added afterward.

The deduplicated focused suite (25 files: strength, screener and worker tests)
passed **266 tests** locally, including all 24 new cases. `git diff --check` passed.
The local interpreter is Python 3.13 with offline test dependencies, not the
production lock. Exact Python 3.12.13/locked dependencies and complete frontend,
browser and deployment checks must therefore be reported from the final GitHub
CI run, not inferred from this local result. This file deliberately does not
claim a CI outcome before that run finishes.

The tests use synthetic market input, local temporary files and injected
clocks. No external-provider accuracy or live deployment is claimed. The normal
read-only GET, owner-only refresh, no merge and no deployment boundaries remain.
