# Changelog

## [1.18.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.17.0...v1.18.0) (2026-11-25)


### Nouveautés

* **make:** menu interactif, sauvegardes et restauration guidées ([2d3f9fe](https://github.com/Dr-J-Watson/Armadok/commit/2d3f9fee632e03fcae0d6073945acd98c3ca43b0))
* **projets:** section Modèles (dossiers, édition, import et export zip) ([6d89f6a](https://github.com/Dr-J-Watson/Armadok/commit/6d89f6a75d0e23090444bee95de4b19854f5b3c3))
* **sauvegardes:** sauvegarde complète du hub et sauvegardes par nœud ([c132c9e](https://github.com/Dr-J-Watson/Armadok/commit/c132c9eba9c93cc50e70bc06bd017cf281e55eeb))

## [1.17.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.16.0...v1.17.0) (2026-11-23)


### Nouveautés

* **projets:** explorateur de fichiers (sélection multiple, import, téléchargement), make interactif, droit « sans limite » ([41a7c0d](https://github.com/Dr-J-Watson/Armadok/commit/41a7c0de139f45cfb5fc6da8215ab7748516f33d))

## [1.16.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.15.0...v1.16.0) (2026-11-23)


### Nouveautés

* **docker:** sélection multiple (Maj+clic), actions groupées, boutons par conteneur, tri sur toutes les colonnes ([10528ef](https://github.com/Dr-J-Watson/Armadok/commit/10528efb715bfe2fb81cdc6284c99d524503c54b))

## [1.15.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.14.0...v1.15.0) (2026-11-23)


### Nouveautés

* barre latérale repliable, boutons par service, suppression avec DNS, comptes DNS par nœud ([4db1f9a](https://github.com/Dr-J-Watson/Armadok/commit/4db1f9a99e07fc3f09bab192fc73d876c1824643))

## [1.14.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.13.1...v1.14.0) (2026-11-22)


### Nouveautés

* **make:** fichier d'état et « make continue » : les réponses des installations (hub, worker, worker natif) sont gardées au fur et à mesure et reprises après une interruption ([bd55143](https://github.com/Dr-J-Watson/Armadok/commit/bd55143bdfdf777f542f8dd52dd5af4542efb4eb))
* **make:** toutes les questions de l'installation (hub, worker Docker, menu) reprises : titre, explication, exemple, invite sur sa ligne ; la couleur n'est plus écrasée par la sonde du hub ([d9fdc09](https://github.com/Dr-J-Watson/Armadok/commit/d9fdc09f441aef296c6fb4bbc526cd3764bf72fd))
* **worker natif:** commandes du quotidien (native-logs, native-restart, native-stop, native-start, native-backup), aide et README ([7cf608f](https://github.com/Dr-J-Watson/Armadok/commit/7cf608f7d3430048201e58938015665b2d9a196a))


### Corrections

* **worker natif:** le venv et l'API de Traefik deviennent de vraies questions (expliquées, mémorisées pour la reprise) ([aea3c71](https://github.com/Dr-J-Watson/Armadok/commit/aea3c71d01001aaafef7d76d253f3870256ea6ca))

## [1.13.1](https://github.com/Dr-J-Watson/Armadok/compare/v1.13.0...v1.13.1) (2026-11-22)


### Corrections

* **worker natif:** questions reformulées (titre, explication, exemple, invite sur sa ligne), acme.json accepté en dossier, adresse IP du hub non signalée, réponses gardées et reprise après interruption ([ba6abb3](https://github.com/Dr-J-Watson/Armadok/commit/ba6abb36261a6014a3872025fb131a6067e54dad))
* **worker:** la vérification du hub passe par /api/worker (POST à vide, refusé en 422 sans rien enrôler) au lieu de /api/health : plus de règle supplémentaire derrière un filtre IP ([e9ab8d5](https://github.com/Dr-J-Watson/Armadok/commit/e9ab8d5a9d8ec4e1fabee00a568b2119502df33c))

## [1.13.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.12.0...v1.13.0) (2026-11-22)


### Nouveautés

* **projets:** dossier de stockage choisi à la création (plusieurs racines), redéploiement d'une seule image, onglet Avancé (actions personnalisées en plusieurs étapes, sans shell, droit du terminal), aide et README ([5dfce19](https://github.com/Dr-J-Watson/Armadok/commit/5dfce19f26eadfd0d1dafcfa507c2fe710441a34))


### Corrections

* **projets:** l'onglet Avancé passe en dernier ([ffdbf99](https://github.com/Dr-J-Watson/Armadok/commit/ffdbf99dee0b5ec20ca3c3b79995acc2697518de))
* **projets:** le menu « … » de l'explorateur de fichiers n'est plus rogné par la liste (position fixe, retourné vers le haut si la place manque) ([c0dc979](https://github.com/Dr-J-Watson/Armadok/commit/c0dc97997f4d86adffe209a0d559ec6db29f79ac))

## [1.12.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.11.1...v1.12.0) (2026-11-22)


### Nouveautés

* worker natif sans Docker (service systemd) : vérifications et checklist, venv en option, certificats par relais, option « Sans Docker » dans Nouveau nœud, aide et README ([2d4c1dc](https://github.com/Dr-J-Watson/Armadok/commit/2d4c1dc7f9192669081b5d4446cb7d67a4e399ef))

## [1.11.1](https://github.com/Dr-J-Watson/Armadok/compare/v1.11.0...v1.11.1) (2026-11-20)


### Corrections

* **sécurité:** élargir un rôle respecte le plafond de chaque nœud où il est attribué ([aca270e](https://github.com/Dr-J-Watson/Armadok/commit/aca270edc5d182be36030afb3c16ea9032f5498f))
* **sécurité:** enregistrer un site n'écrase plus un service sans les droits Services ([55b09b1](https://github.com/Dr-J-Watson/Armadok/commit/55b09b1738dd13546a565f438b906c8925d761d1))
* **sécurité:** IP masquées dans le journal du nœud sans le droit de les voir, identifiants d'URL traités comme secrets dans les modèles de projet ([132acee](https://github.com/Dr-J-Watson/Armadok/commit/132aceeb69096bca6dcea371fd5469f9b9877fd8))
* **sécurité:** l'état en direct de Traefik ne montre que les routeurs de la portée de la personne ([f17e6fa](https://github.com/Dr-J-Watson/Armadok/commit/f17e6faa1daa49d270dd0df15ff5a9851557c3cd))
* **sécurité:** les valeurs du .env ne sortent plus (images, validation compose) sans le droit « variables .env » ([762d290](https://github.com/Dr-J-Watson/Armadok/commit/762d290e61451fd848eefb04bcdf77c0504d5327))
* **sécurité:** retirer un membre depuis la page Droits respecte les rangs et la suspension du nœud ([07614e4](https://github.com/Dr-J-Watson/Armadok/commit/07614e499f58992db16243a1c3e1fb05b36c1f8a))
* **sécurité:** un nom de service compose ne peut plus être lu comme une option (--volumes) ([cd2b9b7](https://github.com/Dr-J-Watson/Armadok/commit/cd2b9b7f3bff948db7fb04fee6f94e66ac78f151))

## [1.11.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.10.0...v1.11.0) (2026-11-20)


### Nouveautés

* cibles de Makefile avec arguments (détection des obligatoires, saisie avant lancement, transmis comme données) ([9534d97](https://github.com/Dr-J-Watson/Armadok/commit/9534d97cbd944906fb1411c63edd711e55474ec5))


### Corrections

* le bandeau « lancé avec … » disparaît quand on a choisi explicitement les fichiers compose ([5e6f21f](https://github.com/Dr-J-Watson/Armadok/commit/5e6f21f788ac7e1afef6e1b500c26d35a74211ca))

## [1.10.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.9.0...v1.10.0) (2026-11-19)


### Nouveautés

* fiche d'un projet, onglets Volumes et Réseaux (volumes, dossiers montés, réseaux, IP et alias par conteneur) ([e366c4e](https://github.com/Dr-J-Watson/Armadok/commit/e366c4e5aafb9097db17ea1b0fc8b98be0a06d72))
* liste des volumes Docker, dossiers montés (bind), colonne Type (nommé, anonyme, dossier monté), chemins dans le conteneur, filtre par type ([82733d9](https://github.com/Dr-J-Watson/Armadok/commit/82733d9c64c619e7d2680a3a29baac12e8f8f3e9))


### Corrections

* page Projets blanche (erreur React [#310](https://github.com/Dr-J-Watson/Armadok/issues/310)), le hook de navigation par dossiers était appelé après les retours anticipés ([ba8ccfd](https://github.com/Dr-J-Watson/Armadok/commit/ba8ccfdfe6b44422cf6024974450de15910b4fd1))

## [1.9.0](https://github.com/Dr-J-Watson/Armadok/compare/v1.8.0...v1.9.0) (2026-11-19)


### Nouveautés

* dossiers et sous-dossiers pour ranger les projets (nom, description, couleur), glisser-déposer, fil d'Ariane et dossier « .. » pour remonter, bouton Ranger ([ef16940](https://github.com/Dr-J-Watson/Armadok/commit/ef169401bf7987bc478d43dc3217fe0d828e40ee))
* dossiers et sous-dossiers pour ranger les sites (même fonctionnement que pour les projets) ; vue filtrée d'un nœud qui publie par un autre Traefik : ses propres dossiers, seuls ses sites sont comptés ([2469894](https://github.com/Dr-J-Watson/Armadok/commit/2469894487a44a0c00adf0800a9752a25c76d21e))


### Corrections

* bouton « Nouveau dossier » des sites placé à droite, à côté de « Nouveau site » ([62b70c4](https://github.com/Dr-J-Watson/Armadok/commit/62b70c42b479a3492414652d2c32492468e01d40))
* carte de site, un nom de domaine long est coupé par « … » au lieu de passer sous les icônes ([aeb85cf](https://github.com/Dr-J-Watson/Armadok/commit/aeb85cfb25dd44b65cc316b2ebe467f7014afc1e))

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
