"""Read-only presentation of supported Manager configuration parameters."""


def settings_sections(config):
    def row(key, label, value, description, kind="text"):
        services = "Manager and Controller" if key.startswith(("tls.", "newers.")) else "Manager"
        description = f"{description} Restart the {services} after changes."
        return dict(key=key, label=label, value=value, description=description, kind=kind)

    tls = config["tls"]
    signing = config["self-signed"]
    return [
        dict(title="Access", description="Manager authentication and session inactivity.", rows=[
            row("access.login", "Require sign in", "Enabled" if config.get("access", {}).get("login", False) else "Disabled", "Credentials are configured through environment variables."),
            row("access.session", "Session inactivity timeout", str(config.get("access", {}).get("session", 3600)) + " seconds", "User activity renews the session; background polling does not.")]),
        dict(title="Certificate expiration", column=1, description="Applies to inventory, dashboard, CA and self-signed certificates.", rows=[
            row("tls.expiration.warning_days", "Warning threshold", str(tls["expiration"]["warning_days"]) + " days", "Warning when remaining whole days are at or below this limit, above the critical threshold."),
            row("tls.expiration.critical_days", "Critical threshold", str(tls["expiration"]["critical_days"]) + " days", "Critical when remaining whole days are at or below this limit. Expired certificates always take precedence.")]),
        dict(title="Time Converter", column=1, description="Time zones available to the converter.", rows=[
            row("time.timezones", "Time zones", config["time"]["timezones"], "Available IANA values and their display labels. Add or change entries in config.yaml.", "list")]),
        dict(title="Interface", description="Appearance of the Manager.", rows=[
            row("logo.animate", "Logo animation", "Enabled" if config.get("logo", {}).get("animate", True) else "Disabled", "Animate the logo in the navigation menu.")]),
        dict(title="Controller", description="Connection used by the menu health indicator.", rows=[
            row("controller.url", "Service URL", config.get("controller", {}).get("url") or "Not configured", "Base address used by the Manager to check controller availability."),
            row("controller.ca_file", "Controller CA file", config.get("controller", {}).get("ca_file") or "System trust store", "Optional PEM CA bundle for HTTPS requests to the Controller; the path is inside the container.")]),
        dict(title="Recent inventory", column=0, description="Monitoring window for the Controller public API.", rows=[
            row("newers.days", "Recent records window", str(config.get("newers", {}).get("days", 0)) + " days", "Rolling window for /v1/public/newers, Dashboard totals and highlighted inventory rows. Set to 0 to disable.")]),
        dict(title="Self-signed certificates", column=0, description="Defaults used when issuing new certificates.", rows=[
            row("self-signed.key_algorithm", "Key algorithm", signing["key_algorithm"], "Algorithm used to generate the private key. RSA is the only supported option."),
            row("self-signed.rsa_key_size", "RSA key size", str(signing["rsa_key_size"]) + " bits", "Size of newly generated RSA keys. Available options: 2048, 3072 or 4096 bits."),
            row("self-signed.signature_hash", "Signature hash", signing["signature_hash"], "Hash algorithm used to sign newly issued certificates. Available options: SHA256, SHA384 or SHA512.")]),
        dict(title="Passwords", column=0, description="Optional shared password records.", rows=[
            row("password.manager", "Manage passwords", "Enabled" if config.get("password", {}).get("manager", False) else "Disabled", "Enable encrypted password records and the Data table.")]),
        dict(title="TLS versions", column=1, description="Protocol classification, independent of certificate expiration.", rows=[
            row("tls.versions." + version, version, status, "Classification for this negotiated protocol version.", "status")
            for version, status in tls["versions"].items()
        ] + [row("tls.default", "Unknown version fallback", tls["default"], "Classification when a reported version is absent from this list. Missing protocol information is always unknown.", "status")]),
    ]
