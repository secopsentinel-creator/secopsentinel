import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.auth import hash_password, password_policy_error
from app.db.session import get_sessionmaker
from app.models import User


def read_password() -> str:
    password = os.environ.get("SECOPS_ADMIN_PASSWORD")
    if password is not None:
        return password
    password_file = os.environ.get("SECOPS_ADMIN_PASSWORD_FILE")
    if password_file is not None:
        return Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
    password = getpass.getpass("Contraseña: ")
    confirmation = getpass.getpass("Confirmar contraseña: ")
    if password != confirmation:
        raise SystemExit("Las contraseñas no coinciden.")
    return password


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--username",
        default=os.environ.get("SECOPS_ADMIN_USERNAME", "maudaemon"),
    )
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    password = read_password()
    error = password_policy_error(password)
    if error:
        print(error)
        return 2

    password_hash = hash_password(password)
    sessionmaker = get_sessionmaker()
    with sessionmaker() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if user is None:
            user = User(
                username=args.username,
                password_hash=password_hash,
                role="investigador",
            )
            db.add(user)
            action = "creado"
        else:
            if not args.reset:
                print("El usuario ya existe. Use --reset para cambiar la contraseña.")
                return 1
            user.password_hash = password_hash
            action = "actualizado"
        db.commit()

    print(f"Usuario {args.username} {action}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
