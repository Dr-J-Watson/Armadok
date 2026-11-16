"""Désactive la double authentification d'un compte (appareil perdu) :
   docker compose run --rm armadok python -m app.tools.reset_2fa <utilisateur>"""
import sys

from app import db
from app.config import settings

settings.validate()
db.init()
if len(sys.argv) != 2:
    raise SystemExit("Usage : python -m app.tools.reset_2fa <utilisateur>")
if not db.one("SELECT 1 FROM users WHERE username=?", (sys.argv[1],)):
    raise SystemExit("Utilisateur introuvable")
db.run("UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_last_step=0, recovery_codes='' WHERE username=?", (sys.argv[1],))
print("Double authentification désactivée pour", sys.argv[1])
