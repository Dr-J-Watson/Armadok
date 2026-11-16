// docker-compose.yml / pile Portainer d'un worker, prêt à copier. Les options décrivent la machine du Traefik distant.
const q = (s) => JSON.stringify(String(s))
const isPath = (s) => /^(\/|\.\/|\.\.\/)/.test(s)

export const WORKER_DEFAULTS = { dyn: '', net: '', api: '', puid: '1000', pgid: '1000', certs: false, acme: '', acmeFile: 'acme.json', data: 'worker_data', modules: 'traefik', dgid: '984', roots: '', logs: '', logFile: 'access.log', exec: false }

// Ce que gère un worker : « traefik » (Traefik + DNS, le défaut), « docker » (conteneurs et projets compose) ou « both ».
export const WORKER_TYPES = [{ id: 'traefik', label: 'Traefik + DNS', hint: 'Les sites, protections et certificats du Traefik de cette machine (le DNS reste géré par le hub).' }, { id: 'docker', label: 'Docker seul', hint: 'Les conteneurs et projets compose de cette machine, sans Traefik.' }, { id: 'both', label: 'Les deux', hint: 'Traefik ET Docker : la liaison complète sur cette machine.' }]
export const rootsOf = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean)

// Image du worker : la MÊME version que le hub (le protocole entre les deux évolue avec les versions)
export const imageFor = (version) => `ghcr.io/dr-j-watson/armadok:${/^v?\d/.test(version || '') ? version.replace(/^v/, '') : 'latest'}`

export function composeFor({ hub, token, version, ...o }) {
  o = { ...WORKER_DEFAULTS, ...o }
  const image = imageFor(version)
  const vols = []  // volumes nommés/externes à déclarer
  const tr = o.modules !== 'docker'; const dk = o.modules !== 'traefik'
  const dyn = o.dyn || '/chemin/vers/traefik-dynamic'
  let dynMount = `${dyn}:/traefik-dynamic`
  if (tr && o.dyn && !isPath(o.dyn)) { dynMount = 'dynamic:/traefik-dynamic'; vols.push(`  dynamic:\n    external: true\n    name: ${q(o.dyn)}`) }
  vols.push('  worker_data:')
  const insecure = hub.startsWith('http://') ? '      WORKER_ALLOW_INSECURE: "1"                 # hub en HTTP : réseau de confiance uniquement\n' : ''
  let certEnv = '', certVol = '', relay = ''
  if (tr && o.certs) {
    let src = `${o.acme || '/chemin/vers/dossier-acme'}:/certs-in:ro`
    if (o.acme && !isPath(o.acme)) { src = 'certsin:/certs-in:ro'; vols.push(`  certsin:\n    external: true\n    name: ${q(o.acme)}`) }
    vols.push('  certs_meta:')
    certEnv = '      TRAEFIK_CERTS_FILE: /traefik-certs/certs.json\n'
    certVol = '      - certs_meta:/traefik-certs:ro\n'
    relay = `
  # Relais des certificats : lit acme.json (root, aucun réseau, lecture seule) et n'écrit que les noms et dates, jamais les clés.
  certs:
    image: ${image}
    container_name: armadok-certs-relay
    command: ["python", "-m", "app.certsync"]
    restart: unless-stopped
    user: "0:0"
    network_mode: none
    read_only: true
    cap_drop: [ALL]
    cap_add: [DAC_READ_SEARCH]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 64m
    healthcheck: { disable: true }
    environment:
      CERTSYNC_IN: /certs-in/${o.acmeFile || 'acme.json'}
    volumes:
      - ${src}
      - certs_meta:/certs-out
`
  }
  let logEnv = '', logVol = ''
  if (tr && o.logs) {   // journal d'accès : lu par le worker et envoyé au hub (statistiques de visiteurs de ce nœud)
    let src = `${o.logs}:/traefik-logs:ro`
    if (!isPath(o.logs)) { src = 'tlogs:/traefik-logs:ro'; vols.push(`  tlogs:\n    external: true\n    name: ${q(o.logs)}`) }
    logEnv = `      TRAEFIK_ACCESS_LOG: /traefik-logs/${o.logFile || 'access.log'}   # lu ICI, envoyé au hub : statistiques et adresses IP de ce nœud\n`
    logVol = `      - ${src}\n`
  }
  const roots = rootsOf(o.roots)
  const dockerEnv = dk ? `      DOCKER_SOCKET: /var/run/docker.sock\n      PGROUPS: "${o.dgid}"                          # groupe du socket Docker (stat -c %g /var/run/docker.sock) : le worker le garde en abandonnant ses droits\n      PROJECT_ROOTS: ${roots.length ? roots.join(',') : '/chemin/vers/projets'}\n${o.exec ? '      WORKER_ALLOW_EXEC: "true"                  # autorise le terminal dans les conteneurs depuis le hub (droit « docker.exec »)\n' : ''}` : ''
  const dockerVols = dk ? `      - /var/run/docker.sock:/var/run/docker.sock   # ATTENTION : accès équivalent à root sur cette machine\n${(roots.length ? roots : ['/chemin/vers/projets']).map((r) => `      - ${r}:${r}                         # même chemin dedans et dehors\n`).join('')}` : ''
  const net = tr && o.net ? '    networks: [proxy]\n' : ''
  const netDecl = tr && o.net ? `\nnetworks:\n  proxy:\n    external: true\n    name: ${q(o.net)}\n` : ''
  return `services:
  armadok-worker:
    image: ${image}
    container_name: armadok-worker
    command: ["python", "-m", "app.drop", "python", "-m", "app.worker"]
    restart: unless-stopped
    user: "0:0"                                  # démarre en root uniquement pour donner /data à PUID:PGID, puis abandonne ses droits
    cap_add: [CHOWN, SETUID, SETGID]
    environment:
      PUID: "${o.puid}"                          # ${tr && dk ? 'doit pouvoir écrire dans la config de Traefik et dans les dossiers des projets' : dk ? 'doit pouvoir écrire dans les dossiers des projets' : 'doit pouvoir écrire dans la config dynamique de Traefik'}
      PGID: "${o.pgid}"
      HUB_URL: ${hub}
      ENROLL_TOKEN: ${token}                    # utilisé une seule fois, à retirer ensuite
${insecure}${dk ? `      WORKER_MODULES: ${tr ? 'traefik,docker' : 'docker'}\n` : ''}${tr ? `      TRAEFIK_DYNAMIC_DIR: /traefik-dynamic\n` : ''}      DATA_DIR: /data
${tr ? (o.api ? `      TRAEFIK_API_URL: ${o.api}\n` : '      # TRAEFIK_API_URL: http://traefik:8080     # facultatif : statut en direct\n') : ''}${certEnv}${logEnv}${dockerEnv}    volumes:
${tr ? `      - ${dynMount}                             # la config dynamique (file provider) de ce Traefik\n` : ''}      - worker_data:/data                       # identité du worker + sauvegardes
${certVol}${logVol}${dockerVols}    healthcheck:
      test: ["CMD", "python", "-m", "app.worker", "--health"]
      interval: 30s
      timeout: 5s
      retries: 3
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 256m
${net}${relay}${netDecl}
volumes:
${vols.join('\n')}
`
}
