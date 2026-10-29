# HTTPS and a private Controller CA

`controller.ca_file` is an optional path to a **public PEM CA certificate/bundle**.
Configure it in both Manager and Scanner when they call a Controller HTTPS
endpoint signed by your private CA. Existing HTTP and public-CA HTTPS remain
supported with ca_file null. Default system roots are retained when adding a CA;
certificate-chain and hostname verification remain enabled.

```yaml
controller:
  url: https://controller.example.test
  ca_file: /opt/dufyn/certs/ca.crt
```

This configures **outbound API trust**, not HTTPS serving. You must first configure
TLS on Controller or a reverse proxy/Ingress. The server certificate SAN must
match the hostname in controller.url. No client certificate is sent (this is not
mTLS). Target certificate discovery keeps its existing collection behavior.

## Docker Compose

1. Put the public CA bundle in `certs/ca.crt`, relative to the repository root
   (for example, `/opt/dufyn/certs/ca.crt` on the host if the repository is there).
2. Set `controller.url` and `controller.ca_file` in `manager/config.yaml` and
   `scanner/config.yaml`. Compose mounts the repository's `certs/` directory
   read-only at `/opt/dufyn/certs` inside both clients. The YAML path refers to
   the file **inside the container**, regardless of the host repository location.
3. From the repository root, recreate Manager to load the changed settings:

```sh
docker compose -f deploy/docker/compose.yaml up -d --force-recreate manager
```

Both YAML configurations and the CA directory are mounted as volumes; no image
rebuild is required for these changes. Start a new Scanner run to load them, or
recreate an existing continuous Scanner. See [Scanner execution](SCANS.md).

Use ca_file null if relying on system trusted CAs. A configured missing/malformed
CA file causes configuration validation to fail; it never silently disables TLS
checks. Copying a CA into certs/ alone does not activate it.

## Kubernetes

Create/update the optional public-CA ConfigMap from the repository root:

```sh
kubectl -n dufyn create configmap dufyn-controller-ca --from-file=ca.crt=certs/ca.crt --dry-run=client -o yaml | kubectl apply -f -
```

In `deploy/kubernetes/configmaps.yaml`, set controller.url and controller.ca_file
in both dufyn-manager-config and dufyn-scanner-config. The supplied Deployment
and CronJob mount the optional CA ConfigMap at `/opt/dufyn/certs` in their
respective clients. No hostPath directory on cluster nodes is needed.

```sh
kubectl apply -k deploy/kubernetes
kubectl -n dufyn rollout restart deployment/dufyn-manager
```

New Scanner Jobs receive updated files/config; recreate an existing Job if needed.
The Controller server/proxy certificate and private key belong in its own TLS
Secret, not this client-trust ConfigMap. Never distribute the CA private key.
The optional CA ConfigMap need not exist for the default HTTP configuration.

## Updates and network boundaries

Restart/recreate clients after updating CA trust; the scanner reuses its verified
context for its process lifetime. When rotating a CA, distribute a bundle trusting
old and new CAs first, update server certificates, then remove the old CA and
restart clients. Keep a separate copy of public trust files when restoring a
configuration backup that references their paths.

HTTPS only to an Ingress does not encrypt its HTTP backend connection. Protect
that additional hop separately when needed; client trust settings alone do not
turn HTTP into HTTPS. Health/readiness and all private Scanner API calls use the
same custom CA; Manager health and PTR monitoring calls do so as well.

See [Installation](INSTALL.md) for the initial Docker setup.
