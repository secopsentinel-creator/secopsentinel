#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Ejecutar con sudo: sudo $0"
  exit 1
fi

TARGET_USER="${SUDO_USER:-maudaemon}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
LUKS_MOUNT="$TARGET_HOME/.local/share/secopsentinel"
SOURCE="/opt/lampp/var/mysql"
DEST="$LUKS_MOUNT/mysql"
OLD="$SOURCE.pre-luks"
SOCKET="$SOURCE/mysql.sock"
APP_DIR="/opt/lampp/htdocs/secopsentinel"

switched=0
restored=0

restore_origin() {
  if (( restored == 1 )); then
    return 0
  fi
  /opt/lampp/lampp stopmysql || true
  for _ in {1..30}; do
    pgrep -u mysql mysqld >/dev/null || break
    sleep 1
  done
  if pgrep -u mysql mysqld >/dev/null; then
    echo "No se pudo detener MariaDB; no se restaura el datadir." >&2
    return 1
  fi
  if [[ -L "$SOURCE" ]]; then
    rm -f "$SOURCE"
  fi
  if [[ -d "$OLD" ]] && [[ ! -d "$SOURCE" ]]; then
    mv "$OLD" "$SOURCE"
  fi
  /opt/lampp/lampp startmysql || true
  restored=1
}

cleanup() {
  if (( switched == 0 )); then
    restore_origin
  fi
}

if [[ -L "$SOURCE" ]]; then
  echo "MariaDB ya usa un enlace en $SOURCE."
  exit 1
fi
if [[ ! -d "$SOURCE" ]]; then
  echo "No existe el datadir actual: $SOURCE"
  exit 1
fi
if [[ -e "$OLD" ]]; then
  echo "Ya existe $OLD; resuélvelo antes de continuar."
  exit 1
fi
if ! mountpoint -q "$LUKS_MOUNT"; then
  echo "El volumen LUKS no está montado en $LUKS_MOUNT."
  exit 1
fi
if [[ -d "$DEST" ]] && [[ -n "$(ls -A "$DEST" 2>/dev/null)" ]]; then
  echo "El destino ya existe y no está vacío: $DEST"
  exit 1
fi
command -v rsync >/dev/null || { echo "Falta rsync."; exit 1; }
command -v setfacl >/dev/null || { echo "Falta setfacl."; exit 1; }

SOURCE_KB="$(du -sk "$SOURCE" | awk '{print $1}')"
AVAIL_KB="$(df -Pk "$LUKS_MOUNT" | awk 'NR==2 {print $4}')"
if (( AVAIL_KB < SOURCE_KB + 1024 * 1024 )); then
  echo "Espacio insuficiente en el volumen LUKS."
  exit 1
fi

if pgrep -u "$TARGET_USER" -f 'uvicorn app.main:app' >/dev/null; then
  read -r -p "La aplicación está corriendo. ¿Detenerla ahora? [s/N] " stop_app
  [[ "$stop_app" =~ ^[sS]$ ]] || exit 1
  pkill -u "$TARGET_USER" -f 'uvicorn app.main:app' || true
  sleep 1
fi

trap cleanup EXIT

/opt/lampp/lampp stopmysql || true
for _ in {1..30}; do
  pgrep -u mysql mysqld >/dev/null || break
  sleep 1
done
if pgrep -u mysql mysqld >/dev/null; then
  echo "MariaDB no se detuvo."
  exit 1
fi

for u in mysql daemon; do
  setfacl -m "u:$u:x" "$TARGET_HOME"
  setfacl -m "u:$u:x" "$TARGET_HOME/.local"
  setfacl -m "u:$u:x" "$TARGET_HOME/.local/share"
done
setfacl -m u:mysql:x "$LUKS_MOUNT"
chmod 711 "$LUKS_MOUNT"

install -d "$DEST"
chown mysql:mysql "$DEST"
chmod 711 "$DEST"

rsync -aHAX --numeric-ids --exclude='*.sock' --exclude='*.sock.lock' \
  --exclude='*.pid' "$SOURCE/" "$DEST/"

chown mysql:mysql "$DEST"
chmod 711 "$DEST"

if ! diff -qr --exclude='*.sock' --exclude='*.sock.lock' --exclude='*.pid' \
  "$SOURCE" "$DEST" >/tmp/mysql-luks-diff.txt; then
  echo "La copia no coincide con el origen. Revisa /tmp/mysql-luks-diff.txt"
  exit 1
fi

mv "$SOURCE" "$OLD"
ln -s "$DEST" "$SOURCE"

if ! /opt/lampp/lampp startmysql; then
  restore_origin
  exit 1
fi

ready=0
for _ in {1..30}; do
  if /opt/lampp/bin/mysqladmin --socket="$SOCKET" ping >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if (( ready != 1 )); then
  restore_origin
  exit 1
fi

if [[ "$(readlink -f "$SOURCE")" != "$DEST" ]]; then
  echo "El enlace de $SOURCE no apunta a $DEST."
  restore_origin
  exit 1
fi

app_ok=0
for _ in {1..5}; do
  if sudo -u "$TARGET_USER" -H bash -c "cd '$APP_DIR' && .venv/bin/python -c \"from sqlalchemy import text; from app.db.session import get_sessionmaker; sm=get_sessionmaker(); db=sm(); print(db.scalar(text('SELECT COUNT(*) FROM users'))); db.close()\""; then
    app_ok=1
    break
  fi
  sleep 1
done
if (( app_ok != 1 )); then
  echo "La aplicación no pudo consultar MariaDB con el nuevo datadir."
  restore_origin
  exit 1
fi

switched=1
echo "MariaDB usa ahora $DEST dentro del volumen LUKS."
echo "Inicia la aplicación con: cd $APP_DIR && scripts/run.sh"
read -r -p "¿Deshabilitar el arranque automático de XAMPP hasta desbloquear LUKS? [S/n] " disable_xampp
if [[ ! "$disable_xampp" =~ ^[nN]$ ]]; then
  systemctl disable xampp.service || true
  echo "XAMPP ya no arranca solo. Tras desbloquear LUKS ejecuta: sudo /opt/lampp/lampp start"
fi

read -r -p "¿Eliminar la copia sin cifrar $OLD? [s/N] " remove_old
if [[ "$remove_old" =~ ^[sS]$ ]]; then
  rm -rf "$OLD"
  echo "Copia sin cifrar eliminada."
else
  echo "Conserva $OLD hasta que verifiques el funcionamiento."
fi
