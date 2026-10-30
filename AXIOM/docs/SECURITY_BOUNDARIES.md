# Security and operational boundaries

This is a local synthetic development application. Default API authentication denies access; explicit DARLA_SYNTHETIC_MODE=1 admits documented fixture sessions. It is not a production identity mechanism. Bind only 127.0.0.1. The UI must never imply that choosing a synthetic context establishes real Dani identity.

No actual credentials, env files, customer records or external private stores are inspected. The process Nebius key is left for Rayzer. Runtime/private-data transmission, externally accessible services, publication and deployment remain separately governed.

Model-provided principals, permissions, tool names, approval references and completion claims are untrusted. The gateway compares to the server-authenticated principal, validates memory references and tools, checks budgets/deadlines and queues permitted internal work. An action proposal is not an execution receipt. Runtime inputs admit synthetic data only until transport, identity, consent and privacy controls are reviewed.

Memory correction/deletion removes current application values and does not retain them in replay responses or receipts. SQLite physical-page recovery, old filesystem backups and operating-system media sanitization are outside this API's deletion guarantee. No private source packet belongs in a public commit. User data ownership does not imply permission to transmit data to a model provider.

No consequential external tool is enabled. No customer contact, customer order commitment, payment/refund, purchase, price change, publishing or credential/ACL change can be authorized by fixture mode. Fixture approval decisions remain execution_authorized=false. Real approval integration must bind immutable action parameters, verified human identity, expiry, revocation and single-use consumption.

Path validation resolves every state target and rejects traversal/symlink escapes. Do not change ancestor reparse points while the app runs. This is application confinement, not OS-level hostile-process isolation; Rayzer supplies runtime sandbox evidence.