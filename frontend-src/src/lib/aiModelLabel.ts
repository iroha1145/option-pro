/** Show the model attached to this result; never borrow the current default. */
export function aiModelLabel(model: string | null | undefined, reasoning?: string | null): string | null {
  const id = model?.trim();
  if (!id) return null;
  const name = id === 'claude-haiku-5-5' ? 'Claude Haiku 5.5'
    : id === 'claude-sonnet-5-5' ? 'Claude Sonnet 5.5'
    : id === 'gpt-5.6-luna' ? 'GPT-5.6 Luna'
    : id === 'gpt-5.6-terra' ? 'GPT-5.6 Terra'
    : id;
  return reasoning?.trim() ? `${name} · ${reasoning.trim()}` : name;
}
