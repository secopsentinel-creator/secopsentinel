#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== sockets =="
ss -tulpn || true

echo "== ufw =="
ufw status verbose || true

echo "== nft =="
nft list ruleset 2>/dev/null || true

echo "== iptables =="
iptables -S 2>/dev/null || true

echo "== luks =="
cryptsetup status secopsentinel-data 2>/dev/null || echo "LUKS no activo"

echo "== clamav =="
clamscan --version 2>/dev/null || echo "ClamAV no instalado"

echo "== git =="
git status --short
