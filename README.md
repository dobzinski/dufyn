# DUFYN

DUFYN stands for “Did yoU Find Your Network?”

## Network Discovery

DUFYN discovers network assets and services, monitors certificates and DNS,
and maps infrastructure across sites. The Manager provides the web interface;
the Controller coordinates authenticated scanners and exposes a read-only public
monitoring API. PostgreSQL stores inventory, topology images and PKI records.

## Features

- ICMP discovery on enabled networks assigned to each scanner, plus manual assets.
- TCP and UDP service inventory for assets and targets, with Nmap UDP support and TLS/QUIC certificate collection.
- Target reachability, latency and HTTP/HTTPS/HTTP3 Web Checks.
- Certificate expiration monitoring using the configured warning/critical windows.
- DNS A/AAAA/CNAME zone imports and network-wide PTR checks, including silent IPs.
- PTR attention for **Undiscovered**, **Inactive** and **Mismatch** records.
- Dashboard charts, Newers, Top 10 ports, Top 5 operating systems and attention tables.
- Physical image maps, logical hierarchies, diagrams and Site Links.
- Tools for calculating subnets, convert times and Base64, format JSON/YAML, inspect certificates, manage private certificates and generate passwords.
- Asset Types and Operating Systems catalogs.
- Public summary, complete distributions, minimal certificate/Web Check lists and recent inventory.

## Get started

Follow [INSTALL.md](docs/INSTALL.md) for Docker Compose or Kubernetes installation,
credentials, scanner enrollment, database backups and updates.
Docker deployment uses one private `deploy/docker/.env` for database credentials
and the persistent PKI key. Publish `.env.example`, never the real `.env`.

| Component | Container address | Responsibility |
| --- | --- | --- |
| Manager | http://localhost:8080 | Inventory, administration, dashboard and topology |
| Controller | http://localhost:8081 | Scanner API and public monitoring JSON |
| Scanner | No published port | Collection of distributed scans from assigned networks and site targets |
| PostgreSQL | Internal network only | Persistent inventory and configuration data |

## Container images

The explicit application images are `dufyn-manager:latest`,
`dufyn-controller:latest`, `dufyn-scanner:latest`, and `dufyn-database:latest`.
The database image extends PostgreSQL.
Docker service names remain `manager`, `controller`, `scanner`, and `postgres`.
Compose volume/project identifiers are retained to keep the current database.
Kubernetes uses namespace `dufyn`.

## Documentation

The [documentation index](docs/README.md) links all guides. Detailed Markdown
lives in `docs/`; deployment manifests and SQL remain in their component folders.

## Repository

```text
certs/        CA file for external HTTPS
controller/   Controller application
database/     Fresh schema, restoration and updates
deploy/       Docker Compose and Kubernetes manifests
docs/         User and technical guides
manager/      Manager application
page/         Project page
scanner/      Scanner application
```
