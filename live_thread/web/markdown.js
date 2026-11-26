/**
 * Lightweight, XSS-safe Markdown renderer for Live Thread Q&A cards, summaries, and session history.
 * Supports headings (h1-h4), nested ul/ol lists, task lists, section-group bullets,
 * aligned pipe tables, fenced code blocks with language labels, blockquotes, and inline formatting.
 */

export const esc = s =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export function fmtInline(s) {
  const codes = [];
  let out = esc(s).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(`<code>${c}</code>`);
    return `\u0000${codes.length - 1}\u0000`;
  });
  out = out.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`,
  );
  out = out.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[\s(—–-])__(.+?)__(?=$|[\s.,;:!?)\u2014\u2013-])/g, '$1<strong>$2</strong>');
  out = out.replace(/~~(.+?)~~/g, '<del>$1</del>');
  out = out.replace(/(^|[\s(—–-])\*([^*\n]+?)\*(?=$|[\s.,;:!?)\u2014\u2013-])/g, '$1<em>$2</em>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => codes[Number(i)]);
}

const LIST_RE = /^([ \t]*)(?:([-*•])|(\d+)[.)])\s+(.*)$/;
const TASK_RE = /^\[([ xX])\]\s+(.*)$/;
const GROUP_TITLE_RE = /^\*\*[^*\n]+\*\*:?$/;
const SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
const HR_RE = /^\s*([-*_])\s*\1\s*\1[\s\1]*$/;
const HEADING_RE = /^\s*(#{1,4})\s+(.+)/;
const FENCE_RE = /^\s*```([a-zA-Z0-9_+-]*)\s*$/;
const QUOTE_RE = /^\s*>\s?/;

function indentWidth(ws) {
  return ws.replace(/\t/g, '  ').length;
}

function parseListLine(line) {
  const m = line.match(LIST_RE);
  if (!m) return null;
  let text = m[4];
  let task = null;
  const tm = text.match(TASK_RE);
  if (tm) {
    task = tm[1].toLowerCase() === 'x';
    text = tm[2];
  }
  return {
    indent: indentWidth(m[1]),
    type: m[3] ? 'ol' : 'ul',
    text,
    task,
  };
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
}

function parseTableAligns(sepLine) {
  return splitTableRow(sepLine).map(cell => {
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    return '';
  });
}

function renderListNodes(nodes, depth = 0) {
  if (!nodes.length) return '';
  const parts = [];
  let i = 0;
  while (i < nodes.length) {
    const tag = nodes[i].type;
    const items = [];
    while (i < nodes.length && nodes[i].type === tag) {
      const item = nodes[i++];
      const hasChildren = item.children.length > 0;
      const isGroup = depth === 0 && tag === 'ul' && item.task === null && hasChildren && GROUP_TITLE_RE.test(item.text.trim());
      const cls = [];
      if (isGroup) cls.push('md-group');
      if (item.task !== null) cls.push('md-task', item.task ? 'done' : 'todo');
      const attr = cls.length ? ` class="${cls.join(' ')}"` : '';
      const checkbox = item.task !== null
        ? `<span class="md-chk" aria-hidden="true">${item.task ? '✓' : ''}</span>`
        : '';
      const label = isGroup
        ? `<div class="md-group-title">${fmtInline(item.text.trim().replace(/:$/, ''))}</div>`
        : `<span class="md-li-text">${fmtInline(item.text)}</span>`;
      const nested = hasChildren ? renderListNodes(item.children, depth + 1) : '';
      items.push(`<li${attr}>${checkbox}${label}${nested}</li>`);
    }
    const listClass = depth === 0 && items.every(x => x.startsWith('<li class="md-group"'))
      ? ' class="md-groups"'
      : (depth > 0 ? ' class="md-nested"' : '');
    parts.push(`<${tag}${listClass}>${items.join('')}</${tag}>`);
  }
  return parts.join('');
}

function parseListBlock(lines, startIdx) {
  const roots = [];
  const stack = []; // [{ indent, node }]
  let i = startIdx;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      // Keep the list open across a single blank line if the next non-empty line is another list item.
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      if (j < lines.length && parseListLine(lines[j])) {
        i = j;
        continue;
      }
      break;
    }

    const parsed = parseListLine(line);
    if (!parsed) {
      // Indented continuation line belonging to the current list item.
      if (stack.length && /^[ \t]+/.test(line) && !FENCE_RE.test(line) && !HEADING_RE.test(line)) {
        const cur = stack[stack.length - 1].node;
        cur.text += ' ' + line.trim();
        i++;
        continue;
      }
      break;
    }

    const node = { type: parsed.type, text: parsed.text, task: parsed.task, children: [] };
    while (stack.length && parsed.indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }
    if (stack.length) {
      stack[stack.length - 1].node.children.push(node);
    } else {
      roots.push(node);
    }
    stack.push({ indent: parsed.indent, node });
    i++;
  }

  return { html: renderListNodes(roots, 0), nextIndex: i };
}

export function renderMarkdown(md) {
  const lines = String(md ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const mf = line.match(FENCE_RE);
    if (mf) {
      const lang = mf[1] || '';
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
      if (i < lines.length) i++;
      const badge = lang ? `<div class="md-lang">${esc(lang)}</div>` : '';
      out.push(`<div class="md-code">${badge}<pre><code>${esc(buf.join('\n'))}</code></pre></div>`);
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && SEP_RE.test(lines[i + 1])) {
      const head = splitTableRow(line);
      const aligns = parseTableAligns(lines[i + 1]);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(splitTableRow(lines[i++]));
      const styleFor = idx => (aligns[idx] ? ` style="text-align:${aligns[idx]}"` : '');
      out.push(
        '<div class="md-table-wrap"><table><thead><tr>' +
        head.map((c, idx) => `<th${styleFor(idx)}>${fmtInline(c)}</th>`).join('') +
        '</tr></thead><tbody>' +
        rows.map(r => '<tr>' + head.map((_, idx) => `<td${styleFor(idx)}>${fmtInline(r[idx] ?? '')}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div>',
      );
      continue;
    }

    if (HR_RE.test(line)) { out.push('<hr>'); i++; continue; }

    const mh = line.match(HEADING_RE);
    if (mh) {
      const level = mh[1].length;
      out.push(`<div class="md-h md-h${level}">${fmtInline(mh[2].trim())}</div>`);
      i++;
      continue;
    }

    if (QUOTE_RE.test(line)) {
      const buf = [];
      while (i < lines.length && QUOTE_RE.test(lines[i])) buf.push(lines[i++].replace(QUOTE_RE, ''));
      out.push(`<blockquote>${renderMarkdown(buf.join('\n'))}</blockquote>`);
      continue;
    }

    if (parseListLine(line)) {
      const { html, nextIndex } = parseListBlock(lines, i);
      out.push(html);
      i = nextIndex;
      continue;
    }

    const buf = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !HR_RE.test(lines[i]) &&
      !FENCE_RE.test(lines[i]) &&
      !HEADING_RE.test(lines[i]) &&
      !QUOTE_RE.test(lines[i]) &&
      !parseListLine(lines[i]) &&
      !(lines[i].includes('|') && i + 1 < lines.length && SEP_RE.test(lines[i + 1]))
    ) {
      buf.push(lines[i++].trim());
    }
    out.push(`<p>${buf.map(fmtInline).join('<br>')}</p>`);
  }

  return out.join('');
}
