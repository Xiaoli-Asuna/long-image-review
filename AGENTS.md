# Repository instructions for AI assistants

Read README.md, docs/DEPLOYMENT.md, docs/PRIVACY.md and SECURITY.md before modifying or deploying this project. DEPLOY_WITH_AI.md contains the intended deployment workflow.

- Supported release target: Windows x64. API mode needs no GPU. Keep standalone loopback mode and authenticated office LAN mode working.
- Preserve the separation between long-lived project Guide/rules and per-article copy/requirements/designs. Historical reports retain their input snapshots.
- Preserve Data, ServerData, ServerConfig.json and backups. Do not merge unrelated workspaces or overwrite user data. Stop and back up a workspace before upgrading it. Never commit data, credentials, customer assets, account databases, logs or private machine paths.
- Credentials must be entered by the human in the intended local UI. Do not inspect credential stores or put API keys into commands, tests, issue reports or chat. DPAPI persistence belongs to the current Windows user.
- Uploading/parsing Guide and running reviews may incur provider charges. Use offline fixtures for development. Never automatically retry a failed paid request, switch to another paid model, or run real inference without explicit authorization.
- Office mode is for a trusted LAN, with self-registration for director/operator/designer. Do not expose it to the public Internet, disable firewall checks, add wildcard hosts, or grant admin through registration. HTTP API settings stay server-local; HTTPS needs valid deployment certificates.
- Explain that all signed-in members share projects; there is no project membership isolation. AI findings are preliminary and require human review.
- Windows startup task runs after user login, not before login. Do not claim a pre-login service exists. Do not change firewall or startup scheduling unless deployment scope calls for it.
- Verify edits using `python -m unittest discover -s tests -v` and `python tools/check_release.py` from the root, with dependencies installed in .venv or the portable runtime. Tests use fictional material and local HTTP/SSE fixtures only. Keep test output in ignored TestResults.
- Build releases with tools/build_portable.py and its hash-locked official downloads. Review staged files and archive contents before any authorized publication. Never infer publication permission from a routine code change.
- Distinguish source tests, real portable launch checks, LAN access from another machine and real model quality. Do not report one as proof of another.
