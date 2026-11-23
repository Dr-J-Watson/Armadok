"""Activer / désactiver routeurs, services, middlewares, options TLS et fichiers entiers — de façon réversible.

Un bloc désactivé est mis en commentaire dans son fichier (« #~ »), un fichier entier est renommé en « .yml.off ». Ce qui en dépend suit :
 - un service désactivé entraîne les routeurs (et services composés) qui l'utilisent ;
 - un middleware / une option TLS désactivé disparaît des listes qui y font référence, le contenu d'origine de la liste étant gardé
   dans une note « #% ref » pour être rétabli à la réactivation. Les notes ne concernent que ce que l'interface a retiré.
"""

from .traefik_lint import _refs
from .traefik_store import KINDS, OFF, StoreError, _lock, parse_items, Store
from .yamltext import (Unsupported, add_pause_tag, block_end, comment_range, drop_pause_tag, read_refs, set_value, uncomment,
                       uncomment_range, write_refs, sync_sections, drop_empty_sections)

# où un routeur / middleware mentionne un autre bloc : chemin -> (type visé)
REFS = {
    "routers": [(("middlewares",), "middlewares"), (("tls", "options"), "options")],
    "middlewares": [(("chain", "middlewares"), "middlewares")],
}


def _base(name: str) -> str | None:
    n, _, provider = name.partition("@")
    return n if provider in ("", "file") else None


def _service_ids(it: dict, index: set[str]) -> list[str]:
    return [i for i in (f"{it['proto']}/services/{_base(n)}" for n in _refs(it)["services"] if _base(n)) if i in index]


def _ref_target(it: dict, ref_kind: str, name: str) -> tuple[str, str, str] | None:
    b = _base(name) if isinstance(name, str) else None
    if not b:
        return None
    return ("tls", "options", b) if ref_kind == "options" else (it["proto"], ref_kind, b)


AUTO_CHAIN = "auto:chain"   # groupe mis de côté parce qu'il n'a plus aucun membre actif
AUTO_DEP = "auto:dep"       # élément tombé avec son service : il revient dès que ce service est réactivé


def _members(it: dict, index: set[str]) -> list[str] | None:
    """Ids des membres d'un groupe (chain) ; None si ce n'est pas un groupe, ou s'il contient autre chose que des blocs de ces fichiers."""
    if it["kind"] != "middlewares" or it["proto"] not in ("http", "tcp"):
        return None
    names = (it.get("refs") or {}).get("chain.middlewares") or ((it["spec"] or {}).get("chain") or {}).get("middlewares")
    if not isinstance(names, list) or not names:
        return None
    ids = [f"{it['proto']}/middlewares/{_base(n)}" if isinstance(n, str) and _base(n) else None for n in names]
    return None if any(i is None or i not in index for i in ids) else ids


def _get(spec: dict, path: tuple[str, ...]):
    for p in path:
        spec = spec.get(p) if isinstance(spec, dict) else None
    return spec


def toggle(store: Store, ids: list[str], files: list[str], enable: bool, tag: str | None, reconcile: bool, dry: bool) -> dict:
    with _lock:
        texts, off = store.read_all()
        items, errors = parse_items(texts, off)
        by_id = {i["id"]: i for i in items}
        for f in files:
            if f not in texts:
                raise StoreError("Fichier introuvable", 404)
        for i in ids:
            if i not in by_id:
                raise StoreError("Élément introuvable", 404)
        touched = {by_id[i]["file"] for i in ids} | set(files)
        for f in touched & set(errors):
            raise StoreError(f"Le fichier {f} contient une erreur YAML : {errors[f]}")
        index = set(by_id)
        disabled_now = {i["id"] for i in items if i["disabled"]}
        in_file = lambda f: [i for i in items if i["file"] == f]  # noqa: E731

        file_names = {f: (f[:-4] if f.lower().endswith(OFF) else f) for f in files}
        for f in files:
            if enable != f.lower().endswith(OFF):
                raise StoreError("Ce fichier est déjà " + ("actif" if enable else "désactivé"), 409)

        flip: list[str] = []          # blocs à (dés)activer en plus de ceux demandés (dépendances)
        tags: dict[str, str] = {}
        marked = {i["id"] for i in items if i["marked"]}
        off_after = (off - set(files)) if enable else (off | set(files))
        seeds = [i for i in ids if (i in marked) == enable and not (not enable and by_id[i]["file"] in off)]
        if enable:
            for i in ids:
                if by_id[i]["file"] in off_after:
                    raise StoreError(f"« {by_id[i]['name']} » est dans un fichier désactivé ({by_id[i]['file']}) : réactivez d'abord le fichier", 409)
        marked_after = (marked - set(seeds)) if enable else (marked | set(seeds))

        def active(i: dict, marks: set[str], offs: set[str]) -> bool:
            return i["file"] not in offs and i["id"] not in marks

        if enable:  # ce qui redevient actif doit retrouver ses services
            for i in seeds:
                mem = _members(by_id[i], index)
                if mem and not any(active(by_id[m], marked_after, off_after) or m in seeds for m in mem):
                    raise StoreError(f"« {by_id[i]['name']} » est un groupe dont tous les éléments sont désactivés : réactivez-en au moins un", 409)
            queue = [i["id"] for i in items if active(i, marked_after, off_after) and not active(i, marked, off)]
            while queue:
                it = by_id[queue.pop()]
                for dep in _service_ids(it, index):
                    d = by_id[dep]
                    if active(d, marked_after, off_after):
                        continue
                    if d["file"] in off_after:
                        raise StoreError(f"« {it['name']} » a besoin de « {d['name']} » qui est dans le fichier désactivé {d['file']}", 409)
                    flip.append(dep)
                    marked_after = marked_after - {dep}
                    queue.append(dep)
            woken = {i for i in seeds if by_id[i]["kind"] == "services"}   # services dont on demande la réactivation (pas ceux qu'un routeur rappelle)
            changed = True
            while changed:  # ce qui était tombé avec eux revient ; un groupe mis de côté (vide) revient dès qu'un de ses membres est actif
                changed = False
                for it in items:
                    if it["id"] not in marked_after or it["file"] in off_after:
                        continue
                    if it.get("pausedBy") == AUTO_CHAIN:
                        mem = _members(it, index)
                        if mem and any(active(by_id[m], marked_after, off_after) for m in mem):
                            flip.append(it["id"])
                            marked_after = marked_after - {it["id"]}
                            changed = True
                    elif it.get("pausedBy") == AUTO_DEP:
                        deps = _service_ids(it, index)
                        if deps and set(deps) & woken and all(active(by_id[d], marked_after, off_after) for d in deps):
                            flip.append(it["id"])
                            marked_after = marked_after - {it["id"]}
                            if it["kind"] == "services":
                                woken.add(it["id"])
                            changed = True
        else:  # ce qui perd un service tombe avec lui
            changed = True
            while changed:
                changed = False
                for it in items:
                    if not active(it, marked_after, off_after):
                        continue
                    mem = _members(it, index)
                    if any(not active(by_id[d], marked_after, off_after) for d in _service_ids(it, index)):
                        flip.append(it["id"])
                        tags[it["id"]] = AUTO_DEP
                    elif mem and all(not active(by_id[m], marked_after, off_after) for m in mem):
                        flip.append(it["id"])
                        tags[it["id"]] = AUTO_CHAIN
                    else:
                        continue
                    marked_after = marked_after | {it["id"]}
                    changed = True

        block_ids = list(dict.fromkeys(seeds + flip))
        new = dict(texts)
        for path in {by_id[i]["file"] for i in block_ids}:
            lines = new[path].split("\n")
            for it in sorted((by_id[i] for i in block_ids if by_id[i]["file"] == path), key=lambda x: -x["line"]):
                if it["flow"]:
                    raise StoreError(f"« {it['name']} » est écrit en YAML « en ligne » dans {path} : modifiez le fichier à la main", 422)
                n = it["line"]
                if enable:
                    plain = uncomment("\n".join(lines)).split("\n")
                    uncomment_range(lines, n, block_end(plain, n))
                    drop_pause_tag(lines, n)
                else:
                    if tags.get(it["id"]) or (tag and it["id"] in seeds):
                        add_pause_tag(lines, n, tags.get(it["id"]) or tag)
                    comment_range(lines, n, block_end(lines, n))
            new[path] = _tidy("\n".join(lines))

        renamed: dict[str, str] = {}
        for f in files:
            dest = f[:-4] if enable else f + ".off"
            renamed[f] = dest
        final_texts = {renamed.get(p, p): t for p, t in new.items()}
        final_off = {p for p in off if p not in renamed} | {renamed[f] for f in files if not enable}

        refs: list[dict] = []
        skipped: list[dict] = []
        if reconcile:
            refs, skipped = _reconcile(final_texts, final_off)
        # contrôle : rien ne doit devenir invalide
        _, errs = parse_items(final_texts, final_off)
        for p, e in errs.items():
            if p not in errors:
                raise StoreError(f"Modification annulée : le fichier {p} deviendrait invalide ({e})", 422)
        for p, t in final_texts.items():
            if p not in final_off:
                Store.parse_text(t)

        names = lambda ids_: [{"id": i, "kind": by_id[i]["kind"], "name": by_id[i]["name"], "file": by_id[i]["file"]} for i in ids_]  # noqa: E731
        dependents: list[str] = []
        if enable:   # éléments désactivés qui utilisent ce qu'on réactive, sans étiquette (coupés à la main ou avant cette version) : on propose
            back = {i for i in seeds if by_id[i]["kind"] == "services"}
            for it in items:
                if it["id"] in marked_after and it["id"] not in block_ids and it["file"] not in off_after and not it.get("pausedBy"):
                    deps = _service_ids(it, index)
                    if deps and set(deps) & back and all(active(by_id[d], marked_after, off_after) for d in deps):
                        dependents.append(it["id"])
        result = {"blocks": names(block_ids), "cascade": names(flip), "files": [{"path": f, "to": renamed[f]} for f in files],
                  "refs": refs, "skipped": skipped, "enable": enable, "applied": not dry, "dependents": names(dependents)}
        if dry:
            return result
        for f, dest in renamed.items():  # d'abord les noms, puis le contenu
            store.move(f, dest)
        original = {renamed.get(p, p): t for p, t in texts.items()}
        for p, t in final_texts.items():
            if t != original.get(p):
                store.write(p, t)
        return result


_SECTIONS: dict[str, tuple[str, ...]] = {}
for (_proto, _kind), (_top, _sub) in KINDS.items():
    _SECTIONS[_top] = _SECTIONS.get(_top, ()) + (_sub,)


def _tidy(text: str) -> str:
    """Évite les sections vides (`http:` sans contenu) que Traefik rejetterait pour tout le dossier."""
    return sync_sections(text, _SECTIONS, Store.parse_text(uncomment(text)))


def _reconcile(texts: dict[str, str], off: set[str]) -> tuple[list[dict], list[dict]]:
    """Met les listes de références en accord avec ce qui est actif : retire les cibles désactivées, rétablit celles qui reviennent."""
    items, _ = parse_items(texts, off)
    down = {(i["proto"], i["kind"], i["name"]) for i in items if i["disabled"]}
    log, skipped = [], []
    for path in sorted(set(i["file"] for i in items) - off):
        todo = [i for i in items if i["file"] == path and not i["disabled"] and i["kind"] in REFS and i["proto"] in ("http", "tcp")]
        if not todo:
            continue
        lines = texts[path].split("\n")
        for it in sorted(todo, key=lambda x: -x["line"]):
            saved = read_refs(lines, it["line"])
            wanted: dict[str, object] = dict(saved)
            for ref_path, ref_kind in REFS[it["kind"]]:
                key = ".".join(ref_path)
                live = _get(it["spec"], ref_path)
                desired = saved.get(key, live)
                if desired is None:
                    continue
                if isinstance(desired, list):
                    effective = [n for n in desired if _ref_target(it, ref_kind, n) not in down]
                    if it["kind"] == "middlewares" and not effective:  # ne jamais laisser un groupe vide : Traefik rejetterait tout le dossier
                        skipped.append({"id": it["id"], "reason": "groupe vide"})
                        continue
                else:
                    effective = desired if _ref_target(it, ref_kind, desired) not in down else None
                try:
                    if effective != live:
                        set_value(lines, it["line"], ref_path, effective if effective else None)
                except Unsupported as e:
                    skipped.append({"id": it["id"], "reason": str(e)})
                    continue
                if effective != desired:
                    wanted[key] = desired
                else:
                    wanted.pop(key, None)
                if effective != live:
                    old = live if isinstance(live, list) else ([live] if live else [])
                    new = effective if isinstance(effective, list) else ([effective] if effective else [])
                    log.append({"id": it["id"], "path": key, "removed": [x for x in old if x not in new], "restored": [x for x in new if x not in old]})
            if wanted != saved:
                write_refs(lines, it["line"], wanted)
        texts[path] = "\n".join(lines)
    return log, skipped


def purge(store: Store, item_id: str) -> str:
    """Supprime un bloc désactivé (et fait oublier son nom aux notes de reprise)."""
    with _lock:
        texts, off = store.read_all()
        items, _ = parse_items(texts, off)
        it = next((i for i in items if i["id"] == item_id), None)
        if not it:
            raise StoreError("Élément introuvable", 404)
        if it["file"] in off:
            raise StoreError("Cet élément est dans un fichier désactivé : réactivez le fichier ou supprimez-le entièrement", 409)
        lines = texts[it["file"]].split("\n")
        plain = uncomment("\n".join(lines)).split("\n")
        del lines[it["line"]:block_end(plain, it["line"])]
        new = dict(texts)
        left = "\n".join(lines)
        new[it["file"]] = _tidy(drop_empty_sections(left, _SECTIONS, Store.parse_text(uncomment(left))))
        # les notes de reprise ne doivent plus rétablir une référence vers un bloc qui n'existe plus
        for p, t in new.items():
            if p in off:
                continue
            ls = t.split("\n")
            for other in sorted((i for i in parse_items({p: t}, set())[0] if not i["disabled"]), key=lambda x: -x["line"]):
                saved = read_refs(ls, other["line"])
                cleaned = {k: ([n for n in v if _ref_target(other, "options" if k == "tls.options" else "middlewares", n) != (it["proto"], it["kind"], it["name"])] if isinstance(v, list) else v)
                           for k, v in saved.items()}
                cleaned = {k: v for k, v in cleaned.items() if v not in ([], None)}
                if cleaned != saved:
                    write_refs(ls, other["line"], cleaned)
            new[p] = "\n".join(ls)
        for p, t in new.items():
            if t != texts[p]:
                Store.parse_text(t)
                store.write(p, t) if t.strip() else store.delete(p)
        return it["file"]
