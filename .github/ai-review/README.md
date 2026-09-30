# Spoon-Knife-Hashi: master-only AI review

Reviews bounded, eligible master-push diffs as untrusted data and creates up to three Japanese P1/P2 correctness Issue candidates. It never checks out or executes pushed source. Existing deployment was verified with two real zero-finding reviews on 2026-09-30; no synthetic public bug Issues were created.

The durable queue correction is described in [QUEUE.md](QUEUE.md). It preserves received events through month-boundary deferral, disabled gates, budget exhaustion and pricing expiry. Hourly minute-17 scheduling, new pushes and manual dispatch can drain pending events. Ambiguous paid calls and publication outcomes never trigger an automatic paid retry.

## Budget

Dedicated model: gpt-5.4-mini-2026-03-17, standard tier, no model tools, max_output_tokens=2000, store=false. Audited 400,000-token full-context bound with prices USD0.75/M input and USD4.50/M output yields USD0.309; adding 25% margin yields USD0.38625. Each attempt irrevocably reserves USD1 BEFORE payment, at most 100 per UTC month. Actual spending can be much lower; no automatic refund is made.

The durable GitHub ledger uses compare-and-swap updates and preserves history. Missing, malformed, conflicting or uncertain writes fail closed. This cap covers the unchanged pilot using a dedicated key under trusted repository owner/writers, not other API usage, taxes, price changes, or deliberate administrator tampering. OpenAI budget alerts alone are not a hard stop. GitHub Actions usage is separate.

Pricing approval currently expires after 2026-10-31 UTC. The queue retains pending events afterward, but paid processing requires price review and an updated approval date. Final and first UTC hours of each month defer work rather than discard it.

## Configuration

Repository variables:
- AI_REVIEW_ENABLED and AI_PUBLISH_ENABLED: explicit true/false gates
- AI_MODEL=gpt-5.4-mini-2026-03-17
- MODEL_MAX_INPUT_TOKENS=400000
- INPUT_USD_PER_MILLION=0.75
- OUTPUT_USD_PER_MILLION=4.50
- MAX_USD_PER_RUN=1
- PRICING_VALID_UNTIL=2026-10-31

Environments ai-review-budget, ai-review and ai-review-publish permit only the selected branch master. OPENAI_API_KEY belongs only in ai-review, entered securely by the user. Coordinator/finalizer use contents:write with no model key; reviewer uses contents:read with the key; publisher uses contents:read and issues:write without the key. All load both trusted scripts from one immutable audited commit. Do not enable the old and queued workflows simultaneously or reset the live ledger.

## Coverage limits

Ordinary linear master pushes only; no forced/new/deleted pushes. Maximum 100 commits, 30 changed files and 24 KB eligible diff. Missing/truncated patches and eligible rename/deletion require attention. Supported programming-source extensions include HTML; binary, README, vendor/generated/secrets/fixtures and .github control-plane files are excluded. No style/refactor/test-coverage findings or public security/privacy findings.

Findings are AI candidates, not execution-verified facts. Secret detection and output filters cannot guarantee every disclosure is caught. Fingerprint deduplication is approximate, and issue-author spoofing can suppress a marker. Histories above 2,000 issue/PR records stop publication. A 3,000-record durable queue fails visibly when full. GitHub trigger loss/concurrency overflow is not a guaranteed-delivery service; preserved pending entries survive delayed scheduling.

## Verification

From repository root:
PYTHONPATH=.github/ai-review python3 -m unittest discover -s .github/ai-review/tests -v
python3 -m py_compile .github/ai-review/review.py .github/ai-review/queue_worker.py

Standalone package: python3 -m unittest discover -s tests -v

63 offline tests passed before queue deployment, including eight independent audit cases. YAML/bootstrap parsing and Python compilation passed. Month-boundary clock transitions are mocked; live tests verify actual GitHub queue/ledger/model/publisher integration without creating synthetic bug Issues.

Sources:
- https://developers.openai.com/api/docs/models/gpt-5.4-mini
- https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents
- https://docs.github.com/en/actions/how-tos/troubleshoot-workflows
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows
