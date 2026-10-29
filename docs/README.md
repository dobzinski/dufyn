# Documentation

Start with the [project overview](../README.md) and [installation guide](INSTALL.md).

## Components

| Component | Data access | Interface |
| --- | --- | --- |
| Manager | Reads/writes PostgreSQL for administration and inventory | HTML/static UI and `/api/...` |
| Controller | Reads/writes PostgreSQL for scanner claims/results; reads for public monitoring | `/v1/private/...` and `/v1/public/...` |
| Scanner | No direct PostgreSQL access | Authenticated outbound HTTP to Controller |
| PostgreSQL | Persistent inventory, tasks, topology and PKI | Internal deployment network |

## Guides

- [Installation](INSTALL.md): Docker setup and scanner enrollment.
- [Scanner execution](SCANS.md): scan selection, execution modes and collection behavior.
- [DNS inventory](DNS.md): forward-zone imports and record persistence.
- [Public monitoring API](API.md): available read-only endpoints.
- [HTTPS and private CA](HTTPS.md): Controller API trust for Manager and Scanner.
