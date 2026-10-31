# Troubleshooting

## HTTP 403: Invalid request origin

When submitting the Manager login form or another POST request behind a
reverse proxy, the application may return:

```json
{"detail":"Invalid request origin"}
```

The Manager validates request origins to protect against cross-site request
forgery (CSRF). If the public URL differs from the URL reconstructed by the
application, the request may be rejected.

### Configure the Manager's public origin

Use a Manager image that supports `MANAGER_PUBLIC_ORIGIN` and add the
following variable to its existing `env` list in `applications.yaml`:

```yaml
- name: MANAGER_PUBLIC_ORIGIN
  value: "https://dufyn.example.test"
```

Replace the example with the exact browser-facing origin, including a
non-default port when applicable. Do not include a URL path.

The Controller does not use this variable.

### Configure trusted proxies

`FORWARDED_ALLOW_IPS` defines which connecting peers may supply trusted
proxy headers, such as `X-Forwarded-Proto` and `X-Forwarded-For`.

It does not control network access or define the allowed browser origin.

Avoid `*`, which trusts forwarded headers from any client that can reach
the application.

For clusters using Calico, inspect the configured Pod IP pools:

```shell
kubectl get ippools.crd.projectcalico.org \
  -o custom-columns='NAME:.metadata.name,CIDR:.spec.cidr'
```

Check the Ingress Controller's Pod IP and whether it uses host networking.
Use trusted proxy addresses or CIDRs that match the connection sources
actually received by the application.

For example, if the Ingress Controller connects from the Pod pool
`10.42.0.0/16`, add the following to the existing `env` lists of both
Manager and Controller:

```yaml
- name: FORWARDED_ALLOW_IPS
  value: "127.0.0.1,10.42.0.0/16"
```

This CIDR is an example, not a universal Kubernetes default. The IP pool
command above is specific to Calico.

Trusting the entire Pod pool also trusts other Pods in that pool.
Combine this configuration with NetworkPolicies that restrict application
access to the required callers. Preserve Manager and scanner access to
the Controller.

### Apply and verify

Commit the manifest changes to the Fleet repository and wait for
reconciliation and the Deployment rollout.

Verify the Manager's effective configuration:

```shell
kubectl -n dufyn exec deployment/dufyn-manager -- \
  printenv MANAGER_PUBLIC_ORIGIN FORWARDED_ALLOW_IPS
```

Test login directly through the configured Manager hostname.

If the error persists, compare the request's `Origin`, `Sec-Fetch-Site`,
and the URL perceived by the application. Do not disable CSRF validation
to bypass the error.
