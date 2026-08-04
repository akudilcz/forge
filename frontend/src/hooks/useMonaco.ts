/**
 * useMonaco — Monaco editor utilities.
 *
 * detectLanguage maps file extensions to Monaco language IDs.
 * The actual editor component lives in @/components/CodeFileEditor.
 */

/** Detect Monaco language from a file extension or explicit override. */
export function detectLanguage(filePathOrLang: string): string {
  const ext = filePathOrLang.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    py: 'python',
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    md: 'markdown',
    toml: 'ini',
    sh: 'shell',
    bash: 'shell',
    sql: 'sql',
    html: 'html',
    css: 'css',
  };
  return map[ext] ?? filePathOrLang;
}
