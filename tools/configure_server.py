"""Create an explicit office-only configuration, without touching existing files."""
import argparse
import ipaddress
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="Real office IPv4, not a VPN/virtual adapter")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        address = ipaddress.IPv4Address(args.ip)
    except ipaddress.AddressValueError as exc:
        parser.error(str(exc))
    networks = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if not any(address in network for network in networks):
        parser.error("Use an RFC1918 office IPv4 address.")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    target = Path(__file__).resolve().parents[1] / "ServerConfig.json"
    config = {"bind_host": "0.0.0.0", "port": args.port,
              "allowed_hosts": [f"{address}:{args.port}", f"127.0.0.1:{args.port}", f"localhost:{args.port}"]}
    try:
        with target.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError:
        parser.error("ServerConfig.json already exists; preserve it and review changes manually.")
    print(f"Office URL: http://{address}:{args.port}/")
    print("No firewall or startup task has been changed. Run StartServer.cmd next.")


if __name__ == "__main__":
    main()
