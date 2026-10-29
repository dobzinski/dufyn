"""Administrative CLI; run inside the API container, never expose as a public route."""
import argparse
import secrets
from app.database import get_connection
from app.main import token_digest


def main():
    parser = argparse.ArgumentParser(description="Issue/rotate a scanner token and optionally assign networks")
    parser.add_argument("scanner_id", type=int)
    parser.add_argument("--network-id", action="append", type=int, default=[])
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    with get_connection(read_only=False) as connection:
        scanner = connection.execute("SELECT id, site_id FROM scanner WHERE id = %s FOR UPDATE", (args.scanner_id,)).fetchone()
        if scanner is None:
            parser.error("Scanner not found")
        for network_id in args.network_id:
            network = connection.execute("SELECT id FROM network WHERE id = %s AND site_id = %s", (network_id, scanner["site_id"])).fetchone()
            if network is None:
                parser.error("Network does not belong to the scanner site")
            connection.execute("INSERT INTO scanner_network (scanner_id, network_id, site_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (args.scanner_id, network_id, scanner["site_id"]))
        connection.execute("UPDATE scanner SET token_hash = %s WHERE id = %s", (token_digest(token), args.scanner_id))
    print(token)


if __name__ == "__main__":
    main()
