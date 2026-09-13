(() => {
  'use strict';
  const normalize = text => text.replace(/\r\n/g, '\n').replace(/\u00a0/g, ' ').replace(/\n+$/, '');
  const read = editor => normalize(editor.innerText || '');
  function replacementError(editor, expected, allowed) {
    if (!allowed() || !editor.isConnected || !editor.isContentEditable) return 'This writing box is no longer available.';
    if (read(editor) !== normalize(expected)) return 'Your draft changed. Check it again before applying this suggestion.';
    return null;
  }
  function replace(editor, expected, replacement, allowed) {
    const error = replacementError(editor, expected, allowed);
    if (error) throw new Error(error);
    const doc = editor.ownerDocument;
    editor.focus({preventScroll: true});
    const selection = doc.getSelection(), range = doc.createRange();
    range.selectNodeContents(editor); selection.removeAllRanges(); selection.addRange(range);
    // Chrome's editing command updates the native editor and its undo history.
    // Do not fall back to replacing DOM nodes: that can bypass a controlled editor.
    if (!doc.execCommand('insertText', false, replacement) || read(editor) !== normalize(replacement)) {
      throw new Error('X could not apply this edit. Copy the suggestion and paste it into your draft.');
    }
  }
  function changes(before, after) {
    const tokenize = text => text.match(/\s+|[^\s]+/gu) || [];
    const a = tokenize(before), b = tokenize(after);
    if (a.length * b.length > 160000) return [{kind: 'remove', text: before}, {kind: 'add', text: after}];
    const width = b.length + 1;
    const table = new Uint16Array((a.length + 1) * width);
    for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) {
      table[i * width + j] = a[i] === b[j] ? 1 + table[(i + 1) * width + j + 1] :
        Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
    const parts = [];
    const push = (kind, text) => {
      if (parts.at(-1)?.kind === kind) parts.at(-1).text += text;
      else parts.push({kind, text});
    };
    let i = 0, j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) { push('same', a[i]); i++; j++; }
      else if (i < a.length && (j === b.length || table[(i + 1) * width + j] >= table[i * width + j + 1])) { push('remove', a[i++]); }
      else push('add', b[j++]);
    }
    return parts;
  }
  function suggestions(before, after) {
    const parts = changes(before, after), edits = [];
    let offset = 0, edit = null;
    const flush = () => { if (edit) { edits.push({...edit, status: 'pending'}); edit = null; } };
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      // Keep related changes within a sentence together, so accepting a prefix
      // cannot leave a fragment that depends on a later replacement.
      if (part.kind === 'same' && !(edit && parts[i + 1] && !/[.!?。！？\n]/u.test(part.text))) {
        flush(); offset += part.text.length; continue;
      }
      edit ||= {start: offset, end: offset, before: '', after: ''};
      if (part.kind !== 'add') { edit.before += part.text; offset += part.text.length; edit.end = offset; }
      if (part.kind !== 'remove') edit.after += part.text;
    }
    flush(); return edits;
  }
  function acceptEdit(text, edits, index) {
    const edit = edits[index];
    if (!edit || edit.status !== 'pending' || text.slice(edit.start, edit.end) !== edit.before) throw new Error('This suggestion no longer matches your draft. Check again.');
    const delta = edit.after.length - (edit.end - edit.start);
    return {text: text.slice(0, edit.start) + edit.after + text.slice(edit.end), edits: edits.map((item, i) =>
      i === index ? {...item, status: 'accepted'} : item.status === 'pending' && item.start >= edit.end ?
        {...item, start: item.start + delta, end: item.end + delta} : {...item})};
  }
  function textRange(editor, start, end) {
    const doc = editor.ownerDocument, text = read(editor), positions = [];
    const walker = doc.createTreeWalker(editor, 4);
    let child, cursor = 0;
    while ((child = walker.nextNode())) {
      if (!child.textContent) continue;
      const value = child.textContent.replace(/\u00a0/g, ' ');
      const at = text.indexOf(value, cursor);
      // InnerText adds line breaks between blocks. Fail closed for other mismatches.
      if (at < 0 || text.slice(cursor, at).trim()) return null;
      positions.push({child, start: at, end: at + value.length}); cursor = at + value.length;
    }
    if (text.slice(cursor).trim() || start < 0 || end < start || end > text.length) return null;
    const first = positions.find(p => start >= p.start && start < p.end) || positions.findLast(p => start === p.end);
    const last = positions.findLast(p => end > p.start && end <= p.end) || positions.find(p => end === p.start);
    if (!first || !last) return null;
    const range = doc.createRange();
    range.setStart(first.child, start - first.start); range.setEnd(last.child, end - last.start);
    return range;
  }
  function replacePart(editor, expected, edit, allowed) {
    const error = replacementError(editor, expected, allowed);
    if (error) throw new Error(error);
    const range = textRange(editor, edit.start, edit.end);
    if (!range) throw new Error('This text cannot be located safely. Check the draft again.');
    const next = expected.slice(0, edit.start) + edit.after + expected.slice(edit.end);
    editor.focus({preventScroll: true});
    const selection = editor.ownerDocument.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    if (!editor.ownerDocument.execCommand('insertText', false, edit.after) || read(editor) !== next) {
      throw new Error('The editor could not apply this suggestion. Check your draft again.');
    }
    return next;
  }
  globalThis.MimicryInlineCore = Object.freeze({normalize, read, replacementError, replace, changes, suggestions, acceptEdit, textRange, replacePart});
})();
