import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, GitBranch, GitCommit, KeyRound, RefreshCw, Rocket, Trash2, Undo2, Webhook } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, CopyBtn, Empty, Loading, useUi } from './ui.jsx'
import { JobOutput } from './ComposeBar.jsx'
import { ago } from '../lib/features.js'

// Git d'un projet : où en est le dossier par rapport à son dépôt, récupérer les changements (jamais de fusion ni d'écrasement), revenir à une version.
export default function ProjectGit({ id, perms }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const [job, setJob] = useState(null); const [chain, setChain] = useState(false); const [fetched, setFetched] = useState(null)
  const q = useQuery({ queryKey: ['project-git', id], queryFn: () => api.get(`/projects/${id}/git`) })
  const fetchM = useMutation({ mutationFn: () => api.post(`/projects/${id}/git/fetch`), onSuccess: (r) => { setFetched(r); qc.invalidateQueries({ queryKey: ['project-git', id] }); toast(r.fetchError ? 'Dépôt injoignable' : r.behind ? `${r.behind} nouveau${r.behind > 1 ? 'x' : ''} commit${r.behind > 1 ? 's' : ''} disponible${r.behind > 1 ? 's' : ''}` : 'Déjà à jour', r.fetchError ? 'err' : 'ok') }, onError: (e) => toast(e.message, 'err') })
  const run = useMutation({ mutationFn: (body) => api.post(`/projects/${id}/git/run`, body), onSuccess: (r, v) => { setJob({ ...r, action: 'git-' + v.action }); setChain(!!v.deploy) }, onError: (e) => toast(e.message, 'err') })
  const deploy = useMutation({ mutationFn: () => api.post(`/projects/${id}/compose`, { action: 'up', build: true }), onSuccess: (r) => { setChain(false); setJob({ ...r, action: 'up' }) }, onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const s = q.data.status; const st = fetched && fetched.isRepo ? { ...s, behind: fetched.behind, ahead: fetched.ahead } : s
  if (!s.isRepo) return <div className="card"><Empty><GitBranch size={28} /><div>Ce dossier n'est pas un dépôt Git.<br /><span className="muted small">Pour un projet qui vient d'un dépôt, utilisez « Cloner un dépôt » dans la liste des projets.</span></div></Empty></div>
  const pull = (withDeploy) => run.mutate({ action: 'pull', deploy: withDeploy })
  const back = async (c) => { if (await confirm({ title: `Revenir à « ${c.subject} » ?`, message: `Le dossier passe sur la version ${c.sha} (tête détachée). Vos modifications locales ne sont pas touchées : si des fichiers suivis en ont, Git refuse. Pour reprendre la dernière version, repassez sur la branche.`, confirmLabel: 'Revenir', danger: false })) run.mutate({ action: 'checkout', ref: c.sha }) }
  return (
    <div className="col">
      <div className="card col" style={{ gap: 8 }}>
        <div className="row wrap" style={{ gap: 8 }}><GitBranch size={16} /><b>{st.detached ? 'Tête détachée' : st.branch}</b>{st.upstream && <span className="muted small">suit {st.upstream}</span>}
          {st.dirty > 0 && <Badge kind="warn" title="Fichiers suivis modifiés localement">{st.dirty} modification{st.dirty > 1 ? 's' : ''} locale{st.dirty > 1 ? 's' : ''}</Badge>}
          {st.behind > 0 && <Badge kind="info">{st.behind} en retard</Badge>}{st.ahead > 0 && <Badge>{st.ahead} en avance</Badge>}{st.behind === 0 && st.upstream && fetched && <Badge kind="ok">à jour</Badge>}</div>
        <div className="muted small mono" style={{ wordBreak: 'break-all' }}>{st.remote || 'aucun dépôt distant'}</div>
        {st.head && <div className="small"><GitCommit size={13} style={{ verticalAlign: -2 }} /> <b>{st.head.subject}</b> <span className="muted">· {st.head.sha} · {st.head.author} · {ago(st.head.time)}</span></div>}
        {fetched?.fetchError && <div className="banner err">{fetched.fetchError}</div>}
        <div className="row wrap" style={{ gap: 6 }}>
          <Btn title="Interroge le dépôt distant pour voir ce qui a changé, sans toucher aux fichiers" busy={fetchM.isPending} onClick={() => fetchM.mutate()}><RefreshCw size={14} /> Vérifier les mises à jour</Btn>
          <Btn kind={st.behind > 0 ? 'primary' : undefined} busy={run.isPending} onClick={() => pull(false)} title="git pull --ff-only : n'écrase jamais vos modifications locales"><Download size={14} /> Récupérer</Btn>
          {perms.deploy && <Btn busy={run.isPending} onClick={() => pull(true)} title="Récupère puis redéploie en reconstruisant les images"><Rocket size={14} /> Récupérer et déployer</Btn>}
          {st.detached && <Btn busy={run.isPending} onClick={() => run.mutate({ action: 'checkout', ref: 'main' })} title="Repasser sur la branche (essaie « main » ; sinon utilisez la liste)"><Undo2 size={14} /> Repasser sur main</Btn>}
        </div>
      </div>
      <div className="card flush"><div style={{ padding: '12px 16px' }}><b>Dernières versions</b></div><table className="t"><tbody>
        {q.data.commits.map((c, i) => <tr key={c.sha}><td className="mono small" style={{ width: 110 }}>{c.sha}</td><td>{c.subject}</td><td className="muted small">{c.author}</td><td className="muted small">{ago(c.time)}</td><td className="actions">{i > 0 && <Btn title="Revenir à ce commit (l'état actuel reste dans l'historique de Git)" small kind="ghost" onClick={() => back(c)}><Undo2 size={13} /> Revenir ici</Btn>}{i === 0 && <Badge>actuelle</Badge>}</td></tr>)}</tbody></table></div>
      <Credentials id={id} info={q.data} />
      <SshKey id={id} info={q.data} />
      <Hook id={id} info={q.data} perms={perms} />
      {job && <JobOutput id={id} job={job} onFinished={(code) => { if (chain && code === 0 && perms.deploy) deploy.mutate() }} onClose={() => { setJob(null); setChain(false) }} />}
    </div>
  )
}

function Credentials({ id, info }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const [u, setU] = useState(info.username || ''); const [t, setT] = useState('')
  const refresh = () => qc.invalidateQueries({ queryKey: ['project-git', id] })
  const save = useMutation({ mutationFn: () => api.put(`/projects/${id}/git/credentials`, { username: u, token: t }), onSuccess: () => { setT(''); refresh(); toast('Jeton enregistré (chiffré)') }, onError: (e) => toast(e.message, 'err') })
  const del = useMutation({ mutationFn: () => api.del(`/projects/${id}/git/credentials`), onSuccess: () => { refresh(); toast('Jeton retiré') }, onError: (e) => toast(e.message, 'err') })
  return (
    <div className="card col" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 8 }}><KeyRound size={15} /><b>Dépôt privé</b>{info.hasToken && <Badge kind="ok">jeton enregistré</Badge>}</div>
      <div className="muted small">Pour un dépôt privé en https, enregistrez un jeton d'accès (lecture seule suffit). Il est chiffré en base, transmis seulement à Git au moment d'une commande, et n'apparaît ni dans le dépôt ni dans les journaux. Pour une adresse SSH, utilisez plutôt la clé de déploiement ci-dessous.</div>
      <div className="row wrap" style={{ gap: 8 }}><input className="input" style={{ maxWidth: 200 }} placeholder="Utilisateur (facultatif)" value={u} onChange={(e) => setU(e.target.value)} /><input className="input grow" type="password" autoComplete="off" placeholder={info.hasToken ? '•••••••• (saisir pour remplacer)' : 'Jeton d\'accès'} value={t} onChange={(e) => setT(e.target.value)} />
        <Btn kind="primary" disabled={!t} busy={save.isPending} onClick={() => save.mutate()}>Enregistrer</Btn>{info.hasToken && <Btn kind="ghost" onClick={async () => { if (await confirm({ title: 'Retirer le jeton ?', message: 'Les prochaines commandes Git sur ce dépôt n\'auront plus d\'identifiants.', confirmLabel: 'Retirer' })) del.mutate() }}><Trash2 size={13} /></Btn>}</div>
    </div>
  )
}

// Clé SSH de déploiement : la partie publique se donne au dépôt (GitHub : Settings → Deploy keys ; GitLab : Settings → Repository → Deploy keys). La privée reste chiffrée sur le hub.
function SshKey({ id, info }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const k = info.ssh || {}
  const refresh = () => qc.invalidateQueries({ queryKey: ['project-git', id] })
  const make = useMutation({ mutationFn: () => api.post(`/projects/${id}/git/ssh-key`), onSuccess: () => { refresh(); toast('Clé de déploiement créée') }, onError: (e) => toast(e.message, 'err') })
  const del = useMutation({ mutationFn: () => api.del(`/projects/${id}/git/ssh-key`), onSuccess: () => { refresh(); toast('Clé retirée') }, onError: (e) => toast(e.message, 'err') })
  return (
    <div className="card col" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 8 }}><KeyRound size={15} /><b>Clé SSH de déploiement</b>{k.source === 'project' && <Badge kind="ok">clé de ce projet</Badge>}{k.source === 'node' && <Badge kind="info" title="Définie pour tout ce nœud (page Projets)">clé du nœud</Badge>}</div>
      <div className="muted small">Pour un dépôt en SSH (<code>git@github.com:moi/projet.git</code>) : donnez cette clé <b>publique</b> au dépôt en lecture seule (« Deploy key »). La clé privée est chiffrée sur le hub, jamais affichée, et n'est écrite sur la machine du projet que le temps d'une commande Git.</div>
      {k.publicKey ? <>
        <div className="codebox slim"><CopyBtn text={k.publicKey} /><pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{k.publicKey}</pre></div>
        <div className="muted small mono">{k.fingerprint}</div>
      </> : <div className="muted small">Aucune clé pour ce projet.</div>}
      <div className="row wrap" style={{ gap: 6 }}>
        <Btn kind={k.source === 'project' ? undefined : 'primary'} busy={make.isPending} onClick={async () => { if (k.source !== 'project' || await confirm({ title: 'Remplacer la clé ?', message: 'L\'ancienne clé ne sera plus utilisée : ajoutez la nouvelle au dépôt, sinon les prochaines commandes échoueront.', confirmLabel: 'Remplacer' })) make.mutate() }}><KeyRound size={14} /> {k.source === 'project' ? 'Remplacer la clé' : 'Créer une clé pour ce projet'}</Btn>
        {k.source === 'project' && <Btn kind="ghost" onClick={async () => { if (await confirm({ title: 'Retirer la clé du projet ?', message: k.source === 'project' ? 'Le projet n\'aura plus de clé propre (la clé du nœud, si elle existe, est utilisée).' : '', confirmLabel: 'Retirer' })) del.mutate() }}><Trash2 size={13} /></Btn>}
      </div>
    </div>
  )
}

// Déclenchement par le dépôt : à chaque envoi (push), le dépôt prévient ce hub, qui récupère (et redéploie si c'est demandé). Chaque appel est signé avec un secret.
function Hook({ id, info, perms }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const h = info.hook || {}; const [deploy, setDeploy] = useState(!!h.deploy); const [shown, setShown] = useState(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['project-git', id] })
  const make = useMutation({ mutationFn: () => api.post(`/projects/${id}/git/hook`, { deploy }), onSuccess: (r) => { setShown(r); refresh() }, onError: (e) => toast(e.message, 'err') })
  const upd = useMutation({ mutationFn: (d) => api.put(`/projects/${id}/git/hook`, { deploy: d }), onSuccess: refresh, onError: (e) => toast(e.message, 'err') })
  const del = useMutation({ mutationFn: () => api.del(`/projects/${id}/git/hook`), onSuccess: () => { setShown(null); refresh(); toast('Webhook supprimé') }, onError: (e) => toast(e.message, 'err') })
  const url = `${window.location.origin}${shown?.path || h.path || ''}`
  return (
    <div className="card col" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 8 }}><Webhook size={15} /><b>Récupération automatique (webhook)</b>{h.set && <Badge kind="ok">actif</Badge>}</div>
      <div className="muted small">À chaque envoi sur le dépôt, celui-ci appelle ce hub, qui fait un <code>git pull --ff-only</code> (jamais d'écrasement) et, si vous le demandez, relance <code>docker compose up -d</code>. L'appel est refusé sans la bonne signature. Le hub doit être joignable depuis le dépôt (GitHub, GitLab, Gitea).</div>
      {h.set && <div className="small"><span className="muted">Adresse à donner au dépôt :</span> <span className="mono">{url}</span> <CopyBtn text={url} />{h.lastAt && <div className="muted">Dernier appel {ago(h.lastAt)} : {h.lastResult || '—'}</div>}</div>}
      {shown && <div className="banner warn"><div><b>Secret du webhook</b> (affiché une seule fois) : <span className="mono">{shown.secret}</span> <CopyBtn text={shown.secret} /></div><div className="small">GitHub : Settings → Webhooks → « Secret », type « application/json ». GitLab : « Secret token ». Gitea : « Secret ».</div></div>}
      <div className="row wrap" style={{ gap: 8 }}>
        {perms.deploy ? <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={deploy} onChange={(e) => { setDeploy(e.target.checked); if (h.set) upd.mutate(e.target.checked) }} />Redéployer après la récupération</label> : <span className="muted small">Redéployer : droit « déployer » requis.</span>}
        <Btn kind={h.set ? undefined : 'primary'} busy={make.isPending} onClick={async () => { if (!h.set || await confirm({ title: 'Renouveler le secret ?', message: 'L\'ancien secret cesse de fonctionner : mettez le nouveau dans le dépôt.', confirmLabel: 'Renouveler' })) make.mutate() }}>{h.set ? 'Renouveler le secret' : 'Activer le webhook'}</Btn>
        {h.set && <Btn kind="ghost" onClick={async () => { if (await confirm({ title: 'Supprimer le webhook ?', message: 'Le dépôt ne pourra plus déclencher de récupération.', confirmLabel: 'Supprimer' })) del.mutate() }}><Trash2 size={13} /></Btn>}
      </div>
    </div>
  )
}
