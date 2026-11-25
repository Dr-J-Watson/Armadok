import { useEffect, useRef, useState } from 'react'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import Editor, { DiffEditor, loader } from '@monaco-editor/react'

self.MonacoEnvironment = { getWorker: () => new editorWorker() }

// Langages que Monaco ne connaît pas : Makefile et fichiers .env (coloration simple : commentaires, clés, variables, cibles, chaînes).
if (!monaco.languages.getLanguages().some((l) => l.id === 'makefile')) {
  monaco.languages.register({ id: 'makefile' })
  monaco.languages.setMonarchTokensProvider('makefile', { tokenizer: { root: [
    [/^\s*#.*$/, 'comment'],
    [/^\.[A-Z_]+(?=\s*:)/, 'keyword'],
    [/^\s*(ifeq|ifneq|ifdef|ifndef|else|endif|include|-include|define|endef|export|override)\b/, 'keyword'],
    [/^[A-Za-z0-9_.\-\/%]+(?=\s*:(?!=))/, 'type.identifier'],
    [/^[A-Za-z_][\w.]*(?=\s*[:+?]?=)/, 'variable'],
    [/\$[({][^)}]+[)}]/, 'variable.predefined'],
    [/\$\$?[@<^?*%]/, 'variable.predefined'],
    [/"[^"]*"|'[^']*'/, 'string'],
    [/\s#.*$/, 'comment'],
  ] } })
  monaco.languages.setLanguageConfiguration('makefile', { comments: { lineComment: '#' }, brackets: [['(', ')'], ['{', '}']] })
}
if (!monaco.languages.getLanguages().some((l) => l.id === 'dotenv')) {
  monaco.languages.register({ id: 'dotenv' })
  monaco.languages.setMonarchTokensProvider('dotenv', { tokenizer: {
    root: [[/^\s*#.*$/, 'comment'], [/^\s*(export\s+)?[A-Za-z_][\w.]*(?==)/, 'type.identifier'], [/=/, 'delimiter', '@value']],
    value: [[/\$\{[^}]*\}|\$[A-Za-z_]\w*/, 'variable.predefined'], [/"([^"\\]|\\.)*"/, 'string'], [/'[^']*'/, 'string'], [/\s#.*$/, 'comment'], [/$/, '', '@pop'], [/./, 'string']],
  } })
  monaco.languages.setLanguageConfiguration('dotenv', { comments: { lineComment: '#' } })
}
loader.config({ monaco }) // Monaco servi localement, aucune requête vers un CDN

function useTheme() {
  const get = () => (document.documentElement.dataset.theme === 'dark' ? 'vs-dark' : 'vs')
  const [t, setT] = useState(get)
  useEffect(() => { const h = () => setT(get()); window.addEventListener('themechange', h); return () => window.removeEventListener('themechange', h) }, [])
  return t
}

const OPTS = { minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, tabSize: 2, insertSpaces: true, automaticLayout: true, renderWhitespace: 'boundary', padding: { top: 8 }, fontFamily: 'ui-monospace, Menlo, Consolas, monospace' }

export default function CodeEditor({ value, onChange, language = 'yaml', height = '100%', markers = [], readOnly, onSave }) {
  const theme = useTheme()
  const ed = useRef(); const onSaveRef = useRef(onSave)
  onSaveRef.current = onSave
  useEffect(() => {
    if (!ed.current) return
    const model = ed.current.getModel()
    monaco.editor.setModelMarkers(model, 'lint', markers.map((m) => ({ severity: monaco.MarkerSeverity.Error, message: m.message, startLineNumber: m.line, startColumn: 1, endLineNumber: m.line, endColumn: 1000 })))
  }, [markers])
  return (
    <Editor height={height} language={language} theme={theme} value={value} options={{ ...OPTS, readOnly }} onChange={(v) => onChange?.(v ?? '')}
      onMount={(e) => { ed.current = e; e.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => onSaveRef.current?.()) }} />
  )
}

export function Diff({ original, modified, height = '60vh', language = 'yaml' }) {
  const theme = useTheme()
  return <DiffEditor height={height} language={language} theme={theme} original={original} modified={modified} keepCurrentOriginalModel keepCurrentModifiedModel options={{ ...OPTS, readOnly: true, renderSideBySide: true }} />
}
