import assert from 'node:assert/strict';
import test from 'node:test';

import { fmtInline, renderMarkdown } from '../live_thread/web/markdown.js';

test('renders section-group nested bullets cleanly instead of flattening', () => {
  const md = [
    '- **Main topics**',
    '  - Обговорення безпеки (security)',
    '  - Емоційний стан учасників',
    '',
    '- **Key takeaways / answers**',
    '  - Учасник підкреслює важливість безпеки',
    '',
    '- **Action items / open questions**',
    '  - Немає визначених завдань',
  ].join('\n');

  const html = renderMarkdown(md);
  assert.match(html, /<ul class="md-groups">/);
  assert.equal((html.match(/<li class="md-group">/g) || []).length, 3);
  assert.equal((html.match(/<div class="md-group-title">/g) || []).length, 3);
  assert.equal((html.match(/<ul class="md-nested">/g) || []).length, 3);
  assert.match(html, /Обговорення безпеки \(security\)/);
});

test('preserves multi-level nested ul and ol lists', () => {
  const md = [
    '1. First step',
    '   - Detail A',
    '     - Sub-detail A1',
    '   - Detail B',
    '2. Second step',
  ].join('\n');

  const html = renderMarkdown(md);
  assert.match(html, /^<ol>.*<\/ol>$/);
  assert.match(html, /<ul class="md-nested">.*<ul class="md-nested">.*Sub-detail A1/);
  assert.match(html, /Second step/);
});

test('renders headings h1-h4, task lists, code blocks with language, tables, and blockquotes', () => {
  const md = [
    '### Section',
    '- [x] Done item',
    '- [ ] Open item',
    '',
    '```bash',
    'kubectl get pods',
    '```',
    '',
    '| Key | Value |',
    '| --- | ---: |',
    '| `port` | **8765** |',
    '',
    '> Important **note**',
  ].join('\n');

  const html = renderMarkdown(md);
  assert.match(html, /<div class="md-h md-h3">Section<\/div>/);
  assert.match(html, /class="md-task done"/);
  assert.match(html, /class="md-task todo"/);
  assert.match(html, /<div class="md-lang">bash<\/div>/);
  assert.match(html, /<pre><code>kubectl get pods<\/code><\/pre>/);
  assert.match(html, /<td style="text-align:right"><strong>8765<\/strong><\/td>/);
  assert.match(html, /<blockquote><p>Important <strong>note<\/strong><\/p><\/blockquote>/);
});

test('escapes HTML to prevent XSS while preserving inline markdown', () => {
  const html = fmtInline('Use `<script>` & **bold** with [link](https://example.com) and <img src=x onerror=alert(1)>');
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /<code>&lt;script&gt;<\/code>/);
  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /<a href="https:\/\/example\.com" target="_blank" rel="noopener noreferrer">link<\/a>/);
});
