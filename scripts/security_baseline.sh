#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Ejecutar con sudo: sudo $0"
  exit 1
fi

TARGET_USER="${SUDO_USER:-maudaemon}"
if ! id "$TARGET_USER" >/dev/null 2>&1; then
  echo "Usuario no existe: $TARGET_USER"
  exit 1
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
LUKS_FILE="$TARGET_HOME/.local/share/secopsentinel.luks"
MOUNT_DIR="$TARGET_HOME/.local/share/secopsentinel"
CONFIG_LINK="$TARGET_HOME/.config/secopsentinel"
MAPPER="secopsentinel-data"

echo "Usuario: $TARGET_USER ($TARGET_HOME)"
read -r -p "Continuar con LUKS, firewall, Apache/FTP y ClamAV? [s/N] " confirm
[[ "$confirm" =~ ^[sS]$ ]] || exit 0

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y cryptsetup-bin clamav clamav-daemon ufw

install -d -m 700 -o "$TARGET_USER" -g "$TARGET_GROUP" "$TARGET_HOME/.local/share"

if [[ -e "$LUKS_FILE" ]] && ! cryptsetup isLuks "$LUKS_FILE" 2>/dev/null; then
  echo "El archivo $LUKS_FILE existe pero no es un contenedor LUKS."
  read -r -p "¿Eliminarlo y crearlo de nuevo? [s/N] " recreate
  if [[ "$recreate" =~ ^[sS]$ ]]; then
    rm -f "$LUKS_FILE"
  else
    exit 1
  fi
fi

if [[ -e "$LUKS_FILE" ]]; then
  echo "Contenedor existente: $LUKS_FILE (no se formatea)"
else
  if [[ -d "$MOUNT_DIR" ]] && [[ -n "$(ls -A "$MOUNT_DIR" 2>/dev/null)" ]]; then
    echo "El punto de montaje no está vacío: $MOUNT_DIR"
    exit 1
  fi
  AVAILABLE_KB="$(df -Pk "$TARGET_HOME/.local/share" | awk 'NR==2 {print $4}')"
  if (( AVAILABLE_KB < 50 * 1024 * 1024 )); then
    echo "Espacio insuficiente para el contenedor de 50 GB."
    exit 1
  fi
  truncate -s 50G "$LUKS_FILE"
  chown "$TARGET_USER:$TARGET_GROUP" "$LUKS_FILE"
  chmod 600 "$LUKS_FILE"
  echo "Se pedirá la frase de paso del contenedor LUKS."
  echo "En la confirmación de cryptsetup escribe YES en mayúsculas."
  cryptsetup luksFormat --type luks2 "$LUKS_FILE"
  read -r -p "¿Añadir ahora una clave de recuperación? [S/n] " add_recovery
  if [[ ! "$add_recovery" =~ ^[nN]$ ]]; then
    echo "Guarda la clave de recuperación en un medio externo, no en este equipo."
    cryptsetup luksAddKey "$LUKS_FILE"
  fi
fi

if [[ ! -e "/dev/mapper/$MAPPER" ]]; then
  cryptsetup open "$LUKS_FILE" "$MAPPER"
fi

if ! blkid "/dev/mapper/$MAPPER" >/dev/null 2>&1; then
  mkfs.ext4 -L "$MAPPER" "/dev/mapper/$MAPPER"
fi

install -d -m 700 -o "$TARGET_USER" -g "$TARGET_GROUP" "$MOUNT_DIR"
if ! mountpoint -q "$MOUNT_DIR"; then
  mount "/dev/mapper/$MAPPER" "$MOUNT_DIR"
fi
chown "$TARGET_USER:$TARGET_GROUP" "$MOUNT_DIR"

install -d -m 700 -o "$TARGET_USER" -g "$TARGET_GROUP" \
  "$MOUNT_DIR/config" "$MOUNT_DIR/cases" "$MOUNT_DIR/quarantine" \
  "$MOUNT_DIR/exports" "$MOUNT_DIR/backups"

if [[ -L "$CONFIG_LINK" ]]; then
  ln -sfn "$MOUNT_DIR/config" "$CONFIG_LINK"
elif [[ -e "$CONFIG_LINK" ]]; then
  echo "Existe $CONFIG_LINK y no es symlink; muévelo a $MOUNT_DIR/config y vuelve a ejecutar."
  exit 1
else
  install -d -m 700 -o "$TARGET_USER" -g "$TARGET_GROUP" "$TARGET_HOME/.config"
  ln -s "$MOUNT_DIR/config" "$CONFIG_LINK"
fi

read -r -p "Ruta externa para respaldo de cabecera LUKS (vacío para omitir): " header_backup
if [[ -n "$header_backup" ]]; then
  cryptsetup luksHeaderBackup "$LUKS_FILE" --header-backup-file "$header_backup"
  chown "$TARGET_USER:$TARGET_GROUP" "$header_backup" 2>/dev/null || true
  chmod 600 "$header_backup" 2>/dev/null || true
fi

echo "Firewall: se denegará la entrada. Si usas SSH, permite tu red antes de continuar."
read -r -p "¿Permitir SSH desde una red local? [s/N] " allow_ssh
if [[ "$allow_ssh" =~ ^[sS]$ ]]; then
  read -r -p "Red CIDR (ej. 192.168.1.0/24): " lan_cidr
  ufw allow from "$lan_cidr" to any port 22 proto tcp
fi
ufw default deny incoming
ufw default allow outgoing
ufw allow in on lo
ufw --force enable

for conf in /opt/lampp/etc/httpd.conf /opt/lampp/etc/extra/httpd-ssl.conf; do
  cp -a "$conf" "$conf.bak.$(date +%F-%H%M%S)"
done
sed -i -E 's/^Listen[[:space:]]+80$/Listen 127.0.0.1:80/' /opt/lampp/etc/httpd.conf
sed -i -E 's/^Listen[[:space:]]+443$/Listen 127.0.0.1:443/' /opt/lampp/etc/extra/httpd-ssl.conf
/opt/lampp/lampp stopapache || true
/opt/lampp/lampp startapache

/opt/lampp/lampp stopftp || true

read -r -p "¿Deshabilitar Avahi/mDNS? [S/n] " disable_avahi
if [[ ! "$disable_avahi" =~ ^[nN]$ ]]; then
  systemctl disable --now avahi-daemon.service avahi-daemon.socket || true
fi

systemctl stop clamav-freshclam || true
freshclam || true
systemctl enable --now clamav-freshclam || true
systemctl enable --now clamav-daemon || true

EICAR='X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
printf '%s\n' "$EICAR" > /tmp/eicar.com
if clamscan /tmp/eicar.com >/tmp/eicar.out 2>&1; then
  echo "FALLO: ClamAV no detectó EICAR"
else
  if grep -q 'FOUND' /tmp/eicar.out; then
    echo "OK: ClamAV detectó EICAR"
  else
    echo "FALLO: revisar /tmp/eicar.out"
  fi
fi
rm -f /tmp/eicar.com /tmp/eicar.out

echo "== Verificación =="
ss -tulpn
ufw status verbose
cryptsetup status "$MAPPER" || true
clamscan --version || true
