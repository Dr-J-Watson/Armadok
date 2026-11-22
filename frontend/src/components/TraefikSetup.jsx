import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Rocket, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { Btn, Field, Modal, useUi } from './ui.jsx'
import { DEFAULTS, dynamicFiles, problems, traefikCompose, traefikEnv } from '../lib/composeBuilder.js'
import { usePersist } from '../lib/persist.js'
import { useGo } from '../lib/nav.js'
import RootPicker from './RootPicker.jsx'

// Sur un hub dont la machine n'a pas de Traefik : proposer d'en créer un, sous forme de PROJET (compose + .env + configuration dynamique).
export function useTraefikOffer() {
  const { node, can } = useNode(); const [gone, setGone] = usePersist('traefik-offer-' + (node?.id || ''), false)
  const q = useQuery({ queryKey: ['traefik-status', node?.id], queryFn: () => api.get('/docker/traefik-status'), staleTime: 60000, retry: false, enabled: !!node && can('projects.import') && can('projects.edit') })
  const missing = !!q.data && q.data.local && !q.data.found
  return { missing, show: missing && !gone, dismiss: () => setGone(true), reset: () => setGone(false) }
}

const BASE = { ...DEFAULTS, tlsHttp: true, tlsDns: false, metrics: false, accessLog: true, dashboard: false, apiInternal: true, dockerProvider: false, chainPrivate: true, prometheus: false, certsRelay: false, webuiHost: 'armadok.exemple.fr' }

const Opt = ({ checked, onChange, label, help }) => (
  <label className="col" style={{ gap: 2, cursor: 'pointer' }}><span className="row" style={{ gap: 8 }}><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><b>{label}</b></span><span className="muted small" style={{ marginLeft: 24 }}>{help}</span></label>)

export function TraefikOffer({ onOpen, onDismiss }) {
  return (
    <div className="banner info" style={{ alignItems: 'flex-start' }}>
      <Rocket size={18} />
      <span className="grow"><b>Aucun Traefik détecté sur cette machine.</b> Armadok sait en créer un pour vous : un projet prêt à démarrer (HTTPS automatique, protections, journal des visiteurs), avec les options de votre choix.</span>
      <Btn kind="primary" onClick={onOpen}>Créer un projet Traefik</Btn><Btn kind="ghost" onClick={onDismiss} title="Ne plus proposer. Vous pourrez le faire plus tard : Aide → « Installer Traefik avec Armadok ».">Non merci</Btn>
    </div>)
}

export default function TraefikSetup({ onClose }) {
  const { toast } = useUi(); const qc = useQueryClient(); const go = useGo()
  const [c, setC] = useState(BASE); const [name, setName] = useState('traefik'); const [root, setRoot] = useState(''); const set = (k) => (v) => setC((x) => ({ ...x, [k]: v }))
  const issues = problems({ ...c }).filter(([lvl, t]) => lvl === 'err' && !/interface invalide/.test(t))
  const run = useMutation({
    mutationFn: async () => {
      await api.post('/docker/networks', { name: c.network, subnet: '', internal: false }).catch(() => null)   // existe déjà : tant mieux
      const files = { 'docker-compose.yml': traefikCompose(c), '.env': traefikEnv(c), ...dynamicFiles(c) }
      return api.post('/projects/create', { name, files, root })
    },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['projects'] }); qc.invalidateQueries({ queryKey: ['docker-networks'] }); toast('Projet Traefik créé : relisez-le puis démarrez-le'); onClose(); if (r.id) go(`/projects/${r.id}`) },
    onError: (e) => toast(e.message, 'err'),
  })
  return (
    <Modal wide title="Créer un projet Traefik" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={run.isPending} disabled={issues.length > 0 || !name.trim()} onClick={() => run.mutate()} title="Écrit les fichiers dans le dossier des projets ; rien n'est démarré tant que vous ne l'avez pas relu"><Rocket size={14} /> Créer le projet</Btn></>}>
      <div className="col gap-lg">
        <div className="muted">Traefik est le point d'entrée de vos sites : il reçoit les visiteurs sur les ports 80 et 443, obtient les certificats HTTPS et envoie chaque adresse vers la bonne application. Armadok l'administre ensuite (sites, protections, certificats). Les fichiers sont créés dans un nouveau projet que vous pouvez relire et modifier avant de le démarrer.</div>
        <RootPicker value={root} onChange={setRoot} />
        <div className="fgrid">
          <Field label="Nom du dossier du projet" help="Créé dans le dossier des projets de ce nœud."><input className="input mono" value={name} onChange={(e) => setName(e.target.value.trim())} /></Field>
          <Field label="Adresse e-mail pour Let's Encrypt" help="Obligatoire pour le HTTPS : Let's Encrypt s'en sert pour vous prévenir avant l'expiration d'un certificat."><input className="input" type="email" value={c.email} onChange={(e) => set('email')(e.target.value)} placeholder="vous@exemple.fr" /></Field>
          <Field label="Réseau Docker partagé" help="Traefik et vos applications doivent partager un réseau pour se joindre. Il est créé s'il n'existe pas."><input className="input mono" value={c.network} onChange={(e) => set('network')(e.target.value.trim())} /></Field>
        </div>
        <div className="col" style={{ gap: 10 }}>
          <Opt checked={c.tlsHttp} onChange={set('tlsHttp')} label="Certificats HTTPS automatiques (Let's Encrypt, défi HTTP)" help="Un vrai certificat pour chaque site, renouvelé tout seul. Demande que le port 80 de cette machine soit joignable depuis Internet. Pour un certificat joker ou un site local, choisissez plutôt le défi DNS dans le Constructeur de piles (Aide)." />
          <Opt checked={c.chainPrivate} onChange={set('chainPrivate')} label="Protection « réseau privé » et en-têtes de sécurité" help="Prépare une règle qui n'autorise que votre réseau local (et les adresses que vous ajoutez) sur les sites qui la choisissent, plus des en-têtes de sécurité usuels. Recommandé pour Armadok lui-même." />
          <Opt checked={c.accessLog} onChange={set('accessLog')} label="Journal d'accès" help="Traefik note chaque visite dans un fichier. Armadok le lit pour afficher les visiteurs, les adresses IP, la carte, les attaques probables et pouvoir les bannir. Sans lui, ces pages restent vides." />
          <Opt checked={c.apiInternal} onChange={set('apiInternal')} label="État en direct (API interne)" help="Permet à Armadok de voir la version et l'état de Traefik. Le port (8080) n'est PAS publié sur Internet, seulement sur le réseau partagé." />
          <Opt checked={c.dashboard} onChange={set('dashboard')} label="Tableau de bord Traefik" help="L'interface web propre à Traefik, derrière une adresse de votre choix. Facultatif : Armadok couvre déjà l'essentiel." />
          <Opt checked={c.metrics} onChange={set('metrics')} label="Métriques Prometheus" help="Ouvre les compteurs de Traefik à Prometheus pour les graphiques de Surveillance. À activer seulement si vous avez (ou voulez) un Prometheus." />
        </div>
        {c.dashboard && <Field label="Adresse du tableau de bord"><input className="input mono" value={c.dashboardHost} onChange={(e) => set('dashboardHost')(e.target.value.trim())} /></Field>}
        {issues.length > 0 && <div className="banner warn"><TriangleAlert size={14} /><span>{issues.map(([, t]) => t).join(' · ')}</span></div>}
        <div className="muted small">Pour aller plus loin (DNS, plusieurs résolveurs, labels Docker…), le Constructeur de piles de l'Aide produit des fichiers plus complets. Rien n'est démarré ici : vous relisez le projet, puis vous le démarrez d'un clic.</div>
      </div>
    </Modal>
  )
}
