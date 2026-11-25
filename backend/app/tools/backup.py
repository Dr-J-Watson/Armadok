"""Sauvegardes du hub en ligne de commande : python -m app.tools.backup <commande>.

C'est le MÊME code et le même format d'archive que l'interface : une sauvegarde faite ici se restaure depuis « Tous les nœuds → Backup et sauvegardes », et inversement.
Utilisé par le Makefile (« make backup », « make restore »), y compris quand le hub est arrêté ou ne démarre plus (conteneur ponctuel : « docker compose run --rm … »).

  list [--tsv]                          les sauvegardes (--tsv : une ligne par sauvegarde, pour les scripts)
  create [--label X] [--components a,b] [--include-key]   crée une sauvegarde ; mot de passe facultatif dans la variable BACKUP_PASSWORD (jamais en argument : visible dans `ps`)
  inspect NOM [--kv]                    contenu, version, clé de chiffrement (mot de passe : BACKUP_PASSWORD)
  import FICHIER                        range un fichier .zip / .armbak dans la liste (copie, vérifiée)
  restore NOM [--components a,b]        restaure ; À LANCER HUB ARRÊTÉ (le Makefile s'en charge). Une sauvegarde de l'état actuel est faite d'abord (--no-safety : sans)
  delete NOM                            supprime
  key                                   dit si ENCRYPTION_KEY est définie et en donne l'empreinte
Codes de sortie : 0 = fait, 1 = refusé ou échec (le message dit pourquoi)."""
import argparse
import json
import os
import shutil
import sys
import time

from .. import backup
from ..config import env
from ..traefik_store import StoreError


def _human(n: float) -> str:
    for u in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "o" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} Po"


def _comps(raw: str | None) -> list[str]:
    return [c.strip() for c in (raw or ",".join(backup.COMPONENTS)).split(",") if c.strip()]


def cmd_list(a) -> int:
    rows = [b for b in backup.listing() if not b.get("scope")]
    for b in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b["createdAt"]))
        comps = ",".join(b["components"]) if b["components"] else ("?" if b["encrypted"] else "")
        if a.tsv:
            print("\t".join([b["name"], when, str(b["size"]), "1" if b["encrypted"] else "0", b.get("label") or "", comps, b.get("app") or "", "1" if b["includesKey"] else "0"]))
        else:
            print(f"{b['name']:<52} {when}  {_human(b['size']):>9}  {'protégée' if b['encrypted'] else 'en clair '}  {b.get('label') or ''}")
    if not rows and not a.tsv:
        print("Aucune sauvegarde.", file=sys.stderr)
    return 0


def cmd_create(a) -> int:
    pw = os.environ.get("BACKUP_PASSWORD", "")
    meta = backup.create(_comps(a.components), "make", label=a.label or "", passphrase=pw, include_key=a.include_key, kind="ligne de commande")
    print(f"OK\t{meta['name']}\t{meta['size']}")
    return 0


def cmd_inspect(a) -> int:
    info = backup.inspect(a.name, os.environ.get("BACKUP_PASSWORD", ""))
    m = info["manifest"]
    if a.kv:   # lignes « clé=valeur » : se lit sans jq ni python dans un script shell
        print(f"created={time.strftime('%Y-%m-%d %H:%M', time.localtime(m.get('createdAt') or 0))}")
        print(f"by={m.get('createdBy') or ''}\napp={m.get('app') or ''}\ncurrent={info['currentApp']}\nlabel={m.get('label') or ''}\nkeystatus={info['keyStatus']}\nincludeskey={'1' if m.get('includesKey') else '0'}")
        for cid, v in (m.get("components") or {}).items():
            print(f"component={cid}\t{backup.COMPONENTS.get(cid, cid)}\t{v.get('files', 0)}\t{v.get('bytes', 0)}")
        return 0
    print(json.dumps({**info, "createdAtText": time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("createdAt") or 0))}, ensure_ascii=False))
    return 0


def cmd_import(a) -> int:
    src = os.path.abspath(a.file)
    name = os.path.basename(src)
    if not os.path.isfile(src):
        raise StoreError("Fichier introuvable : " + src, 404)
    if not backup.NAME_OK.match(name):
        raise StoreError("Le fichier doit s'appeler quelque chose.zip ou quelque chose.armbak (lettres, chiffres, . _ -)", 422)
    dst = backup.store_dir() / name
    n = 2
    while dst.exists():
        dst = backup.store_dir() / f"{os.path.splitext(name)[0]}-{n}{os.path.splitext(name)[1]}"
        n += 1
    part = dst.with_name(dst.name + ".armadok-part")
    shutil.copyfile(src, part)
    os.chmod(part, 0o600)
    try:
        if dst.suffix == ".zip":
            man = backup._read_manifest_zip(part)
            if not man or man.get("format") is None:
                raise StoreError("Ce fichier n'est pas une sauvegarde d'Armadok (manifeste absent)", 422)
            if man.get("scope"):
                raise StoreError("C'est une sauvegarde de nœud, pas une sauvegarde du hub", 422)
            os.replace(part, dst)
            backup.write_meta(dst.name, {**man, "name": dst.name, "encrypted": False, "size": dst.stat().st_size, "kind": "importée"})
        else:
            if not backup.is_encrypted_file(part):
                raise StoreError("Ce fichier n'est pas une sauvegarde chiffrée d'Armadok", 422)
            os.replace(part, dst)
            backup.write_meta(dst.name, {"name": dst.name, "encrypted": True, "size": dst.stat().st_size, "kind": "importée", "createdAt": time.time(), "createdBy": "make"})
    finally:
        if part.exists():
            part.unlink()
    print(f"OK\t{dst.name}")
    return 0


def cmd_restore(a) -> int:
    pw = os.environ.get("BACKUP_PASSWORD", "")
    comps = _comps(a.components)
    if not a.no_safety:
        safety = backup.create(list(backup.COMPONENTS), "make", label="avant-restauration", kind="avant restauration")
        print(f"SAFETY\t{safety['name']}", file=sys.stderr)
    plan = backup.stage_restore(a.name, pw, comps, "make")
    msg = backup.apply_pending()
    print(f"OK\t{plan['files']}\t{msg}")
    if plan.get("archiveKey"):
        print(f"KEY\t{plan['archiveKey']}")
    return 0


def cmd_delete(a) -> int:
    backup.delete(a.name)
    print("OK")
    return 0


def cmd_key(_a) -> int:
    print(f"{'1' if env('ENCRYPTION_KEY') else '0'}\t{backup.key_fingerprint()}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.tools.backup", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--tsv", action="store_true")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("create")
    s.add_argument("--label", default="")
    s.add_argument("--components", default="")
    s.add_argument("--include-key", action="store_true")
    s.set_defaults(fn=cmd_create)
    s = sub.add_parser("inspect")
    s.add_argument("name")
    s.add_argument("--kv", action="store_true")
    s.set_defaults(fn=cmd_inspect)
    s = sub.add_parser("import")
    s.add_argument("file")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("restore")
    s.add_argument("name")
    s.add_argument("--components", default="")
    s.add_argument("--no-safety", action="store_true")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("delete")
    s.add_argument("name")
    s.set_defaults(fn=cmd_delete)
    s = sub.add_parser("key")
    s.set_defaults(fn=cmd_key)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except StoreError as e:
        print(f"ERREUR\t{e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERREUR\t{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
