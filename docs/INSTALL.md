# INSTALL

## Docker

1. Install and start Docker on openSUSE
```sh
zypper install docker docker-compose
systemctl enable --now docker
```

2. Prepare configuration

Clone the repository into `/opt/dufyn` and create the local environment file.
Run these commands as an administrator. On systems other than openSUSE,
install Docker and its Compose plugin using that platform's instructions.

```sh
git clone https://github.com/dobzinski/dufyn.git /opt/dufyn
cd /opt/dufyn
cp deploy/docker/.env.example deploy/docker/.env
```

You can use openssl to generate the `PKI_MASTER_KEY` password.
```sh
openssl rand -base64 32
```

And, for Postgres, generate the `POSTGRES_PASSWORD` and `POSTGRES_APP_PASSWORD` passwords.
```sh
openssl rand -hex 24
```

Edit `deploy/docker/.env`: replace the passwords placeholders.
Keep this key unchanged and backed up;
it protects stored private keys and passwords. Never publish the real `.env`.
Do not overwrite an existing installation's `.env` or encryption key.

By default, ports 8080 and 8081 listen on `127.0.0.1`: access is limited to the
Docker host or a host reverse proxy. For direct remote access, set the corresponding
`MANAGER_BIND_ADDRESS` or `CONTROLLER_BIND_ADDRESS` to the host's interface IP,
or `0.0.0.0` for all IPv4 interfaces. `FORWARDED_ALLOW_IPS` controls trusted proxy
headers, not port access.

Review `manager/config.yaml` and `scanner/config.yaml`. To require Manager login,
set `access.login: true` and configure `MANAGER_LOGIN_USER` and
`MANAGER_LOGIN_PASSWORD` in `.env`.

3. Pull and run images
```sh
cd /opt/dufyn
docker compose -f deploy/docker/compose.yaml --profile scanner config --quiet
docker compose -f deploy/docker/compose.yaml --profile scanner pull
docker compose -f deploy/docker/compose.yaml up -d --no-build postgres manager controller
docker compose -f deploy/docker/compose.yaml ps
curl -fsS http://localhost:8080/health
curl -fsS http://localhost:8081/ready
```

4. Access
Open `http://localhost:8080/` on the Docker host, or use its reachable IP if remote access was enabled.

5. Scanner
In Manager, create a Site, an enabled Network and a Scanner. Assign the Network
to the Scanner, then generate a token and set `controller.token` in
`scanner/config.yaml`. Alternatively, set `SCANNER_TOKEN` in `.env`; a nonempty
environment token overrides the YAML value.

Run Scanner for its first execution, then run collection:

```sh
docker compose -f deploy/docker/compose.yaml run --rm scanner
```

Compose mounts `scanner/config.yaml` read-only into the container. Changing its
token or settings requires a new Scanner execution.

The supplied Controller URL works inside this Compose network. A remote Scanner
needs a reachable Controller URL and port access. See [HTTPS](HTTPS.md) for a
private CA, [Scanner execution](SCANS.md) for modes and partial scans,
[DNS inventory](DNS.md) for zone imports, and the [public API](API.md) for monitoring.

### Uninstall using Docker

Uninstall Dufyn: remove containers, images, and volumes.

**WARNING: This also removes the PostgreSQL data volume, permanently deleting all database data.**

```sh
docker compose -f deploy/docker/compose.yaml down -v --rmi all --remove-orphans
```

## Kubernetes

Use the updated Kubernetes manifest bundle. The commands below assume it is in
`deploy/kubernetes/`.

### 1. Prepare the manifests

- In `pvc.yaml`, replace `<your-storage-class>` with your cluster's StorageClass.
  Prefer block storage for PostgreSQL data; avoid NFS for the database data volume.
- In `secrets.yaml`, fill in the database and application credentials, Manager
  credentials and persistent PKI key. Generate the key with `openssl rand -base64 32`.
  Keep the same key when restoring existing encrypted data. Never commit real secrets.
- In `configmaps.yaml`, review Manager and scanner settings. Enable
  `access.login: true` to require Manager authentication.
- Confirm that the image tags in `applications.yaml`, `postgres.yaml` and the
  scanner manifests match the versions you want to deploy.
- Keep `suspend: true` in `scanner-cronjob-all.yaml` until scanner enrollment is complete.

The updated `kustomization.yaml` includes `secrets.yaml`; fill it before applying.

### 2. Configure access

In `kustomization.yaml`, select only one Ingress file: `ingress-nginx.yaml` or
`ingress-traefik.yaml`. The corresponding Ingress Controller must already be installed.
Set the correct `spec.ingressClassName` for your cluster.

Replace `dufyn.example.test` and `dufyn-api.example.test` with your domains and
point their DNS records to the Ingress address. In `secrets.yaml`, provide the
Base64-encoded PEM certificate and private key for `dufyn-tls`; the certificate
must cover both hostnames.

In the Manager's existing `env` list in `applications.yaml`, configure:

```yaml
- name: MANAGER_PUBLIC_ORIGIN
  value: "https://dufyn.example.test"
- name: FORWARDED_ALLOW_IPS
  value: "127.0.0.1,10.42.0.0/16"
```

Use the exact public Manager origin, without a path. `MANAGER_PUBLIC_ORIGIN`
is not needed by the Controller. Add the following to the Controller's `env` list:

```yaml
- name: FORWARDED_ALLOW_IPS
  value: "127.0.0.1,10.42.0.0/16"
```

Replace the example CIDR with the trusted proxy source addresses or networks
actually seen by the applications. This setting trusts forwarded headers; it does
not restrict network access. Avoid `*`. Trusting an entire Pod pool also trusts
other Pods in that pool; use NetworkPolicies to restrict callers as needed.

### 3. Install and verify

```sh
kubectl apply -k deploy/kubernetes/
kubectl -n dufyn get pods,pvc,svc,ingress
```

Open `https://dufyn.example.test`, or your configured Manager hostname.

### 4. Enroll and run the scanner

In Manager, create a Site, Network and Scanner, assign the Network and generate
the scanner token. Set it in the `dufyn-scanner` Secret in `secrets.yaml`, then
apply the manifests again.

To test one complete cycle manually:

```sh
kubectl -n dufyn create job --from=cronjob/dufyn-scanner-all dufyn-scan-test
kubectl -n dufyn logs -f job/dufyn-scan-test -c dufyn-scanner
```

Use a new Job name for each test. Once validated, set `suspend: false` in
`scanner-cronjob-all.yaml` and apply again. Its `0 * * * *` schedule runs hourly.
Keep the certificates, DNS/PTR and endpoints CronJobs suspended for manual use.
Configure DNS server/zones and enable PTR in the scanner ConfigMap when needed.

### 5. Apply configuration changes

After applying changes to the shared Manager ConfigMap, restart both applications:

```sh
kubectl -n dufyn rollout restart deployment/dufyn-manager deployment/dufyn-controller
```

ConfigMap changes do not automatically restart these Pods. Scanner changes apply
to new Jobs.

### 6. Uninstall

Remove the resources defined by the installation manifests:

```sh
kubectl delete -k deploy/kubernetes/
```

**WARNING: This also deletes the `dufyn` namespace, its Secrets and PVCs, and
other resources in that namespace, including manually created Jobs. Persistent
storage may be deleted depending on the volume reclaim policy. Back up the
database and retain its PKI key before uninstalling.**

### Fleet labs

For Rancher Fleet deployment examples and labs, see
[fleet-dufyn](https://gitlab.com/dobzinski/fleet-dufyn/).
