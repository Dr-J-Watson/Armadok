"""Sauvegarde d'un NŒUD, côté hub : récupérer les fichiers de la machine du nœud (par node_backup_ops), ranger l'archive, la comparer à l'état actuel, restaurer fichier par fichier.

Les archives sont rangées sur le hub dans `sauvegardes-noeuds/<id du nœud>/` ; elles ne font PAS partie de la sauvegarde complète du hub (c'est le responsable du nœud qui les possède).
Archive = un zip : `manifest.json` (scope « node », empreinte de chaque fichier) et `machine/<élément>/<chemin>`. Même chiffrement par mot de passe que la sauvegarde du hub.
Restaurer = choisir des FICHIERS dans la comparaison (modifié / absent maintenant / en trop) : rien d'autre n'est touché, et une sauvegarde « avant-restauration » est faite d'abord."""
import hashlib
import json
import os
import secrets
import shutil
import threading
import time
import zipfile
from pathlib import Path

from . import backup, node_backup_ops as ops, worker_link
from .config import settings
from .traefik_store import StoreError

FORMAT = 1
MAX_PER_NODE = 20
MAX_ITEMS = 3000
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def store(node_id: str) -> Path:
    d = settings.data_dir / "sauvegardes-noeuds" / node_id
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def call(ctx, op: str, args: dict):
    """L'opération sur la machine du nœud : son worker, ou ce hub-même pour le nœud local."""
    if ctx.node["kind"] == "remote":
        return worker_link.call(ctx.id, op, args)
    return ops.handle(op, args, tuple(ctx.node.get("modules") or ()))


def machine(ctx) -> dict:
    """Ce qu'on peut sauvegarder maintenant sur ce nœud ; `online` faux (et rien à proposer) si son worker n'est pas connecté."""
    try:
        return {"online": True, "components": call(ctx, "nb_info", {})["components"]}
    except StoreError as e:
        if e.status in (503, 504, 429):
            return {"online": False, "components": []}
        raise


def listing(node_id: str) -> list[dict]:
    return backup.listing(store(node_id))


# ------------------------------------------------------------------ créer
def _pull(ctx, token: str, dst: Path, expected: int) -> None:
    import base64
    off = 0
    with open(dst, "wb") as f:
        while True:
            r = call(ctx, "nb_pack_read", {"token": token, "offset": off})
            raw = base64.b64decode(r["b64"])
            f.write(raw)
            off += len(raw)
            if r["done"]:
                break
            if off > expected + ops.CHUNK:
                raise StoreError("Réponse du worker incohérente", 502)


def _prune_safety(node_id: str) -> None:
    """À la limite, on retire la plus ancienne sauvegarde « avant restauration » (les manuelles ne sont jamais supprimées d'office)."""
    items = [b for b in listing(node_id) if b["kind"] == "avant restauration"]
    if items:
        backup.delete(sorted(items, key=lambda b: b["createdAt"])[0]["name"], store(node_id))


def create(ctx, components: list[str], user: str, label: str = "", passphrase: str = "", kind: str = "manuelle") -> dict:
    comps = [c for c in dict.fromkeys(components) if c in ops.COMPONENTS]
    if not comps:
        raise StoreError("Choisissez au moins un élément à sauvegarder", 422)
    if passphrase and len(passphrase) < 8:
        raise StoreError("Le mot de passe de la sauvegarde doit faire 8 caractères au moins", 422)
    node_id = ctx.id
    if len(listing(node_id)) >= MAX_PER_NODE:
        if kind == "avant restauration":
            _prune_safety(node_id)
        if len(listing(node_id)) >= MAX_PER_NODE:
            raise StoreError(f"Ce nœud a déjà {MAX_PER_NODE} sauvegardes : supprimez-en une avant d'en créer", 409)
    d = store(node_id)
    pack = call(ctx, "nb_pack", {"components": comps})
    if backup._free(d) < pack["size"] * 3 + 50 * 1024 * 1024:
        raise StoreError("Pas assez de place sur le disque du hub pour cette sauvegarde", 507)
    work = backup.tmp_dir() / f"nbk-{secrets.token_hex(4)}"
    work.mkdir(mode=0o700)
    try:
        raw_zip = work / "machine.zip"
        _pull(ctx, pack["token"], raw_zip, pack["size"])
        stamp = time.strftime("%Y%m%d-%H%M%S")
        lab = backup.clean_label(label)
        stem = f"noeud-{stamp}" + (f"-{lab}" if lab else "")
        final_zip = work / (stem + ".zip")
        hashes: dict[str, str] = {}
        with zipfile.ZipFile(raw_zip) as zi, zipfile.ZipFile(final_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zo:
            for info in zi.infolist():
                if info.is_dir():
                    continue
                h = hashlib.sha256()
                with zi.open(info) as fi, zo.open("machine/" + info.filename, "w", force_zip64=True) as fo:
                    for chunk in iter(lambda: fi.read(1024 * 1024), b""):
                        h.update(chunk)
                        fo.write(chunk)
                hashes["machine/" + info.filename] = h.hexdigest()
            manifest = {"format": FORMAT, "scope": "node", "app": os.environ.get("APP_VERSION", "dev"), "createdAt": time.time(), "createdBy": user, "label": label.strip()[:80], "kind": kind,
                        "node": {"id": node_id, "name": ctx.node["name"]}, "components": pack["components"], "files": hashes}
            zo.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        final = d / (stem + (".armbak" if passphrase else ".zip"))
        part = final.with_name(final.name + ".armadok-part")
        if passphrase:
            backup.encrypt_file(final_zip, part, passphrase)
        else:
            shutil.move(str(final_zip), part)
        os.chmod(part, 0o600)
        os.replace(part, final)
        meta = {k: v for k, v in manifest.items() if k != "files"}
        meta.update(name=final.name, encrypted=bool(passphrase), size=final.stat().st_size)
        backup.write_meta(final.name, meta, d)
        return meta
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ lire et comparer
def _open(ctx, name: str, passphrase: str) -> "backup.Opened":
    o = backup.Opened(name, passphrase, store(ctx.id))
    try:
        m = o.manifest()
        if m.get("scope") != "node":
            raise StoreError("Ce n'est pas une sauvegarde de nœud", 422)
    except Exception:
        o.close()
        raise
    return o


def inspect(ctx, name: str, passphrase: str = "") -> dict:
    o = _open(ctx, name, passphrase)
    try:
        m = o.manifest()
    finally:
        o.close()
    node = m.get("node") or {}
    return {"manifest": {k: m.get(k) for k in ("app", "createdAt", "createdBy", "label", "kind", "components")}, "node": node, "sameNode": node.get("id") == ctx.id}


def diff(ctx, name: str, passphrase: str = "") -> dict:
    """Ce qui diffère entre la sauvegarde et le nœud MAINTENANT : modifié, absent maintenant (restaurable), en trop (absent de la sauvegarde)."""
    o = _open(ctx, name, passphrase)
    try:
        m = o.manifest()
        with zipfile.ZipFile(o.zip_path) as z:
            sizes = {i.filename: i.file_size for i in z.infolist()}
    finally:
        o.close()
    have = set(ops.available(tuple(ctx.node.get("modules") or ())))
    comps = [c for c in (m.get("components") or {}) if c in have]
    cur = call(ctx, "nb_state", {"components": comps})["files"]
    entries, same = [], 0
    for c in comps:
        prefix = f"machine/{c}/"
        saved = {k[len(prefix):]: h for k, h in (m.get("files") or {}).items() if k.startswith(prefix)}
        now = cur.get(c, {})
        for rel, h in saved.items():
            if rel not in now:
                entries.append({"component": c, "rel": rel, "status": "missing", "sizeBackup": sizes.get(prefix + rel), "sizeNow": None})
            elif now[rel][0] != h:
                entries.append({"component": c, "rel": rel, "status": "modified", "sizeBackup": sizes.get(prefix + rel), "sizeNow": now[rel][1]})
            else:
                same += 1
        for rel, (_h, size) in now.items():
            if rel not in saved:
                entries.append({"component": c, "rel": rel, "status": "extra", "sizeBackup": None, "sizeNow": size})
    order = {"modified": 0, "missing": 1, "extra": 2}
    entries.sort(key=lambda e: (order[e["status"]], e["component"], e["rel"]))
    skipped = [c for c in (m.get("components") or {}) if c not in have]
    return {"entries": entries[:5000], "truncated": len(entries) > 5000, "same": same, "unavailable": skipped, "components": {c: ops.COMPONENTS[c][0] for c in ops.COMPONENTS}}


def file_diff(ctx, name: str, passphrase: str, component: str, rel: str) -> dict:
    ops.safe_rel(rel)
    if component not in ops.COMPONENTS:
        raise StoreError("Élément inconnu", 422)
    o = _open(ctx, name, passphrase)
    try:
        with zipfile.ZipFile(o.zip_path) as z:
            try:
                info = z.getinfo(f"machine/{component}/{rel}")
            except KeyError:
                after = None
            else:
                if info.file_size > ops.MAX_READ:
                    after = {"large": True, "size": info.file_size}
                else:
                    raw = z.read(info)
                    after = {"binary": True, "size": len(raw)} if b"\x00" in raw[:8192] else {"content": raw.decode("utf-8", "replace"), "size": len(raw)}
    finally:
        o.close()
    try:
        before = call(ctx, "nb_read", {"component": component, "rel": rel})
    except StoreError as e:
        if e.status != 404:
            raise
        before = None
    return {"before": before, "after": after}


# ------------------------------------------------------------------ restaurer
def restore(ctx, name: str, passphrase: str, items: list[dict], user: str) -> dict:
    """Restaure les fichiers choisis (action « restore » : remettre la version de la sauvegarde ; « delete » : retirer un fichier absent de la sauvegarde)."""
    import base64
    if not items or len(items) > MAX_ITEMS:
        raise StoreError(f"Choisissez entre 1 et {MAX_ITEMS} fichiers", 422)
    for it in items:
        if it.get("component") not in ops.COMPONENTS or it.get("action") not in ("restore", "delete"):
            raise StoreError("Demande de restauration invalide", 422)
        ops.safe_rel(it.get("rel"))
    o = _open(ctx, name, passphrase)   # lu et déchiffré AVANT tout : un mot de passe faux ne laisse rien à moitié fait
    try:
        have = set(ops.available(tuple(ctx.node.get("modules") or ())))
        involved = sorted({it["component"] for it in items})
        if any(c not in have for c in involved):
            raise StoreError("Un élément choisi n'existe pas sur ce nœud (module absent)", 409)
        safety = create(ctx, involved, user, label="avant-restauration", kind="avant restauration")["name"]
        done = deleted = 0
        errors: list[str] = []
        with zipfile.ZipFile(o.zip_path) as z:
            for it in items:
                c, rel = it["component"], it["rel"]
                try:
                    if it["action"] == "delete":
                        call(ctx, "nb_delete", {"component": c, "rel": rel})
                        deleted += 1
                        continue
                    with z.open(f"machine/{c}/{rel}") as f:
                        off, part = 0, f.read(ops.CHUNK)
                        while True:
                            nxt = f.read(ops.CHUNK)   # un morceau d'avance : c'est ce qui dit si celui-ci est le dernier
                            last = not nxt
                            call(ctx, "nb_put", {"component": c, "rel": rel, "b64": base64.b64encode(part).decode(), "offset": off, "last": last})
                            off += len(part)
                            if last:
                                break
                            part = nxt
                    done += 1
                except KeyError:
                    errors.append(f"{c}/{rel} : absent de la sauvegarde")
                except StoreError as e:
                    errors.append(f"{c}/{rel} : {e}")
        return {"restored": done, "deleted": deleted, "errors": errors, "safety": safety}
    finally:
        o.close()


# ------------------------------------------------------------------ tâches de fond (une à la fois par nœud)
def start_job(node_id: str, fn, *args, **kw) -> str:
    jid = secrets.token_hex(6)
    with _lock:
        for k in [k for k, j in _jobs.items() if j["done"] and time.time() - j["ended"] > 600]:
            del _jobs[k]
        if any(not j["done"] and j["node"] == node_id for j in _jobs.values()):
            raise StoreError("Une sauvegarde ou une restauration est déjà en cours sur ce nœud", 409)
        _jobs[jid] = {"node": node_id, "done": False, "result": None, "error": "", "ended": 0.0}

    def run() -> None:
        try:
            _jobs[jid]["result"] = fn(*args, **kw)
        except StoreError as e:
            _jobs[jid]["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            _jobs[jid]["error"] = f"Erreur inattendue ({type(e).__name__})"
        _jobs[jid]["done"], _jobs[jid]["ended"] = True, time.time()
    threading.Thread(target=run, name=f"nbackup-{jid}", daemon=True).start()
    return jid


def job(node_id: str, jid: str) -> dict:
    j = _jobs.get(jid)
    if not j or j["node"] != node_id:
        raise StoreError("Tâche inconnue", 404)
    return {"done": j["done"], "result": j["result"], "error": j["error"]}
