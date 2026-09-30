# Spoon-Knife-Hashi: inert master-only AI review pilot

Target: https://github.com/comoc/Spoon-Knife-Hashi. User approved master-only pilot, Japanese issues for concrete bugs, and USD100/month OpenAI API budget. Issues enablement was separately completed by the parent on 2026-09-30. This package does not enable or install anything. No paid request, credential creation, or issue publication has been performed.

## What runs

Only ordinary linear pushes to master. Three isolated GitHub-hosted jobs load the same audited immutable script without checking out or executing target code:

1. Reservation: short-lived GITHUB_TOKEN with contents:write, no OpenAI key. Atomically reserves USD1 on ai-review-budget/ledger.json
2. Review: contents:read and environment OpenAI secret. Verifies this run-attempt reservation, reads inert comparison JSON, makes at most one bounded model call, validates findings
3. Publish: contents:read and issues:write, no OpenAI key. Independently revalidates anchors and creates up to three Japanese correctness issues, suppressing fingerprints already present in issue history

The workflow is deliberately named .yml.template and has a placeholder trusted SHA. Keep it inert until approval/setup is complete. AI_REVIEW_ENABLED and AI_PUBLISH_ENABLED must be repository variables, initially false.

## Monthly budget and limits

The durable ledger uses GitHub Contents API blob-SHA compare-and-swap. Missing, malformed, exhausted, conflicting or ambiguously written ledgers fail closed BEFORE any paid request. The script never initializes or resets the ledger. Initialize it once under reviewed setup as {"version":1,"months":{}} on the dedicated ai-review-budget branch. Preserve all prior months and never remove reservations. A new month is created only inside the same atomic update.

Each full run attempt irrevocably consumes one USD1 reservation, including subsequent failures and timeouts. There are at most 100 reservations per UTC calendar month. No refund/reclaim, paid retry, or automatic retry after a ledger write occurs. A full-workflow rerun obtains a fresh reservation; a partial review-job rerun without a matching attempt reservation stops. A reservation may be wasted for an unsupported/empty diff; this intentionally favors safety over budget utilization.

The audited model is gpt-5.4-mini-2026-03-17 using standard service tier, no tools, max_output_tokens=2000 and store=false. Official model page checked 2026-09-30 gives 400,000 context, USD0.75/M input and USD4.50/M output. Even reserving the ENTIRE context rather than guessing tokens gives USD0.309 per request; a 25% safety factor yields USD0.38625, below the USD1 slot. The script rejects another model, smaller input-capacity setting, lower audited rates, nonfinite prices, or expired price approval. The maximum output includes reasoning token usage. Revalidate prices and model availability at activation.

Reservation and paid-call verification both stop during the first and final UTC hour of a month to reduce boundary attribution risk. Slow environment approval cannot move an old-month reservation into a new-month call. This is a conservative pilot API-usage control, not an absolute provider-invoice guarantee: shared-key callers, administrator tampering, provider price changes, taxes, credits and provider settlement attribution are outside it. Use a dedicated project/key solely for this pilot. OpenAI project budget alerts alone are soft limits, not a hard stop. GitHub Actions usage is separate from the OpenAI budget.

## Required protections before activation

- Restrict ai-review-budget, ai-review and ai-review-publish environments to master; keep the OpenAI key ONLY in ai-review
- Protect trusted workflow/script and master changes with appropriate independent review; protect ledger integrity against reset, force push and deletion. Do not allow an untrusted branch workflow to mutate the ledger. Verify the actual rules and any GitHub Actions bypass semantics before calling the ledger trusted
- The budget writer's contents:write is explicit and isolated from model/source execution. It is not technically scoped to one file. This permission/control setup requires approval; no persistent GitHub credential is needed
- Administrators or trusted writers able to replace workflows/erase the ledger can defeat a repository-local cap. If these actors are in threat scope, use an independently controlled billing proxy/ledger; do not claim this prototype meets that threat model
- Pin the audited script commit in all three bootstraps, configure environment secret by secure user handoff, review source-sharing/provider retention scope, then authorize review-only live smoke testing
- Do not enable public issue posting until review-only quality, actual permissions, ledger updates, refusal/error behavior and cost are verified

Suggested repository variables after confirmation: AI_MODEL=gpt-5.4-mini-2026-03-17, MODEL_MAX_INPUT_TOKENS=400000, INPUT_USD_PER_MILLION=0.75, OUTPUT_USD_PER_MILLION=4.50, MAX_USD_PER_RUN=1, PRICING_VALID_UNTIL=2026-10-31. The expiry is a proposed review deadline, not a provider price lock. Enable variables remain false.

## Review scope and safety

HTML is now eligible so this tiny repository's index.html can actually be reviewed. Other allowed programming-language extensions are retained; binaries and README text are excluded. Existing target has only README, forkit.gif, index.html and no run/test instructions. Reviewed source is treated only as untrusted data. No builds, package installation, shell tool execution or target-code execution occurs.

Stops on new/deleted/forced/non-default pushes, unavailable/non-ancestor baseline, over 100 commits, over 30 changed files, over 24KB diff, missing/truncated patches or eligible rename/deletion. Filters generated/vendor/secrets/fixtures paths. At most three P1/P2 high-confidence correctness candidates; public security/privacy findings withheld. Pattern-based secret scanning and output filters are not proof against all secrets/disclosures. Findings are AI candidates without execution verification.

Fingerprint deduplication is approximate; source/root-cause changes may duplicate, closed issues stay suppressed, and an issue author can spoof a marker. Issue history above 2,000 records stops. Queue max retains up to 100 pending runs; overflow cancels. Thus this is neither guaranteed every-push delivery nor an all-branch service. Future all-branch coverage needs trusted event routing rather than granting arbitrary branch YAML access to privileged environments.

## Verification

From the installed repository root:
PYTHONPATH=.github/ai-review python3 -m unittest discover -s .github/ai-review/tests -v
python3 -m py_compile .github/ai-review/review.py

For this standalone package, use python3 -m unittest discover -s tests -v from its root.

40 offline/mock tests pass, including YAML parsing, CAS shape, exhausted/duplicate/malformed/missing ledger, ambiguous write no-retry, month-boundary guard, HTML eligibility and prior safety checks. Final audit added delayed approval, UTC rollover, partial rerun and malformed reviewer-ledger checks. No live model, live ledger CAS, permissions/rules, actionlint or real publication test has run.

Sources:
- https://developers.openai.com/api/docs/models/gpt-5.4-mini
- https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents
- https://help.openai.com/en/articles/9186755-managing-your-work-in-the-api-platform-with-projects
- https://docs.github.com/en/actions/reference/security/secure-use
