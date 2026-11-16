# Changelog

## [1.1.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.0.0...v1.1.0) (2026-11-16)


### Nouveautés

* Docker — détail des images, réseaux (créer, relier, détacher) et volumes (contenu en lecture seule), installation guidée de Traefik, services « terminés » sans alerte, Publier ouvre directement l'assistant de site, terminal dans les logs d'un projet, nouveau nœud selon le type, chemins longs lisibles, infobulles, Docker au-dessus de Sites, nom Armadok en bas du menu ([1f10d5e](https://github.com/Dr-J-Watson/Armadok/commit/1f10d5e5d025900c565f46ffea52cba718ef46b0))


### Corrections

* l'installation demande la version d'Armadok (latest ou un numéro précis) au lieu d'imposer une image ; une version de développement retombe sur latest dans la commande du worker ([3087189](https://github.com/Dr-J-Watson/Armadok/commit/3087189a5d113a8fc22022eca4a6abc4d35e48dc))

## 1.0.0 (2026-11-16)


### Nouveautés

* DNS (OVH, DuckDNS, DNS local, AdGuard Home, Pi-hole) et certificats gérés sans jamais lire une clé privée ([0adfb0a](https://github.com/Dr-J-Watson/Armadok/commit/0adfb0a523c60b660db3c895fc2d1fe5b7ec5b7e))
* gestion de Docker liée à Traefik (conteneurs, projets compose, éditeur Monaco, Git en SSH, webhook, terminal) sur le hub ou sur un worker ([0adfb0a](https://github.com/Dr-J-Watson/Armadok/commit/0adfb0a523c60b660db3c895fc2d1fe5b7ec5b7e))
* gestion de Traefik par nœud (sites, middlewares, services, certificats, rangement, historique des fichiers) depuis une interface web, avec nœuds distants par worker ([0adfb0a](https://github.com/Dr-J-Watson/Armadok/commit/0adfb0a523c60b660db3c895fc2d1fe5b7ec5b7e))
* installation guidée par make (hub, worker Traefik, Docker ou les deux) et application installable sur téléphone ([0adfb0a](https://github.com/Dr-J-Watson/Armadok/commit/0adfb0a523c60b660db3c895fc2d1fe5b7ec5b7e))
* rôles, groupes et 59 droits fins par nœud, accès temporaires et d'urgence, double authentification, journal d'audit chaîné ([0adfb0a](https://github.com/Dr-J-Watson/Armadok/commit/0adfb0a523c60b660db3c895fc2d1fe5b7ec5b7e))
* surveillance (Prometheus, alertes, visiteurs, carte des adresses, attaques probables) et bannissement manuel ou automatique par règles ([0adfb0a](https://github.com/Dr-J-Watson/Armadok/commit/0adfb0a523c60b660db3c895fc2d1fe5b7ec5b7e))
