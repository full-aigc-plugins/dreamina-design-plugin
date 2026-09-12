# Production closing path

The earlier offline disjunction (`blocked` or `NOT_RUN`) is sufficient only for
the historical feature plan. Production acceptance uses the stricter path:

1. receive a trusted action-time approval event from Codex UI/MCP;
2. run one minimum-cost canary through the guarded submit path;
3. persist the pre-submit intent and returned submit ID;
4. query to a terminal state without resubmission;
5. download through `query_result --download_dir` into an approved root;
6. verify local media metadata and SHA-256;
7. pass the final security review;
8. publish, reinstall from the public marketplace, and verify remote SHA.

Until all eight steps are complete, the plugin remains in production
hardening rather than production-ready status.
