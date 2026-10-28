# Security policy

The supported release line is v1.0.x on Windows x64. This application is intended for a trusted office LAN, not direct Internet exposure. Self-registration is open to LAN users after local administrator setup. All signed-in members share the project library; there is no per-project tenancy or membership isolation. Default HTTP traffic is not encrypted.

Do not disable host/origin checks, CSRF protection, private-network restrictions or role checks. Keep model settings restricted to administrators on server localhost when using HTTP. Firewall setup is optional and limited to Private/Domain and LocalSubnet. Use certificates supplied by your organization when HTTPS is required.

Treat uploaded Guide, copy and image content as untrusted model input. Model findings are suggestions and never authorize publication or execute tools. Do not expose another service's secret in prompts or reports. Raster review cannot certify font licensing or guarantee factual accuracy.

Report reproducible issues with synthetic samples. Never attach API keys, account databases, customer material, full workspaces, certificate private keys or unredacted logs. For a suspected security issue, use the repository's private vulnerability reporting facility if enabled; otherwise request a private contact without disclosing exploit details or secrets in a public issue.
