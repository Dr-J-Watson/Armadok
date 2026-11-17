# Changelog

## [1.3.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.2.0...v1.3.0) (2026-11-17)


### Nouveautés

* un nœud qui publie ses sites via un autre Traefik hérite de ses domaines (liste en lecture seule) et de ses réglages (points d'entrée, résolveurs, adresse DNS affichés dans « Ce nœud ») ([c206dbe](https://github.com/Dr-J-Watson/Armadok/commit/c206dbe5d2a66a487a3b0ec85d32bbef3b15a407))
* un nœud sans Traefik dont les sites passent par un autre Traefik a sa catégorie « Sites » dans le menu (les sites de ce Traefik qui mènent à ses conteneurs, ouverture sur le bon nœud, nouveau site) ([0c6db78](https://github.com/Dr-J-Watson/Armadok/commit/0c6db780ce3039d81f2a5f9e04500f3e49db0e45))

## [1.2.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.1.0...v1.2.0) (2026-11-17)


### Nouveautés

* conteneur lancé à la main → projet (docker-compose.yml + .env avec les variables d'environnement, volumes conservés, remplacement de l'ancien conteneur renommé et gardé), « Nouveau projet » (vide, modèle de base, depuis un conteneur, dépôt Git, Traefik de base), bouton « Convertir en projet » dans la fiche d'un conteneur ([854294e](https://github.com/Dr-J-Watson/Armadok/commit/854294e6b83b311d819084c69f1b079a119515d1))
* installation — choix « interactif » ou « seulement le .env et le compose » dans make install / hub / worker, retrait de l'installation Portainer (make stack, worker-stack, onglet de l'assistant), README : organisation recommandée (dossier racine des projets, configuration dynamique en dossier ou en volume) ([6f41127](https://github.com/Dr-J-Watson/Armadok/commit/6f41127b57a50ce126cab6683aa068d709e65759))
* labels Traefik ⇄ fichier — d'un site vers le compose (service suggéré d'après la cible du site, fichier compose au choix, labels fusionnés sans écraser, site mis en pause dans Traefik pour éviter le doublon) et du compose vers Traefik (fichier de destination au choix, retrait des labels ensuite) ([4d9ef56](https://github.com/Dr-J-Watson/Armadok/commit/4d9ef56c22a62affd54b141dcde08a454f76d430))
* modèles — sélection des fichiers en arbre repliable avec cases à cocher (dossiers à cocher d'un coup, data et logs repliés), bouton « Gérer les modèles » (renommer, décrire, privé / partagé, supprimer) sur la fiche du projet et dans la fenêtre d'enregistrement ([8ef31db](https://github.com/Dr-J-Watson/Armadok/commit/8ef31db604e36b60085121c9f3764362b6ee6dae))
* modèles de projet — enregistrer un projet comme modèle (fichiers au choix, secrets du .env vidés, nom du projet en paramètre), « Nouveau projet → À partir d'un modèle » (modèle de base, exemple Minecraft, modèles personnels, variables du .env à remplir), ligne « Fichiers » sous le chemin du projet ([90082e9](https://github.com/Dr-J-Watson/Armadok/commit/90082e91a0eb277aaf85c80a367f5e5506007e32))
* modèles de projet privés ou partagés (privé par défaut, partage modifiable, secrets toujours vidés quand un modèle est partagé) ([53b8641](https://github.com/Dr-J-Watson/Armadok/commit/53b864126b556eeb06983f46dd5fc88c1cb0213f))
* projets — choix des fichiers compose (override pris par défaut, jeux au choix, arrêt et redémarrage avec ceux du dernier lancement) et onglet Makefile (cibles avec leur description, lancement avec la sortie en direct) ([ef93695](https://github.com/Dr-J-Watson/Armadok/commit/ef93695b0a6a08d338df743927ebcf42c442d06b))
* un nœud peut publier ses sites avec le Traefik d'un autre nœud (réglage « Ce nœud », adresses annoncées par le worker avec WORKER_ADDRESSES, liaison automatique des conteneurs, « Publier sur Traefik » vers ce Traefik, question dans make worker et dans « Nouveau nœud ») ([6631926](https://github.com/Dr-J-Watson/Armadok/commit/6631926d6181ef55490064eadd4b53c080b426fc))

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
