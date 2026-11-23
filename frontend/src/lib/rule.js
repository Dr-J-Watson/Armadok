// Parseur / générateur de règles Traefik (Host(`a`) && !PathPrefix(`/x`) …) pour le constructeur en blocs.
const TOKEN = /\s*(&&|\|\||!|\(|\)|,|`[^`]*`|"[^"]*"|'[^']*'|[A-Za-z_][A-Za-z0-9_]*)/y

function tokenize(rule) {
  const out = []
  TOKEN.lastIndex = 0
  rule = rule.trim()
  let pos = 0
  while (pos < rule.length) {
    TOKEN.lastIndex = pos
    const m = TOKEN.exec(rule)
    if (!m) throw new Error('syntaxe')
    out.push(m[1])
    pos = TOKEN.lastIndex
    while (pos < rule.length && /\s/.test(rule[pos])) pos++
  }
  return out
}

export function parseRule(rule) {
  const t = tokenize(rule || '')
  let i = 0
  const peek = () => t[i]
  const take = (e) => { const x = t[i]; if (x === undefined || (e && x !== e)) throw new Error('syntaxe'); i++; return x }
  const or = () => { const n = [and()]; while (peek() === '||') { take(); n.push(and()) } return n.length === 1 ? n[0] : { op: 'or', n } }
  const and = () => { const n = [not()]; while (peek() === '&&') { take(); n.push(not()) } return n.length === 1 ? n[0] : { op: 'and', n } }
  const not = () => { if (peek() === '!') { take(); return { op: 'not', n: not() } } return atom() }
  const atom = () => {
    if (peek() === '(') { take(); const n = or(); take(')'); return n }
    const fn = take()
    take('(')
    const args = []
    while (peek() !== ')') { const a = take(); args.push(/^[`"']/.test(a) ? a.slice(1, -1) : a); if (peek() === ',') take() }
    take(')')
    return { op: 'fn', fn, args }
  }
  if (!t.length) throw new Error('vide')
  const n = or()
  if (i !== t.length) throw new Error('syntaxe')
  return n
}


// ------------------------------------------------------------------ règle <-> lignes
// L'éditeur montre une LISTE de lignes, indentées comme du code : une ligne indentée FAIT PARTIE de la ligne du dessus.
// Une ligne et ses lignes indentées forment un bloc (des parenthèses). Chaque ligne a :
//   op    : son lien avec ce qui la précède (la ligne du dessus, ou la ligne indentée précédente du même bloc) : ET ou OU, qu'on bascule d'un clic
//   level : son indentation
//     Host(A)
//     OU Host(B)
//          ET PathPrefix(/x)        ->  Host(A) || (Host(B) && PathPrefix(/x))
//     Host(A)
//     ET PathPrefix(/a)
//          OU PathPrefix(/b)        ->  Host(A) && (PathPrefix(/a) || PathPrefix(/b))
// Sans indentation, la priorité habituelle s'applique (ET avant OU) ; la règle écrite met alors des parenthèses autour des ET pour que ce soit lisible.
let seq = 0
export const rowId = () => 'r' + ++seq
export const hostRow = (value = '', op = 'and', level = 0) => ({ id: rowId(), kind: 'host', value, op, level })
export const condRow = (fn, args = [''], op = 'and', level = 0) => ({ id: rowId(), kind: 'cond', fn, args, neg: false, op, level })

// Forme « non » ramenée aux feuilles (De Morgan) : !(a && b) devient !a || !b, pour que seules les conditions portent un NON
function nnf(a, neg) {
  if (a.op === 'not') return nnf(a.n, !neg)
  if (a.op === 'fn') {
    if (a.fn === 'Host' && a.args.length > 1) return nnf({ op: 'or', n: a.args.map((x) => ({ op: 'fn', fn: 'Host', args: [x] })) }, neg)
    return { ...a, neg }
  }
  return { op: neg ? (a.op === 'and' ? 'or' : 'and') : a.op, n: a.n.map((x) => nnf(x, neg)) }
}

// Éléments d'un groupe, mis à plat : un groupe de même opérateur, ou un ET dans un OU, se lit à plat (la priorité du ET suffit) ;
// un OU dans un ET reste un élément à part (un bloc). Chaque élément porte son lien avec le précédent.
function items(node) {
  const out = []
  node.n.forEach((child, i) => {
    const link = i === 0 ? null : node.op
    if (child.op === 'fn' || !(child.op === node.op || (child.op === 'and' && node.op === 'or'))) out.push({ link, el: child })
    else items(child).forEach((it, k) => out.push({ link: k === 0 ? link : it.link, el: it.el }))
  })
  return out
}

const leafRow = (node, level, op) => (node.fn === 'Host' && !node.neg ? hostRow(node.args[0] || '', op, level) : { ...condRow(node.fn, node.args, op, level), neg: !!node.neg })

// Émet un élément (ligne, ou bloc = ligne racine + lignes indentées). Renvoie false si le bloc n'a pas de ligne racine possible.
function emitEl(el, level, op, out) {
  if (el.op === 'fn') { out.push(leafRow(el, level, op)); return true }
  let its = items(el)
  if (its[0].el.op !== 'fn') {                                   // un bloc commence par une ligne : on remonte la première ligne simple…
    const k = its.findIndex((x) => x.el.op === 'fn')
    if (k < 0 || new Set(its.slice(1).map((x) => x.link)).size > 1) return false   // …sauf si l'ordre compte (ET et OU mélangés)
    const links = its.slice(1).map((x) => x.link); const root = its[k]
    its = [root, ...its.filter((_, j) => j !== k)].map((x, j) => ({ ...x, link: j === 0 ? null : links[0] }))
  }
  if (!emitEl(its[0].el, level, op, out)) return false
  return its.slice(1).every((x) => emitEl(x.el, level + 1, x.link, out))
}

// Règle -> lignes ; null si la règle ne se lit pas (elle reste alors éditable en texte)
export function ruleToRows(rule) {
  if (!rule || !rule.trim()) return []
  let ast
  try { ast = parseRule(rule) } catch { return null }
  ast = nnf(ast, false)
  const out = []
  if (ast.op === 'fn') return [leafRow(ast, 0, 'and')]
  const its = items(ast)
  return its.every((x, k) => emitEl(x.el, 0, k === 0 ? 'and' : x.link, out)) ? fix(out) : null
}

// Lignes vides retirées, niveaux cohérents : la première ligne n'est pas indentée, on ne descend jamais de plus d'un cran
export function fix(rows) {
  return clampLevels(rows.filter((r) => (r.kind === 'host' ? r.value : r.args[0])))
}
// Même cohérence des niveaux, sans retirer les lignes vides (utile pendant l'édition)
export const clampLevels = (rows) => rows.reduce((out, r, i) => [...out, { ...r, level: Math.max(0, Math.min(r.level, i === 0 ? 0 : out[i - 1].level + 1)) }], [])

const leaf = (r) => (r.kind === 'host' ? 'Host(`' + r.value + '`)' : `${r.neg ? '!' : ''}${r.fn}(${r.args.map((a) => '`' + a + '`').join(', ')})`)

// Suite d'éléments reliés par ET / OU : les ET sont mis entre parenthèses quand ils côtoient un OU (priorité du ET, rendue visible)
function join(list) {
  const segs = [[]]
  list.forEach((x, k) => { if (k > 0 && x.op === 'or') segs.push([]); segs[segs.length - 1].push(x.expr) })
  return segs.map((t) => (segs.length > 1 && t.length > 1 ? `(${t.join(' && ')})` : t.join(' && '))).join(' || ')
}

export function rowsToRule(rows) {
  const rs = fix(rows)
  // Une ligne + les lignes indentées qui la suivent. Renvoie [expression sans parenthèses, ligne suivante, a des enfants]
  const block = (i) => {
    const kids = []
    let j = i + 1
    while (j < rs.length && rs[j].level > rs[i].level) { const [expr, next, has] = block(j); kids.push({ op: rs[j].op, expr: has ? `(${expr})` : expr }); j = next }
    return [kids.length ? join([{ op: 'and', expr: leaf(rs[i]) }, ...kids]) : leaf(rs[i]), j, kids.length > 0]
  }
  const top = []
  for (let i = 0; i < rs.length;) { const [expr, next, has] = block(i); top.push({ op: rs[i].op, expr, has }); i = next }
  return top.length === 1 ? top[0].expr : join(top.map((x) => ({ op: x.op, expr: x.has ? `(${x.expr})` : x.expr })))
}

export const rowsHosts = (rows) => rows.filter((r) => r.kind === 'host' && r.value).map((r) => r.value)
