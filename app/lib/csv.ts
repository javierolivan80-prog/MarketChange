// csv.ts — exportación a CSV genérica, client-side, sin dependencias.
// Un solo escapado correcto (comillas, comas, saltos de línea) reutilizado
// por cualquier tabla de la app, en vez de que cada botón de export
// reimplemente su propio join(",").
export function downloadCsv(filename: string, headers: string[], rows: (string | number | null)[][]) {
  const escape = (value: string | number | null): string => {
    if (value === null || value === undefined) return "";
    const s = String(value);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers, ...rows].map((row) => row.map(escape).join(","));
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
