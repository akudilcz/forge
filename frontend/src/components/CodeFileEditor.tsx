/**
 * CodeFileEditor — shared Monaco-based code editor with forge-dark theme.
 *
 * Provides VS Code-like syntax highlighting, editing, and optional
 * line decorations (used by CodeTraceView for trace overlays).
 *
 * Usage:
 *   <CodeFileEditor value={code} filePath="src/foo.py" />
 *   <CodeFileEditor value={code} filePath="src/foo.py" readOnly={false} onSave={save} />
 */

import { useRef, useCallback, useEffect, useState } from 'react';
import Editor, { type OnMount, type BeforeMount } from '@monaco-editor/react';
import type { editor as monacoEditor } from 'monaco-editor';
import { detectLanguage } from '@/hooks/useMonaco';

// ── Public types ──────────────────────────────────────────────────────────────

export interface LineDecoration {
  startLine: number;
  endLine: number;
  className: string;
  glyphClassName?: string;
  hoverMessage?: string;
  /** Inline text shown after the first line (e.g. LLR IDs) */
  inlineLabel?: string;
}

export interface CodeFileEditorProps {
  /** File content to display */
  value: string;
  /** File path — used for language detection */
  filePath: string;
  /** Read-only mode (default true) */
  readOnly?: boolean;
  /** Fired on every content change */
  onChange?: (value: string) => void;
  /** Fired on Ctrl+S */
  onSave?: (value: string) => void;
  /** Scroll to this line and highlight range */
  scrollToLine?: number | null;
  /** End of the range to highlight (defaults to scrollToLine) */
  scrollToLineEnd?: number | null;
  /** Monaco line decorations (trace overlays, etc.) */
  decorations?: LineDecoration[];
  /** Container height (default '100%') */
  height?: string | number;
  /** Callback to access the raw editor instance */
  onEditorMount?: (editor: monacoEditor.IStandaloneCodeEditor) => void;
}

// ── Theme (defined once) ──────────────────────────────────────────────────────

let themeRegistered = false;

const FORGE_THEME_NAME = 'forge-dark';

// ── Component ─────────────────────────────────────────────────────────────────

export default function CodeFileEditor({
  value,
  filePath,
  readOnly = true,
  onChange,
  onSave,
  scrollToLine,
  scrollToLineEnd,
  decorations,
  height = '100%',
  onEditorMount,
}: CodeFileEditorProps) {
  const editorRef = useRef<monacoEditor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import('monaco-editor') | null>(null);
  const decorationsRef = useRef<monacoEditor.IEditorDecorationsCollection | null>(null);

  // Keep a stable ref for onSave so the key command doesn't go stale
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  const handleBeforeMount: BeforeMount = useCallback((monaco) => {
    if (themeRegistered) return;
    themeRegistered = true;
    monaco.editor.defineTheme(FORGE_THEME_NAME, {
      base: 'vs-dark',
      inherit: true,
      rules: [],
      colors: {
        'editor.background': '#0d1117',
        'editor.foreground': '#e6edf3',
        'editorLineNumber.foreground': '#484f58',
        'editorLineNumber.activeForeground': '#e6edf3',
        'editor.selectionBackground': '#264f78',
        'editorCursor.foreground': '#58a6ff',
        'editorGutter.background': '#0d1117',
        'editor.lineHighlightBackground': '#161b2240',
      },
    });
  }, []);

  // Track mount state so decoration effects can re-fire
  const [mounted, setMounted] = useState(false);

  const handleMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;

    // Ctrl+S / Cmd+S to save
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      onSaveRef.current?.(editor.getValue());
    });

    onEditorMount?.(editor);
    setMounted(true);
  }, [onEditorMount]);

  // ── Apply decorations ───────────────────────────────────────────────────────
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;

    // Clear previous decorations
    decorationsRef.current?.clear();

    if (!decorations || decorations.length === 0) return;

    const model = editor.getModel();
    const lineCount = model?.getLineCount() ?? 0;
    const monacoDecorations: monacoEditor.IModelDeltaDecoration[] = [];
    for (const d of decorations) {
      // Skip decorations that reference lines beyond the current model
      if (d.startLine < 1 || d.startLine > lineCount) continue;
      const endLine = Math.min(d.endLine, lineCount);

      // Background tint + glyph for the full function range
      monacoDecorations.push({
        range: new monaco.Range(d.startLine, 1, endLine, 1),
        options: {
          isWholeLine: true,
          className: d.className,
          glyphMarginClassName: d.glyphClassName,
          glyphMarginHoverMessage: d.hoverMessage ? { value: d.hoverMessage } : undefined,
        },
      });
      // Inline label (LLR/CASE chips) after the function def line
      if (d.inlineLabel) {
        const lineLen = model?.getLineLength(d.startLine) ?? 1;
        monacoDecorations.push({
          range: new monaco.Range(d.startLine, lineLen + 1, d.startLine, lineLen + 1),
          options: {
            after: {
              content: `  ${d.inlineLabel}`,
              inlineClassName: 'trace-inline-label',
            },
          },
        });
      }
    }

    decorationsRef.current = editor.createDecorationsCollection(monacoDecorations);
  }, [decorations, mounted]);

  // ── Scroll to line + highlight function range ──────────────────────────────
  const highlightRef = useRef<monacoEditor.IEditorDecorationsCollection | null>(null);

  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (scrollToLine == null || !editor || !monaco) return;

    highlightRef.current?.clear();

    const totalLines = editor.getModel()?.getLineCount() ?? 0;
    if (scrollToLine < 1 || scrollToLine > totalLines) return;

    editor.revealLineInCenter(scrollToLine);

    const endLine = Math.min(scrollToLineEnd ?? scrollToLine, totalLines);

    highlightRef.current = editor.createDecorationsCollection([{
      range: new monaco.Range(scrollToLine, 1, endLine, 1),
      options: {
        isWholeLine: true,
        className: 'trace-line-selected',
      },
    }]);

    // Fade out after 4 seconds
    const timer = setTimeout(() => highlightRef.current?.clear(), 4000);
    return () => clearTimeout(timer);
  }, [scrollToLine, decorations]);

  const language = detectLanguage(filePath);
  const showGlyphMargin = (decorations?.length ?? 0) > 0;

  return (
    <Editor
      height={height}
      language={language}
      value={value}
      theme={FORGE_THEME_NAME}
      beforeMount={handleBeforeMount}
      onMount={handleMount}
      onChange={(v) => onChange?.(v ?? '')}
      loading={
        <div className="h-full flex items-center justify-center text-forge-muted text-xs font-mono animate-pulse">
          Loading editor...
        </div>
      }
      options={{
        readOnly,
        minimap: { enabled: false },
        fontSize: 12,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        lineNumbers: 'on',
        scrollBeyondLastLine: false,
        automaticLayout: true,
        wordWrap: 'on',
        glyphMargin: showGlyphMargin,
        folding: true,
        renderLineHighlight: readOnly ? 'none' : 'line',
        cursorBlinking: 'smooth',
        smoothScrolling: true,
        padding: { top: 8, bottom: 8 },
        domReadOnly: readOnly,
        contextmenu: !readOnly,
        tabSize: 4,
        insertSpaces: true,
        bracketPairColorization: { enabled: true },
      }}
    />
  );
}
