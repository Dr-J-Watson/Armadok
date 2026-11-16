# Branche `docker-manager` : gestion de Docker dans l'interface

Fusion de deux projets : cette interface (DNS, Traefik, droits, nœuds) et **Docknair** (`../better_portenair` : projets Compose, conteneurs, images, Git).
Branche **locale**, jamais poussée ni fusionnée avant le feu vert de l'utilisateur.

## Idée directrice : une seule application, deux mondes reliés

Ce n'est pas « Portainer à côté de l'interface Traefik » : c'est **Docker ajouté à une application qui existe déjà**, où chaque objet Docker et chaque objet Traefik se renvoient l'un à l'autre.

### Liaison entre nœuds (un conteneur sur A, publié par le Traefik de B)

Cas courant : une machine « Docker seul » dont les conteneurs sont servis par le Traefik d'un **autre** nœud. Les noms de conteneur ne se résolvent pas d'une machine à l'autre : le Traefik de B vise `http://adresse-de-A:port-publié`. Le moteur doit donc raisonner autrement :

| Sur le nœud Docker (A) | Sur le nœud Traefik (B) |
|---|---|
| ports **publiés** de chaque conteneur (`adresse hôte : port hôte`) et **adresses de la machine** | l'URL d'un service : `http://192.168.1.20:8080` |

**Lien automatique** quand l'hôte de l'URL est une adresse de A *et* le port est un port publié par le conteneur (« sûr »), ou seulement l'adresse (« probable »). **Lien manuel** en repli : sur un conteneur, « servi par le site X du nœud B » (mémorisé, avec l'identité de la personne qui l'a fait).

À décider avec les adresses : le worker rapporte les adresses de sa machine (interfaces), et chaque nœud garde en réglage des adresses supplémentaires (nom DNS, adresse VPN/WireGuard) pour les cas que l'auto-détection ne voit pas.

Conséquences :
- **Droits** : voir le lien exige de pouvoir voir **les deux nœuds** (sinon on voit le conteneur sans son site, ou l'inverse).
- **Statistiques et IP** : elles viennent du journal d'accès du Traefik **de B**. Aujourd'hui ce journal n'est lu que sur le nœud principal : pour les nœuds distants, il faudra que le worker Traefik le lise et le transmette (travail à part, noté ici pour ne pas l'oublier).
- Même nœud ou non, l'onglet Projets affiche la même chose : la liaison est simplement trouvée « sur ce nœud » ou « sur le nœud B ».

### Le nouvel onglet principal : « Projets »

Les onglets actuels restent (Sites, Certificats, IP…). S'ajoute un onglet **plus central**, **Projets**, qui devient la porte d'entrée d'un nœud où Docker est disponible. Tout fonctionne **par nœud**, comme aujourd'hui (pas de sélecteur global) : on est sur un nœud, on voit ses projets.

**La liste.** Même forme que la page **Sites** : des **cartes** (avec la même bascule cartes / liste compacte, mémorisée), mais **3 par ligne** sur ordinateur (au lieu de `minmax(320px, 1fr)` en `auto-fill`, une grille de 3 colonnes), pour avoir la place d'afficher correctement les informations ; 2 colonnes sur tablette, 1 sur téléphone. Chaque carte = un projet Compose : état, services (en marche / arrêtés), CPU et mémoire, et ses **statistiques générales** (requêtes, erreurs, visiteurs sur la période, pays principal, attaques), le site qui lui correspond, une alerte si quelque chose cloche. Onglets Gérés / Découverts / Ignorés et import en un clic (comme Docknair).

**La fiche d'un projet.** Tout ce qui le concerne au même endroit :

| Onglet | Contenu | Disponible si le nœud a… |
|---|---|---|
| Vue d'ensemble | état, actions (déployer, redémarrer, pull, arrêter, down), stats d'accès, services avec leur site | `docker` (les stats et les sites demandent aussi `traefik`) |
| Services | chaque service : état, CPU/mémoire, logs, terminal, **le routeur / site Traefik qui y mène** | `docker` (+ `traefik` pour les sites) |
| Fichiers | compose, `.env`, Dockerfiles, éditeur, diff, historique ; **labels Traefik lus comme des règles** | `docker` |
| Logs | en direct, filtrables par service | `docker` |
| Accès | **les IP qui y ont accédé**, requêtes, erreurs, pays, attaques ; clic vers la fiche IP | `docker` + `traefik` |
| Sites | les sites Traefik de ce projet, ouverts dans leur page habituelle | `docker` + `traefik` |
| Git, Images, Historique, Droits | comme Docknair | `docker` |

**L'affichage s'adapte** : sur un nœud « Docker seul », il n'y a ni Sites ni Accès ni statistiques réseau, mais tout le reste ; sur un nœud « Traefik seul », l'onglet Projets n'existe pas. Aucun bouton grisé qui ne mène nulle part : ce qui n'est pas disponible n'est pas affiché.

### Le fil qui relie un conteneur à Traefik

```
conteneur ──(labels  traefik.http.routers.X.*)────────────► routeur X@docker ─┐
conteneur ──(nom:port dans l'URL d'un service YAML)───────► service ◄─ routeur ┤
                                                                               ▼
                                                              règle Host(`site.exemple.fr`)
                                                                               ▼
                                         visites par site (ip_paths) · IP · pays · erreurs · attaques
```

Trois façons de trouver quel routeur mène à un conteneur : ses **labels** (fournisseur Docker), le **nom du conteneur et son port** dans l'URL d'un service d'un fichier YAML (fournisseur fichier, le cas des sites créés ici), et le **réseau Docker** partagé avec Traefik. Le plus fiable gagne, le reste est signalé « probable ».

### Ce que la liaison apporte (à garder en tête dans CHAQUE étape)

| Depuis… | On voit / on peut… |
|---|---|
| **un conteneur** | les routeurs Traefik qui y mènent (règle, protections, entrée, certificat) avec un lien vers chacun ; l'état du site (répond-il ?) ; ses **statistiques d'accès** (requêtes, erreurs, pays) ; les **IP qui y ont accédé**, cliquables vers la fiche IP ; ses attaques détectées |
| **un site / une règle Traefik** | le conteneur qui le sert (état, journaux, redémarrer) et son projet Compose |
| **une IP** | les conteneurs (services) qu'elle a touchés |
| **un projet Compose** | pour chaque service, ses labels Traefik **lus comme des règles** (lisibles, avec protections) ; conversion **labels ⇄ fichier YAML** dans les deux sens ; « publier ce service sur Traefik » en deux clics |
| **un bannissement / une alerte** | le conteneur ou le site concerné |
| **un conteneur sans règle** | « Aucun site n'y mène » avec un bouton pour en créer un (préremplit nom, réseau, port exposé) |

### Labels ⇄ YAML

Les labels `traefik.http.routers.X.rule=…` sont la même chose qu'un fichier du dossier dynamique, aplatie en clés pointées. Ici, c'est déjà notre modèle (`kind`, `name`, `spec`) : la conversion est mécanique et réversible. À prévoir :
- **labels → fichier `zz-…yml`** (par exemple pour sortir un site des labels) ; **fichier → labels** (pour les remettre dans le compose) ;
- avertir quand le fournisseur Docker de Traefik **n'est pas activé** (les labels ne servent alors à rien) ;
- avertir des **doublons** (même routeur en label et en fichier) et des références cassées (middleware inconnu).

### Modules par nœud (pour les workers)

Un nœud porte des **modules**, ce qui donne les trois types de worker :

| Module | Ce qu'il apporte | Nécessite sur la machine |
|---|---|---|
| `traefik` | sites, middlewares, fichiers YAML, certificats, journal d'accès, bannissements (+ le DNS, qui vit sur le hub) | le dossier dynamique de Traefik |
| `docker` | conteneurs, images, réseaux, volumes, projets Compose, Git | le socket Docker |

| Type de worker | `WORKER_MODULES` | Usage |
|---|---|---|
| Complet | `traefik,docker` | Traefik **et** ses conteneurs : **la liaison complète** |
| Docker seul | `docker` | une machine à gérer, sans Traefik (pas de liaison, ou vers le Traefik d'un autre nœud plus tard) |
| Traefik seul | `traefik` | l'existant (le défaut : rien ne change pour les workers actuels) |

Le **nœud local** (le hub) a les modules que sa machine permet : `traefik` si le dossier dynamique est monté, `docker` si le socket l'est.

Conséquences, dès la première étape :
- la base garde les modules du nœud (`nodes.modules`) ; le worker les **annonce** dans son `hello`, le hub **refuse** toute opération hors de ses modules ;
- l'interface masque les pages d'un module absent ; **la liaison n'apparaît que si les deux modules sont là** ;
- les droits sont groupés par module (un nœud sans `docker` n'affiche jamais les droits Docker) ; voir un lien exige de pouvoir voir **les deux côtés** ;
- le protocole worker garde deux listes fermées d'opérations (`OPS_TRAEFIK`, `OPS_DOCKER`), jamais de commande libre ;
- le **DNS reste sur le hub** (API OVH) : « Traefik + DNS » signifie « un nœud avec le module `traefik` ».

## Sécurité (Docker = presque root sur la machine)

- Le module `docker` est **désactivé par défaut** et s'active par nœud, avec le socket monté **volontairement**.
- Droits séparés et fins (voir, agir, journaux, terminal, supprimer, projets…), chacun pouvant exiger la **2FA** (mécanisme « 2FA par droit » existant).
- Tout est **audité** (chaîne d'audit existante) ; l'accès d'urgence garde son avis au propriétaire.
- Jamais d'exécution libre côté worker : opérations nommées, arguments validés, dossiers de projet limités à des **racines autorisées** par nœud.
- Le terminal dans un conteneur est le droit le plus sensible : 2FA conseillée par défaut, session tracée.

## Étapes

La liaison n'est pas une étape à la fin : **chaque étape livre sa part de liaison**. Les écrans de Docknair sont **réécrits dans le style de l'application** (mêmes composants, mêmes tableaux, même navigation), pas copiés tels quels.

| # | Contenu | Liaison livrée avec |
|---|---|---|
| 0 ✅ | **Fondations** : `nodes.modules`, annonce par le worker, filtrage des opérations, menu et droits par module, `WORKER_MODULES` (assistant « Nouveau nœud », Makefile) ; **moteur de liaison** conteneur ↔ routeur ↔ site ↔ statistiques ; **adaptation de l'interface aux modules** | aucun changement visible, moteur testé |
| 1 ✅ | **Docker du nœud** : conteneurs (liste, démarrer / arrêter / redémarrer, journaux en direct, stats), images, réseaux, volumes, nettoyage | fiche d'un conteneur : ses règles Traefik, son site, ses accès et ses IP |
| 2 ✅ | **Onglet Projets** : découverte, import, liste avec **stats générales**, fiche (vue d'ensemble, services, logs, accès/IP, sites) | le projet relie ses services, leurs conteneurs, leurs sites et leurs visiteurs |
| 3 ✅ | **Fichiers du projet** : compose, `.env`, Dockerfiles, éditeur, diff, historique, déploiement (up / down / pull / build), modèles | labels lus comme des règles ; **labels ⇄ YAML** ; vérifications croisées |
| 4 ✅ | **Publier sur Traefik** : créer un site depuis un conteneur ou un service (préremplit réseau, port, nom) ; avertir si le fournisseur Docker de Traefik est absent | création ⇄ liaison dans les deux sens |
| 5 ✅ | **Terminal, suivi des images, Git**, notifications | alertes et bannissements qui renvoient au projet |
| 6 ✅ | **Workers Docker** : opérations Docker par le canal worker, types « Docker seul » et « Complet » | liaison complète sur un worker Complet |

## Reprise depuis Docknair

À reprendre en l'adaptant, pas à recopier : `docker_ops.py`, `docker_info.py`, `docker_run.py`, `compose_scan.py`, `projects_fs.py`, `templates.py`, `gitsync.py` / `git_ops.py`, `watchers.py` (suivi d'images), les écrans Conteneurs / Logs / Terminal / Git.
Déjà présent ici et **non repris** : authentification, 2FA, audit, droits, sauvegardes, enrôlement des workers, accès d'urgence.

## État de l'étape 0 (faite)

- **Modules** : colonne `nodes.modules` ; le worker les annonce dans son `hello` (`WORKER_MODULES`, défaut `traefik`) ; le hub les enregistre et **refuse** (409) toute opération hors des modules du nœud ; le worker refuse aussi (400). Le nœud local : `traefik` toujours, `docker` seulement si **`DOCKER_MANAGE=true` ET** le socket est monté (le socket seul, monté pour les certificats, n'active rien).
- **Interface** : `useNode()` expose `modules`, `hasModule(m)`, `hasDocker` ; sans le module Traefik, plus de Sites, Middlewares, Surveillance, Traefik avancé, « Nouveau site », ni de statistiques (les fonctionnalités sont coupées côté serveur) ; le DNS reste (il vit sur le hub).
- **Moteur de liaison** : `backend/app/linkage.py`, fonctions pures (labels et URL de service ⇄ routeurs, sens inverse, hôtes), testées sur des données fictives. L'accès réel à Docker vient à l'étape 1.
- **Volontairement pas encore exposé** : `WORKER_MODULES` dans l'assistant « Nouveau nœud » et le Makefile (un worker « Docker seul » ne pourrait encore rien faire : arrive à l'étape 6), `DOCKER_MANAGE` dans `.env.example` (arrive avec les pages Docker, étape 1).

## État final de la branche

Les sept étapes sont faites, testées (381 tests) et vérifiées **contre le vrai Docker** (liste, journaux, statistiques, terminal, déploiement d'un conteneur jetable, comparaison d'images avec le registre, worker « Docker seul » lancé depuis la pile générée).

| Étape | Livré |
|---|---|
| 0 | modules par nœud, annonce par le worker, interface adaptée, moteur de liaison |
| 1 | conteneurs, journaux, stats, images, réseaux, volumes, nettoyage ; droits `docker.*` ; protections (interface, Traefik) |
| 2 | onglet Projets (cartes 3 par ligne), fiche projet (vue d'ensemble, services, logs, accès et IP, sites) |
| 3 | fichiers (éditeur, diff, historique, `.env` protégé), déploiement compose en tâche de fond, suppression, **labels ⇄ YAML** et contrôles croisés |
| 4 | publier sur Traefik depuis un conteneur ; détection du fournisseur Docker de Traefik |
| 5 | terminal (xterm), Git (état, pull sans écrasement, retour à une version, clonage, jeton chiffré), suivi des images + notifications |
| 6 | workers Docker (3 types, pile générée), liaison entre nœuds (adresses + ports publiés), installateur (`make install`, `make doctor`), README, aide |

### Ce qui n'est volontairement PAS fait

- **Git** : pas de sondage automatique des dépôts (le webhook signé le remplace), pas de clé SSH protégée par mot de passe.
- **Suivi des images** : registres publics seulement.
- **Ordres longs sur un worker** : une requête du hub expire à 25 s (le nettoyage de beaucoup d'images peut dépasser).

### Ajouts après les sept étapes

| Ajout | Détail |
|---|---|
| `make worker` / `worker-stack` | demande le type (Traefik, Docker, les deux), le journal d'accès, les dossiers des projets, le terminal ; `make doctor` vérifie aussi un worker Docker |
| Journal d'accès d'un nœud distant | le worker lit `TRAEFIK_ACCESS_LOG` et envoie les lignes brutes au hub (message `access`, 2000 lignes et 8 Ko par ligne au plus) ; le hub les range sous le nœud ; rafales (alertes) séparées par nœud ; alertes et règles automatiques tournent pour chaque nœud qui a un journal |
| Terminal sur un worker | session tenue par le worker (`docker_exec.py`, 5 au plus, 15 min d'inactivité), le hub lit par courtes attentes ; refusé sans `WORKER_ALLOW_EXEC=true` ; shell fixe |
| Git en SSH | clé Ed25519 par projet ou par nœud (`gitssh.py`), chiffrée en base, écrite 0600 le temps d'une commande puis effacée, `known_hosts` en confiance au premier contact |
| Webhook | `POST /api/hooks/git/{projet}` signé (GitHub, Gitea, GitLab), pull `--ff-only`, redéploiement facultatif, un appel / 10 s, verrouillage après 10 échecs |

## Décisions prises

- Tout reste **par nœud** (pas de sélecteur global comme Docknair).
- Un onglet **Projets** devient le centre d'un nœud qui a Docker ; l'onglet **Sites** reste.
- L'affichage **s'adapte aux modules** du nœud : on ne montre que ce qui est disponible.
- Les écrans de Docknair sont **adaptés au style de l'application**.
- Hub d'abord, workers ensuite (mais le modèle des trois types est posé dès l'étape 0).

## Points ouverts (tranchés)

- Racines des projets : **`PROJECT_ROOTS`**, montées avec le même chemin ; c'est aussi la limite dure de ce que l'interface lit et écrit.
- Conteneur de l'interface / de Traefik : arrêt ou suppression demandent de **retaper le nom** ; l'interface ne peut ni s'arrêter ni se supprimer.
- Où mettre Docker : entrées « Projets » et « Docker » dans le menu du nœud, avec des liens croisés partout.
- Nœuds entre eux : **adresses de la machine** réglées dans « Ce nœud » (pas d'auto-détection).
- Un défaut de Docknair à ne pas reprendre (page Conteneurs qui plante en ouverture directe) : évité.
