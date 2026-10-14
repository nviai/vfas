# Security Policy

VFAS stores companies' books, payroll and tax data, so every vulnerability report is treated as a high priority.

## Supported versions

| Version | Security fixes |
|---|---|
| 2.x (main branch) | ✓ |
| 1.x (cash book) | ✗ |

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion.**

1. Open a private report at <https://github.com/nviai/vfas/security/advisories/new> (repository **Security** tab →
   **Report a vulnerability**). Only the maintainers can see it.
2. Include: version/commit, setup (SQLite or PostgreSQL, SaaS or self-host), steps to reproduce, and impact (access to
   another company's data, privilege escalation, tampering with posted vouchers…). A minimal proof of concept helps.
3. Test only on your own installation or a demo account; **never access real data of other businesses**.

Reports may be written in English or Vietnamese. What the maintainers commit to:

| Step | Target |
|---|---|
| Acknowledge the report | 3 business days |
| Assess severity and reply with a plan | 7 days |
| Release a fix | Critical/High: 14 days · Medium: 30 days · Low: next release |

We publish an advisory once a fix is available and credit the reporter (if you agree). Good-faith research that
follows this policy will not be pursued legally.

## Scope

In scope: the code in this repository and the bundled Docker/Compose configuration. Of particular interest:
- reading or modifying another company's data on the same server;
- bypassing role-based access (e.g. a cashier seeing payroll, a viewer posting vouchers);
- editing or deleting posted vouchers or the audit log;
- session hijacking, CSRF, XSS, SSRF through the AI assistant settings, arbitrary file reads through Excel/XML import.

Out of scope: problems caused by configurations that contradict the [security guide](docs/SECURITY-GUIDE.md) (e.g.
exposing port 8000 to the Internet without HTTPS), volumetric denial of service, vulnerabilities of third-party AI
providers.

## Related documents

[docs/SECURITY-GUIDE.md](docs/SECURITY-GUIDE.md): security model, the latest review, cloud and self-host deployment
checklists, incident response.
