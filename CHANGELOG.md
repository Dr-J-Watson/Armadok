# Changelog

## [1.8.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.7.0...v1.8.0) (2026-11-19)


### Nouveautés

* boutons d'action par service dans un projet (redémarrer, arrêter, démarrer, redéployer selon l'état), en icônes avec infobulle ([f7b6a07](https://github.com/Dr-J-Watson/Armadok/commit/f7b6a07f12db75aa37af8de18d3776100a386f6b))
* limite de bannissements d'IP portée à 2000 par défaut, réglable par nœud depuis l'onglet Bannies, avec un compteur ([2006d34](https://github.com/Dr-J-Watson/Armadok/commit/2006d34228cc292abe90c79a41cf9a6d5d00d84e))
* liste des bannies avec recherche, tri et colonne de date de ban ; limite de bannissements illimitée ou chiffrée, avec choix du comportement au débordement (refuser, ou lever d'anciens bannissements d'automod par temps restant ou par date) ([3653176](https://github.com/Dr-J-Watson/Armadok/commit/3653176862dc4b9c76b3600c51533589ae94a448))


### Corrections

* un fichier compose choisi pour le lancement qui n'existe plus ne bloque plus le projet — le choix est oublié et la règle automatique reprend (à l'ouverture comme au lancement) ([e7e0149](https://github.com/Dr-J-Watson/Armadok/commit/e7e0149e7164c0380fadb6649be41a83ddc240db))

## [1.7.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.6.0...v1.7.0) (2026-11-18)


### Nouveautés

* cliquer sur une image d'un projet ouvre son détail (couches, variables, ports, conteneurs qui l'utilisent) ([7a6f80b](https://github.com/Dr-J-Watson/Armadok/commit/7a6f80b6bc6fb5471623680a2987de185ec15f75))

## [1.6.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.5.0...v1.6.0) (2026-11-18)


### Nouveautés

* planning de vérification des images à règles multiples (durée en j/h/min/s, chaque jour, jours de la semaine, jours du mois, premier lundi du mois, une fois, expression cron) ; la version d'une image écrite ${VAR} est lue dans le .env du projet ([c014b8d](https://github.com/Dr-J-Watson/Armadok/commit/c014b8de35db21f58cb026c4039deab9419005e9))


### Corrections

* la Surveillance d'un nœud qui publie par le Traefik d'un autre ne montre plus que ses sites (métriques, services, journal d'accès, adresses IP, alertes) ([da2e048](https://github.com/Dr-J-Watson/Armadok/commit/da2e048499ff4bfee2ca7c6089044eb043d0006b))
* le conteneur auxiliaire de relance d'Armadok se supprime tout seul en s'arrêtant (AutoRemove), et les restes d'anciennes relances sont nettoyés au démarrage ([4ef1fec](https://github.com/Dr-J-Watson/Armadok/commit/4ef1fec188bbe019960d38d78b341bad2544f414))
* une image écrite ${VAR:-défaut} est résolue côté hub avec sa valeur par défaut, même quand le worker n'a pas encore la lecture du .env ([3ec03ea](https://github.com/Dr-J-Watson/Armadok/commit/3ec03ea42d9188cac760ba87c1be06416263cc90))

## [1.5.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.4.1...v1.5.0) (2026-11-18)


### Nouveautés

* en affichage complet, la liste des sites indique vers quel nœud chaque site mène (ou l'adresse externe : box, autre machine) ([77dfa51](https://github.com/Dr-J-Watson/Armadok/commit/77dfa5142323ed7f3a4553873af45cc49191c4bb))
* suivi des images — lien vers la page du paquet (ghcr.io, Docker Hub, Quay), détection d'une version plus haute que le tag courant, et planning au choix (toutes les N heures ou jours, certains jours de la semaine, un jour du mois) ([576d367](https://github.com/Dr-J-Watson/Armadok/commit/576d3670600b8a7602d916fa9120282fcfcef841))

## [1.4.1](https://github.com/Dr-J-Watson/Armadok/compare/v1.4.0...v1.4.1) (2026-11-17)


### Corrections

* relancer depuis l'interface le projet qui contient Armadok lui-même (worker ou hub) ne laisse plus le conteneur arrêté — la commande compose est confiée à un conteneur auxiliaire éphémère ; le worker s'arrête tout de suite sur SIGTERM au lieu d'attendre 10 s ([753a919](https://github.com/Dr-J-Watson/Armadok/commit/753a91974794a6fe85cac813cda8ca91c5f61493))

## [1.4.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.3.0...v1.4.0) (2026-11-17)


### Nouveautés

* « Affichage complet » dans le sélecteur de nœud — Docker (conteneurs, images, réseaux, volumes), Projets et Sites listent tous les nœuds à la fois avec une colonne « Nœud » ; un clic ouvre l'élément sur son nœud ([6309766](https://github.com/Dr-J-Watson/Armadok/commit/6309766f9ca5501a16fbf6be8261e3460020e6cd))
* changer de nœud en restant sur la même page (fiches propres à un nœud : retour à la liste), raccourcis g puis 1-9 / n / p pour naviguer entre les nœuds, numéros dans le sélecteur, « Aller au nœud… » dans la recherche ([1079a6c](https://github.com/Dr-J-Watson/Armadok/commit/1079a6cfea2d7aaf2a398194f4b80d5a2adc79e6))
* un nœud sans Traefik qui publie via un autre Traefik reprend toutes les pages Traefik de ce nœud (Sites, Middlewares, Traefik avancé, Surveillance) filtrées sur ses conteneurs, avec un bouton « Tout voir » ; tests : horodatages relatifs (une date figée sortait de la fenêtre de 7 jours) ([90b82d3](https://github.com/Dr-J-Watson/Armadok/commit/90b82d3a287645da952f27cb9a4227688d5e4d19))

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
