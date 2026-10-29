# Public monitoring API

The Controller exposes anonymous, read-only monitoring endpoints under
`/v1/public`. All counts are JSON integers, with zero for empty datasets.
The Manager UI and scanner/private endpoints keep their existing contracts.

## Endpoints

| GET endpoint | Result |
| --- | --- |
| `/v1/public/newers` | Newly observed assets, targets, ports, certificates, DNS and PTR within the configured days, with counts and pages per group |
| `/v1/public/summary` | All Dashboard counters, Management totals, full distributions and DNS-zone record counts, from one repeatable-read snapshot |
| `/v1/public/management` | Totals for each Management resource |
| `/v1/public/distributions/operating-systems` | Every OS and its asset count, including unused catalog entries and unassigned assets |
| `/v1/public/distributions/types` | Every type and its asset count, including unused catalog entries and unassigned assets |
| `/v1/public/distributions/ports` | Every discovered port/protocol and its quantity; no top-10 limit |
| `/v1/public/dns/ptr` | Latest reverse observations, name comparison and candidates for stale PTRs, including IPs outside the Asset inventory |
| `/v1/public/dns/zones/counts` | Each zone with total records and counts for A, AAAA and CNAME, including empty zones |
| `/v1/public/certificates` | Minimal certificate information, asset/target context and expiration status |
| `/v1/public/attention` | Expired, critical and warning certificates, with the same minimal fields as `/certificates` |
| `/v1/public/web-checks` | Minimal target summary for targets with collected Web Checks |
