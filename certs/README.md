# Controller CA trust

Place the public PEM CA certificate/bundle in `certs/ca.crt` on the host.
Compose mounts this directory read-only at `/opt/dufyn/certs` in Manager/Scanner.
In both config.yaml files set controller.url to the HTTPS API endpoint and
controller.ca_file to `/opt/dufyn/certs/ca.crt`. Leave ca_file null for system trust.
Only the public CA is needed here; never distribute its private key to clients.

This does not enable HTTPS on Controller or configure a reverse proxy/Ingress.
The server certificate SAN must match the hostname used in controller.url.
Restart/recreate clients after replacing the CA bundle. Existing HTTP remains valid.
See [HTTPS configuration](../docs/HTTPS.md) for Docker and Kubernetes instructions.
