import { Activity, Boxes, Cloud, Globe, KeyRound, Layers, LayoutDashboard, Lock, Server, UserCog, Users, Workflow, Wrench } from 'lucide-react'

// Contenu « pour débuter » de l'aide : parcours de démarrage, guide page par page, lexique, questions fréquentes.
// `need` : condition d'affichage d'un lien (traefik, dns, accounts, monitor, admin, rights, node, nodes) ; sans `need`, toujours visible.
// `to` : page de l'interface ; `action` : 'new-site' (ouvre l'assistant) ou 'profile' (ouvre « Mon compte »).

export const START = [
  { id: 'st-dns', t: 'Ajoutez votre fournisseur DNS', d: 'Le DNS est l\'annuaire qui dit « mon-site.exemple.fr se trouve à telle adresse ». Dites à l\'interface où est le vôtre (OVH, DuckDNS, AdGuard Home, Pi-hole, ou un domaine en DNS local) : elle créera les adresses de vos sites à votre place.',
    more: 'Pour OVH, il faut une clé d\'API limitée (l\'aide « DNS » explique laquelle). Si vous n\'avez pas de fournisseur, vous pouvez aussi déclarer un domaine « en DNS local » et gérer l\'adresse vous-même.', to: '/accounts', label: 'Ouvrir « Comptes DNS »', need: 'accounts' },
  { id: 'st-domains', t: 'Vérifiez que vos domaines apparaissent', d: 'Dans « Domaines », vous devez voir chaque domaine et ses enregistrements (les lignes de l\'annuaire). Si un domaine manque, revenez à l\'étape précédente.', to: '/dns', label: 'Ouvrir « Domaines »' },
  { id: 'st-site', t: 'Créez votre premier site', d: 'Un « site » relie trois choses : une adresse (blog.exemple.fr), éventuellement des protections, et l\'application qui répond (par exemple http://192.168.1.20:3000). L\'assistant vous guide pas à pas et peut créer l\'enregistrement DNS en même temps.',
    more: 'Une application dans Docker se joint avec le nom de son conteneur (http://mon-app:3000) si elle est dans le même réseau que Traefik. Une machine de votre réseau se joint avec son adresse (http://192.168.1.20:3000).', action: 'new-site', label: 'Créer un site', need: 'traefik' },
  { id: 'st-test', t: 'Vérifiez qu\'il répond', d: 'Sur la carte du site, le menu à trois points (⋯) propose « Tester le site » : il contrôle d\'un coup le nom (DNS), le certificat et la réponse. Une réponse 401 ou 403 est normale pour un site protégé.', to: '/sites', label: 'Ouvrir « Sites »', need: 'traefik' },
  { id: 'st-cert', t: 'Contrôlez le certificat HTTPS', d: 'Le petit cadenas du navigateur vient d\'un certificat que Traefik obtient tout seul, mais il peut mettre quelques minutes. Un point orange sur « Traefik avancé » signale un certificat à régler.', to: '/traefik?tab=certs', label: 'Voir les certificats', need: 'traefik' },
  { id: 'st-2fa', t: 'Protégez votre compte avec la double authentification', d: 'En plus du mot de passe, un code à 6 chiffres généré par une application sur votre téléphone. Si quelqu\'un découvre votre mot de passe, il ne pourra rien faire sans ce code. Notez bien les codes de secours.', action: 'profile', label: 'Activer la 2FA' },
  { id: 'st-watch', t: 'Jetez un œil à la surveillance', d: 'Si Prometheus et le journal d\'accès sont branchés, vous voyez le trafic, les erreurs, et les adresses qui frappent à votre porte, avec la possibilité de les bloquer.', to: '/monitoring', label: 'Ouvrir « Surveillance »', need: 'monitor' },
  { id: 'st-team', t: 'Invitez quelqu\'un, avec les droits qu\'il faut', d: 'Chaque personne a un compte et des droits précis (voir sans modifier, gérer un domaine, tout gérer…). Les droits sensibles peuvent exiger la double authentification.', to: '/node?tab=members', label: 'Ouvrir les membres', need: 'node' },
]

export const GUIDE = [
  { id: 'g-home', icon: LayoutDashboard, t: 'Accueil', to: '/', d: 'Le tableau de bord : ce qui demande votre attention, en un coup d\'œil.',
    points: ['Les cartes du haut comptent vos sites, vos domaines et les problèmes ; chacune est cliquable.', 'La ligne suivante montre les alertes en cours, les adresses bannies et les certificats à régler.', '« À surveiller » liste les incohérences de configuration : cliquez une ligne pour ouvrir le site concerné.', '« Activité récente » est le journal de ce qui a été fait (par qui, quand) : cliquez une ligne pour y aller.'] },
  { id: 'g-sites', icon: Workflow, t: 'Sites', to: '/sites', need: 'traefik', d: 'Une carte par site. C\'est la page où l\'on passe le plus de temps.',
    points: ['« Nouveau site » lance l\'assistant (adresse, protections, destination, DNS).', 'Le bouton « Modifier », ou un clic sur la carte, ouvre le site.', 'L\'interrupteur met le site en pause : Traefik cesse de le servir, rien n\'est supprimé, c\'est réversible.', 'Menu à trois points (⋯) : copier l\'adresse, ouvrir, tester, statistiques, visiteurs, certificat, enregistrement DNS, dupliquer.', 'Les pastilles disent l\'essentiel : « Sans protection », « DNS manquant », certificat (cliquez-la pour le détail).'],
    tip: 'Pour créer un site presque identique à un autre, utilisez « Dupliquer ce site » dans le menu à trois points (⋯).' },
  { id: 'g-mw', icon: Layers, t: 'Middlewares', to: '/middlewares', need: 'traefik', d: 'Les protections et transformations appliquées avant d\'envoyer la requête à l\'application.',
    points: ['Exemples : demander un mot de passe, n\'autoriser que le réseau local, ajouter des en-têtes de sécurité, limiter le débit.', 'Un middleware se crée une fois et s\'applique à autant de sites que vous voulez.', 'La page montre qui utilise chaque protection avant que vous la coupiez.'] },
  { id: 'g-dns', icon: Globe, t: 'Domaines (DNS)', to: '/dns', d: 'Les enregistrements de chaque domaine : à quelle adresse mène chaque nom.',
    points: ['Choisissez le domaine en haut, filtrez, triez les colonnes.', 'Chez OVH, les modifications sont mises en attente puis « appliquées » (un bandeau le rappelle) ; chez DuckDNS, AdGuard Home ou Pi-hole, c\'est immédiat.', 'La colonne « Traefik » montre le site qui utilise l\'adresse, ou propose d\'en créer un.', '« Accès » partage le domaine ; « Outils » : import/export, historique et restauration, DNSSEC.', 'Les boutons « Mettre en pause les sites » coupent d\'un coup tous les sites d\'un domaine sans toucher au DNS.'] },
  { id: 'g-acc', icon: Cloud, t: 'Comptes DNS', to: '/accounts', need: 'accounts', d: 'Où l\'interface retrouve vos fournisseurs DNS et vos domaines.',
    points: ['Ajoutez un compte OVH, DuckDNS, AdGuard Home ou Pi-hole, ou un domaine en DNS local.', 'Les clés sont chiffrées dans la base ; vous ne les revoyez jamais en clair.', 'Un domaine peut être partagé en lecture ou en modification avec d\'autres personnes.'] },
  { id: 'g-certs', icon: Lock, t: 'Certificats HTTPS', to: '/traefik?tab=certs', need: 'traefik', d: 'Les certificats que Traefik a obtenus : domaines, émetteur, échéance, sites concernés.',
    points: ['« À surveiller » liste les sites sans certificat ou dont le certificat expire.', 'Cochez plusieurs certificats pour les supprimer ou les renouveler d\'un coup (Traefik est alors arrêté quelques secondes ; une sauvegarde d\'acme.json est faite avant).', 'Le filtre cherche un domaine ou un site ; cliquez un en-tête pour trier.'],
    tip: 'Un certificat expiré qui traîne est souvent le reste d\'un site supprimé : vous pouvez le retirer sans risque.' },
  { id: 'g-traefik', icon: Wrench, t: 'Traefik avancé', to: '/traefik', need: 'traefik', d: 'L\'édition complète de la configuration, pour les cas que l\'assistant ne couvre pas.',
    points: ['Onglets : routeurs, services, middlewares, TLS, certificats, fichiers, problèmes.', 'Chaque fichier modifié garde une sauvegarde : vous pouvez voir l\'historique et restaurer.', 'L\'onglet « Fichiers » permet de ranger en dossiers (le rangement automatique ignore les fichiers qui commencent par « zz »).']},
  { id: 'g-mon', icon: Activity, t: 'Surveillance', to: '/monitoring', need: 'monitor', d: 'Trafic, erreurs, alertes, adresses IP et blocage. Les onglets apparaissent selon ce qui est branché.',
    points: ['« Vue d\'ensemble » : requêtes, erreurs, lenteur, carte des visiteurs. Chaque chiffre mène au détail.', '« Sites » : un tableau par site ; cliquez-en un pour ses statistiques (période réglable, personnalisable).', '« Alertes » : règles prêtes à l\'emploi, historique, et « sourdine » 1 h ou 24 h pour faire taire un message sans perdre l\'alerte.', '« Adresses IP » : qui se connecte, avec pays, attaques probables, et boutons Valider / Bannir (un à un ou plusieurs d\'un coup). Export CSV.', '« Bannies » : les adresses qui reçoivent un 403 ; vous pouvez changer la durée.', '« Automod » : des règles qui bannissent automatiquement (par pays, adresse suspecte, type d\'attaque…). Commencez en mode observation.', '« Validées » : les adresses que vous reconnaissez (vous, un collègue, un outil) ; elles ne sont plus marquées.', '« Explorateur » : n\'importe quelle mesure Prometheus, en questions toutes prêtes, constructeur ou PromQL.'],
    tip: 'Pas de « Surveillance » dans le menu ? Soit rien n\'est branché, soit vos droits ne le permettent pas : voir « Fonctionnalités facultatives ».' },
  { id: 'g-node', icon: Server, t: 'Ce nœud', to: '/node', need: 'node', d: 'Les réglages de l\'instance Traefik courante.',
    points: ['Réglages : nom, adresses par défaut, Prometheus, webhook d\'alerte.', 'Membres : qui a accès, avec quel rôle, éventuellement pour une durée limitée.', 'Sécurité : les droits qui exigent la double authentification.', 'Connexion : pour un nœud distant, l\'installation et l\'état du worker.'] },
  { id: 'g-rights', icon: KeyRound, t: 'Droits', to: '/rights', need: 'rights', d: 'Qui peut faire quoi.',
    points: ['Des rôles prêts à l\'emploi (lecteur, éditeur, administrateur…) et la possibilité d\'en composer.', 'Les attributions peuvent être limitées dans le temps.'] },
  { id: 'g-team', icon: Users, t: 'Équipe', to: '/team', need: 'admin', d: 'Les comptes de l\'interface (administrateurs de la plateforme).',
    points: ['Créer un compte (mot de passe de 12 caractères au minimum), le désactiver, réinitialiser son mot de passe ou sa 2FA.', 'Réinitialiser un mot de passe ou une 2FA déconnecte aussitôt les appareils de la personne.'] },
  { id: 'g-nodes', icon: Boxes, t: 'Tous les nœuds', to: '/nodes', need: 'admin', d: 'La vue d\'ensemble de la plateforme.',
    points: ['Tous les nœuds, leurs propriétaires et leur état ; cliquez le nombre de membres ou de domaines pour y aller.', 'Accès d\'urgence tracé et notifié, et actions rapides (suspendre, bannir un propriétaire…).'] },
  { id: 'g-me', icon: UserCog, t: 'Mon compte', action: 'profile', d: 'Cliquez votre nom en bas du menu.',
    points: ['Activer ou retirer la double authentification, récupérer vos avis.', 'Changer votre mot de passe : vos autres appareils sont alors déconnectés.', '« Me déconnecter partout ailleurs » coupe les autres sessions (téléphone, autre navigateur).'] },
]

export const TRICKS = [
  ['Un chiffre, un nom de site, une adresse', 'Presque tout ce qui est souligné ou en forme de carte se clique et mène au détail. Un bouton « ← Retour » vous ramène d\'où vous veniez.'],
  ['Passer la souris sur une valeur', 'Une petite icône « copier » apparaît à côté des adresses IP, noms d\'hôte et enregistrements.'],
  ['Le menu à trois points (⋯) d\'un site', 'Copier l\'adresse, tester, statistiques, visiteurs, certificat, DNS, dupliquer.'],
  ['« Annuler » après une action', 'Après un bannissement, une validation, une pause ou une sourdine, un bouton « Annuler » reste affiché quelques secondes.'],
  ['Trier un tableau', 'Cliquez sur l\'en-tête d\'une colonne (domaines, certificats, services).'],
  ['Vos réglages sont retenus', 'La période choisie, le tri et la vue de la liste d\'adresses sont gardés d\'une visite à l\'autre, dans ce navigateur.'],
]

export const GLOSSARY = [
  ['Traefik', 'Le « portier » : il reçoit toutes les visites sur les ports 80 et 443 et les envoie vers la bonne application, en gérant le HTTPS.'],
  ['Site', 'Dans cette interface : une adresse (blog.exemple.fr) reliée à une application. Côté Traefik, c\'est un routeur et un service.'],
  ['Routeur', 'La règle qui dit « les visites pour telle adresse vont vers tel service ». Exemple : Host(`blog.exemple.fr`).'],
  ['Service', 'L\'application qui répond : une adresse et un port (http://192.168.1.20:3000), ou un conteneur Docker.'],
  ['Middleware', 'Une protection ou un traitement appliqué en chemin : mot de passe, liste d\'adresses autorisées, en-têtes de sécurité, redirection…'],
  ['Entrypoint (point d\'entrée)', 'Une porte d\'entrée de Traefik : « web » (port 80) et « websecure » (port 443, HTTPS).'],
  ['DNS', 'L\'annuaire d\'Internet : il transforme un nom (blog.exemple.fr) en adresse IP.'],
  ['Enregistrement DNS', 'Une ligne de l\'annuaire. A : nom → adresse IPv4. AAAA : nom → adresse IPv6. CNAME : un nom qui en suit un autre. TXT : un texte (vérifications, SPF…).'],
  ['Certificat HTTPS', 'Ce qui prouve que le site est le bon et chiffre l\'échange (le cadenas). Traefik les obtient gratuitement auprès de Let\'s Encrypt.'],
  ['Résolveur de certificats', 'La méthode qu\'utilise Traefik pour obtenir un certificat : par HTTP (site public) ou par DNS (site privé, ou certificat « joker »).'],
  ['Certificat joker (wildcard)', 'Un seul certificat valable pour tous les sous-domaines d\'un domaine (*.exemple.fr). Il exige la validation par DNS.'],
  ['Pause d\'un site', 'Traefik cesse de servir le site sans rien supprimer. Rien n\'est perdu, on peut reprendre à tout moment.'],
  ['Nœud', 'Une instance de Traefik gérée par l\'interface, avec ses sites, ses membres et ses domaines. Le nœud « Principal » est celui de cette machine.'],
  ['Worker', 'Le petit programme installé sur une autre machine pour que l\'interface pilote le Traefik qui s\'y trouve (nœud distant).'],
  ['Double authentification (2FA)', 'Un code à 6 chiffres, renouvelé toutes les 30 secondes par une application de votre téléphone, demandé en plus du mot de passe.'],
  ['Journal d\'accès', 'Le fichier où Traefik note chaque visite. L\'interface le lit pour lister les adresses IP, leur pays et leurs tentatives d\'attaque.'],
  ['Prometheus', 'Un outil qui garde en mémoire les mesures de Traefik (requêtes, erreurs, lenteur) pour tracer les courbes.'],
  ['Adresse validée', 'Une adresse IP que vous reconnaissez : elle n\'est plus marquée suspecte et ne peut pas être bannie.'],
  ['Bannissement', 'L\'adresse reçoit une erreur 403 sur tous les sites du nœud, pour une durée choisie ou définitivement.'],
  ['Automod', 'Des règles qui bannissent toutes seules les adresses qui remplissent des conditions (pays, suspecte, type d\'attaque…).'],
  ['Alerte en sourdine', 'L\'alerte reste affichée mais n\'envoie plus de message au webhook pendant la durée choisie.'],
]

export const FAQ = [
  { q: 'Mon site ne s\'ouvre pas. Par où commencer ?', a: 'Utilisez « Tester le site » (menu à trois points (⋯) de la carte) : il dit si le nom, le certificat ou la réponse pose problème. Si c\'est le nom, vérifiez l\'enregistrement DNS (et son délai de propagation). Si c\'est la réponse, l\'application derrière ne répond pas : vérifiez son adresse et son port.', link: ['diag', 'Voir le dépannage détaillé'] },
  { q: 'Le navigateur affiche « certificat non sécurisé » ou « connexion non privée ».', a: 'Le certificat n\'est pas encore obtenu, ou a expiré. Ouvrez l\'onglet Certificats : le site apparaît dans « À surveiller », avec un bouton pour le redemander. Comptez quelques minutes après la création d\'un site.', link: ['certs', 'Aide sur les certificats'] },
  { q: 'Comment couper un site sans le supprimer ?', a: 'Sur la carte du site, l\'interrupteur le met en pause (réversible). Pour tous les sites d\'un domaine, utilisez « Mettre en pause les sites » dans la page Domaines. Un bouton « Annuler » reste affiché après l\'action.' },
  { q: 'Que devient le DNS si je supprime un site ?', a: 'Rien : seul le site est retiré de Traefik. L\'enregistrement DNS reste, vous pouvez le supprimer à part dans « Domaines ».' },
  { q: 'Comment bloquer une adresse, ou tout un pays ?', a: 'Surveillance → Adresses IP : « Bannir » sur une ligne, ou cochez-en plusieurs. Pour un pays, cliquez un point de la carte puis « Ce pays » : cela crée une règle d\'automod, en observation d\'abord pour vérifier ce qu\'elle bloquerait.', link: ['options', 'Aide sur la surveillance'] },
  { q: 'Je reçois trop de messages d\'une alerte.', a: 'Dans l\'onglet Alertes, cliquez « 1 h » ou « 24 h » à droite de la ligne : plus de message au webhook, l\'alerte reste visible. Vous pouvez aussi relever le seuil de la règle ou l\'allonger (durée minimale).' },
  { q: 'J\'ai perdu mon téléphone (double authentification).', a: 'Connectez-vous avec un code de secours, saisi à la place du code à 6 chiffres. Sans code de secours, un administrateur de la plateforme peut réinitialiser votre 2FA (Équipe).' },
  { q: 'Pourquoi je ne vois pas telle page ou tel bouton ?', a: 'Chaque personne a des droits précis, et les pages non branchées (Surveillance, Certificats) sont masquées. Demandez au propriétaire du nœud de vous donner le droit voulu. Si un bandeau parle de double authentification, activez-la dans « Mon compte » : le droit revient aussitôt.' },
  { q: 'Comment revenir en arrière après une mauvaise modification ?', a: 'Chaque fichier Traefik enregistré garde une sauvegarde : Traefik avancé → Fichiers → historique, puis restaurer. Pour le DNS, la page Domaines → Outils garde l\'historique des zones.' },
  { q: 'Où sont mes données et comment les sauvegarder ?', a: 'Comptes, droits et clés sont dans la base de l\'interface, dans le dossier /data du conteneur (à sauvegarder avec votre fichier .env). Les fichiers Traefik sont dans le dossier dynamique de Traefik.', link: ['armadok', 'Aide sur cette interface'] },
  { q: 'Comment mettre l\'interface à jour ?', a: 'Dans le dossier du projet : récupérez la nouvelle image puis relancez. Rien n\'est perdu, la base se met à jour toute seule au démarrage.', code: 'docker compose pull && docker compose up -d' },
]
