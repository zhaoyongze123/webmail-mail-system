export function AdminTextPreview({ title, text }: { title: string; text: string }) {
  if (!text) {
    return null;
  }

  return (
    <div className="admin-info-card admin-text-preview">
      <strong>{title}</strong>
      <pre className="admin-mono">{text}</pre>
    </div>
  );
}
