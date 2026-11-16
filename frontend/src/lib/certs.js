// Aide « certificats » : ce que Traefik (pas cette interface) doit avoir pour obtenir un certificat HTTPS pour un domaine.
const RESOLVERS = '        resolvers: ["1.1.1.1:53", "8.8.8.8:53"]   # DNS publics : évite que votre DNS local brouille la vérification'

const yaml = (provider) => `# traefik.yml (configuration statique de Traefik)
certificatesResolvers:
  letsencrypt:
    acme:
      email: vous@exemple.fr
      storage: /letsencrypt/acme.json
      dnsChallenge:
        provider: ${provider}
${RESOLVERS}`

const wildcard = (zone) => `# Sur un routeur : un seul certificat pour tout le domaine (joker)
tls:
  certResolver: letsencrypt
  domains:
    - main: "${zone}"
      sans: ["*.${zone}"]`

export function certHelp(provider, zone) {
  if (provider === 'ovh') return {
    title: 'Certificat par défi DNS chez OVH',
    intro: 'Traefik crée lui-même un enregistrement _acme-challenge temporaire dans la zone pour prouver qu\'il contrôle le domaine. Aucun sous-domaine public n\'est nécessaire : cela marche même si vos sites ne sont résolus que par votre DNS local.',
    env: ['OVH_ENDPOINT=ovh-eu', 'OVH_APPLICATION_KEY=…', 'OVH_APPLICATION_SECRET=…', 'OVH_CONSUMER_KEY=…'],
    envNote: 'À mettre dans l\'environnement du conteneur Traefik. Ce sont ses propres clés : celles de cette interface ne lui sont pas transmises (créez-lui une clé avec le droit de modifier la zone).',
    yaml: yaml('ovh'), wildcard: wildcard(zone),
  }
  if (provider === 'duckdns') return {
    title: 'Certificat par défi DNS chez DuckDNS',
    intro: 'Traefik pose un TXT temporaire sur votre nom DuckDNS pour prouver qu\'il le contrôle : il ne faut donc aucun accès entrant depuis Internet, et votre site peut rester sur une adresse privée.',
    env: ['DUCKDNS_TOKEN=…'],
    envNote: 'À mettre dans l\'environnement du conteneur Traefik (même jeton que celui enregistré ici, mais Traefik le lit de son côté). DuckDNS ne gère qu\'un TXT à la fois par nom : un certificat joker et un certificat simple ne doivent pas être demandés en même temps.',
    yaml: yaml('duckdns'), wildcard: wildcard(zone),
  }
  return {
    title: 'Certificats pour un domaine en DNS local',
    intro: 'Ce domaine est résolu par votre propre DNS, donc rien ne permet à Let\'s Encrypt de le vérifier directement. Trois solutions courantes :',
    bullets: [
      'Le domaine est aussi enregistré chez un fournisseur supporté par Traefik (OVH, DuckDNS, Cloudflare…) : utilisez son défi DNS dans Traefik (voir les modèles OVH / DuckDNS). Le DNS local et le public peuvent différer : aucun enregistrement public n\'est requis pour les sites.',
      'Une autorité interne (step-ca, mkcert, votre CA) : déclarez le certificat dans Traefik (tls.certificates) ou un résolveur ACME vers cette autorité.',
      'Si le site est joignable depuis Internet sur les ports 80 / 443 : le défi HTTP de Traefik suffit, sans compte DNS.',
    ],
    yaml: yaml('ovh'), wildcard: wildcard(zone), generic: true,
  }
}
