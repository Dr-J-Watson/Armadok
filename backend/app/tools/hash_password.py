"""docker compose run --rm armadok python -m app.tools.hash_password"""
import getpass

import bcrypt

pw = getpass.getpass("Mot de passe : ")
if pw != getpass.getpass("Confirmez : "):
    raise SystemExit("Les mots de passe diffèrent.")
print("\nAjoutez dans .env (guillemets simples obligatoires) :\n")
print(f"ADMIN_PASSWORD_HASH='{bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(12)).decode()}'")
