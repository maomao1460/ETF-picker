// 作者：相空
/**
 * CSV 导出工具 — 纯前端, 支持中文 (BOM 头)
 */

export function exportCSV(
  filename: string,
  headers: string[],
  rows: (string | number | null | undefined)[][]
) {
  // BOM 头确保 Excel 正确识别 UTF-8 中文
  const BOM = '\uFEFF';

  const escape = (v: string | number | null | undefined): string => {
    if (v == null) return '';
    const s = String(v);
    // 包含逗号、引号、换行时用双引号包裹
    if (s.includes(',') || s.includes('"') || s.includes('\n')) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };

  const csvContent =
    BOM +
    headers.map(escape).join(',') +
    '\n' +
    rows.map((row) => row.map(escape).join(',')).join('\n');

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** 当前日期字符串 YYYY-MM-DD */
export function today(): string {
  return new Date().toISOString().slice(0, 10);
}
