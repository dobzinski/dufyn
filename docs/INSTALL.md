# INSTALL

## Docker

1. Install and start Docker on openSUSE
```sh
zypper install docker docker-compose
systemctl enable --now docker
```

2. Prepare configuration

The examples assume the repository is already downloaded to `/opt/dufyn`.
Run the Docker installation commands as an administrator. On systems other than
openSUSE, install Docker and its Compose plugin using that platform's instructions.

```sh
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

3. Build
```sh
cd /opt/dufyn
docker compose -f deploy/docker/compose.yaml config --quiet
docker compose -f deploy/docker/compose.yaml up -d --build
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

Build the Scanner image for its first execution, then run collection:

```sh
docker compose -f deploy/docker/compose.yaml build scanner
docker compose -f deploy/docker/compose.yaml run --rm scanner
```

Compose mounts `scanner/config.yaml` read-only into the container. Changing its
token or settings requires a new Scanner execution, not an image rebuild.
Recreate a running continuous Scanner to load changed configuration. Rebuild
when application code, dependencies or the image base changes.

The supplied Controller URL works inside this Compose network. A remote Scanner
needs a reachable Controller URL and port access. See [HTTPS](HTTPS.md) for a
private CA, [Scanner execution](SCANS.md) for modes and partial scans,
[DNS inventory](DNS.md) for zone imports, and the [public API](API.md) for monitoring.


# Working here ...
