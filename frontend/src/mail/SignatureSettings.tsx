import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from 'react';
import {
  createSignature,
  deleteSignature,
  fetchDefaultSignature,
  fetchSignatures,
  setDefaultSignature,
  updateSignature,
} from './api';
import { sanitizeMessageHtml } from './MessageReader';
import type { MailSignature, SignatureUpsertPayload } from './types';
import { translateText } from '../i18n/runtime';

type EditorMode = 'rich' | 'plain';
type ColorMenu = 'text' | 'highlight' | null;
type ToggleStyleCommand = 'bold' | 'italic' | 'underline' | 'strikeThrough';
type InlineImagePosition = 'left' | 'center' | 'right';
type ActiveStyles = {
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strikeThrough: boolean;
  insertOrderedList: boolean;
  insertUnorderedList: boolean;
};

type SignatureDraft = {
  id: string | null;
  name: string;
  htmlBody: string;
  textBody: string;
};

const FONT_FAMILIES = ['宋体', '微软雅黑', 'Arial', 'Helvetica', 'Times New Roman', 'Courier New'];
const FONT_SIZES = [
  { label: '12', value: '12px' },
  { label: '14', value: '14px' },
  { label: '16', value: '16px' },
  { label: '18', value: '18px' },
  { label: '24', value: '24px' },
  { label: '32', value: '32px' },
];
const COLOR_SWATCHES = [
  '#1abc9c',
  '#2ecc71',
  '#3498db',
  '#9b59b6',
  '#536779',
  '#f1c40f',
  '#16a085',
  '#27ae60',
  '#2980b9',
  '#8e44ad',
  '#2c3e50',
  '#f39c12',
  '#e67e22',
  '#e74c3c',
  '#ecf0f1',
  '#95a5a6',
  '#d9d9d9',
  '#ffffff',
  '#d35400',
  '#c0392b',
  '#bdc3c7',
  '#7f8c8d',
  '#999999',
  '#000000',
];
const TABLE_PICKER_ROWS = 8;
const TABLE_PICKER_COLUMNS = 10;
const INLINE_IMAGE_DEFAULT_WIDTH = 320;
const INLINE_IMAGE_MIN_WIDTH = 120;
const INLINE_IMAGE_MAX_WIDTH = 900;
const INLINE_IMAGE_STEP = 10;
const DEFAULT_ACTIVE_STYLES: ActiveStyles = {
  bold: false,
  italic: false,
  underline: false,
  strikeThrough: false,
  insertOrderedList: false,
  insertUnorderedList: false,
};
const EMPTY_DRAFT: SignatureDraft = {
  id: null,
  name: '',
  htmlBody: '',
  textBody: '',
};

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeSignatureHtml(html: string) {
  return sanitizeMessageHtml(html ?? '');
}

function htmlToPlainText(html: string) {
  const container = document.createElement('div');
  container.innerHTML = html;
  return (container.innerText || container.textContent || '').replace(/\u200b/g, '');
}

function plainTextToHtml(value: string) {
  return escapeHtml(value.replace(/\r\n/g, '\n')).replace(/\n/g, '<br>');
}

function normalizeSignature(signature: MailSignature) {
  const htmlBody = sanitizeSignatureHtml(signature.html_body || '');
  return {
    ...signature,
    html_body: htmlBody,
    text_body: htmlToPlainText(htmlBody),
  };
}

function readSignatureDraft(signature: MailSignature | null): SignatureDraft {
  if (!signature) {
    return EMPTY_DRAFT;
  }
  const htmlBody = sanitizeSignatureHtml(signature.html_body || '');
  return {
    id: signature.id,
    name: signature.name,
    htmlBody,
    textBody: htmlToPlainText(htmlBody),
  };
}

function buildTableHtml(rows: number, columns: number) {
  const safeRows = Math.max(1, Math.min(rows, 20));
  const safeColumns = Math.max(1, Math.min(columns, 10));
  const cells = Array.from({ length: safeRows }, () => `<tr>${Array.from({ length: safeColumns }, () => '<td><br></td>').join('')}</tr>`).join('');
  return `<table style="width:100%;border-collapse:collapse;" border="1"><tbody>${cells}</tbody></table><p><br></p>`;
}

function createInlineImageId() {
  return `signature-inline-image-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function parseInlineImageWidth(image: HTMLImageElement) {
  const styleWidth = image.style.width.trim();
  if (styleWidth.endsWith('px')) {
    const parsed = Number.parseInt(styleWidth, 10);
    if (Number.isFinite(parsed) && parsed > 0) {
      return parsed;
    }
  }
  const attrWidth = Number.parseInt(image.getAttribute('width') || '', 10);
  if (Number.isFinite(attrWidth) && attrWidth > 0) {
    return attrWidth;
  }
  return INLINE_IMAGE_DEFAULT_WIDTH;
}

function clampInlineImageWidth(value: number) {
  return Math.min(INLINE_IMAGE_MAX_WIDTH, Math.max(INLINE_IMAGE_MIN_WIDTH, value));
}

function stripSignatureEditorMetadata(value: string) {
  const container = document.createElement('div');
  container.innerHTML = value;
  container.querySelectorAll<HTMLElement>('.inline-image').forEach((wrapper) => {
    wrapper.removeAttribute('data-inline-image-id');
    wrapper.removeAttribute('data-selected');
    wrapper.removeAttribute('data-inline-image-position');
  });
  return container.innerHTML;
}

export type SignatureSettingsProps = {
  open: boolean;
  onClose: () => void;
};

export default function SignatureSettings({ open, onClose }: SignatureSettingsProps) {
  const [signatures, setSignatures] = useState<MailSignature[]>([]);
  const [defaultSignatureId, setDefaultSignatureId] = useState<string | null>(null);
  const [selectedSignatureId, setSelectedSignatureId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SignatureDraft>(EMPTY_DRAFT);
  const [editorMode, setEditorMode] = useState<EditorMode>('rich');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [openColorMenu, setOpenColorMenu] = useState<ColorMenu>(null);
  const [tablePickerOpen, setTablePickerOpen] = useState(false);
  const [tablePickerSize, setTablePickerSize] = useState({ rows: 1, columns: 1 });
  const [activeStyles, setActiveStyles] = useState<ActiveStyles>(DEFAULT_ACTIVE_STYLES);
  const [selectedInlineImageId, setSelectedInlineImageId] = useState<string | null>(null);
  const [selectedInlineImageWidth, setSelectedInlineImageWidth] = useState(INLINE_IMAGE_DEFAULT_WIDTH);
  const editorRef = useRef<HTMLDivElement | null>(null);
  const plainTextRef = useRef<HTMLTextAreaElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const savedSelectionRef = useRef<Range | null>(null);
  const richHtmlCacheRef = useRef('');

  const selectedSignature = useMemo(
    () => signatures.find((item) => item.id === selectedSignatureId) ?? null,
    [selectedSignatureId, signatures],
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErrorMessage(null);
    Promise.all([fetchSignatures(), fetchDefaultSignature()])
      .then(([listResult, defaultResult]) => {
        if (cancelled) {
          return;
        }
        const items = (listResult.signatures ?? []).map(normalizeSignature);
        setSignatures(items);
        const defaultId = defaultResult.signature?.id ?? items.find((item) => item.is_default)?.id ?? null;
        setDefaultSignatureId(defaultId);
        const nextSelectedId = selectedSignatureId && items.some((item) => item.id === selectedSignatureId)
          ? selectedSignatureId
          : defaultId ?? items[0]?.id ?? null;
        setSelectedSignatureId(nextSelectedId);
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage((error as Error).message || translateText('签名加载失败'));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const nextDraft = readSignatureDraft(selectedSignature);
    setDraft(nextDraft);
    setEditorMode(nextDraft.htmlBody.trim() ? 'rich' : 'plain');
    setOpenColorMenu(null);
    setTablePickerOpen(false);
    setTablePickerSize({ rows: 1, columns: 1 });
    setActiveStyles(DEFAULT_ACTIVE_STYLES);
    setSelectedInlineImageId(null);
    setSelectedInlineImageWidth(INLINE_IMAGE_DEFAULT_WIDTH);
    richHtmlCacheRef.current = nextDraft.htmlBody;
  }, [open, selectedSignature]);

  useEffect(() => {
    if (!open || editorMode !== 'rich' || !editorRef.current) {
      return;
    }
    if (editorRef.current.innerHTML !== draft.htmlBody) {
      editorRef.current.innerHTML = draft.htmlBody;
    }
  }, [draft.htmlBody, editorMode, open]);

  useEffect(() => {
    if (!open || editorMode !== 'rich' || !selectedInlineImageId) {
      return;
    }
    const editor = editorRef.current;
    if (!editor) {
      return;
    }
    let foundImage: HTMLImageElement | null = null;
    editor.querySelectorAll<HTMLElement>('.inline-image').forEach((wrapper) => {
      const isSelected = wrapper.getAttribute('data-inline-image-id') === selectedInlineImageId;
      if (isSelected) {
        wrapper.setAttribute('data-selected', 'true');
        foundImage = wrapper.querySelector('img');
      } else {
        wrapper.removeAttribute('data-selected');
      }
    });
    if (foundImage) {
      setSelectedInlineImageWidth(parseInlineImageWidth(foundImage));
      return;
    }
    setSelectedInlineImageId(null);
  }, [draft.htmlBody, editorMode, open, selectedInlineImageId]);

  function markError(message: string) {
    setErrorMessage(message);
  }

  function updateDraftField(field: keyof SignatureDraft, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
    setErrorMessage(null);
  }

  function setDraftBodies(nextHtmlBody: string, nextTextBody?: string) {
    const textBody = nextTextBody ?? htmlToPlainText(nextHtmlBody);
    setDraft((current) => ({
      ...current,
      htmlBody: nextHtmlBody,
      textBody,
    }));
    richHtmlCacheRef.current = nextHtmlBody;
    setErrorMessage(null);
  }

  function resetEditorToBlank() {
    setSelectedSignatureId(null);
    setDraft(EMPTY_DRAFT);
    setEditorMode('plain');
    setOpenColorMenu(null);
    setTablePickerOpen(false);
    setActiveStyles(DEFAULT_ACTIVE_STYLES);
    setSelectedInlineImageId(null);
    setSelectedInlineImageWidth(INLINE_IMAGE_DEFAULT_WIDTH);
    richHtmlCacheRef.current = '';
    setErrorMessage(null);
  }

  function editorContainsNode(node: Node | null) {
    return Boolean(node && editorRef.current?.contains(node));
  }

  function saveSelection() {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      return;
    }
    const range = selection.getRangeAt(0);
    if (editorContainsNode(range.commonAncestorContainer)) {
      savedSelectionRef.current = range.cloneRange();
    }
  }

  function collapseSelectionToEditorEnd() {
    const editor = editorRef.current;
    const selection = window.getSelection();
    if (!editor || !selection) {
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
    savedSelectionRef.current = range.cloneRange();
  }

  function restoreSelection() {
    const selection = window.getSelection();
    const range = savedSelectionRef.current;
    if (!selection || !editorRef.current) {
      return;
    }
    if (!range || !editorContainsNode(range.commonAncestorContainer)) {
      collapseSelectionToEditorEnd();
      return;
    }
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function syncFromEditor() {
    const editor = editorRef.current;
    if (!editor) {
      return;
    }
    saveSelection();
    setDraftBodies(editor.innerHTML, htmlToPlainText(editor.innerHTML));
    if (selectedInlineImageId && !getSelectedInlineImageElements()) {
      setSelectedInlineImageId(null);
      setSelectedInlineImageWidth(INLINE_IMAGE_DEFAULT_WIDTH);
    }
    refreshActiveStyles();
    readInlineStylesFromSelection();
  }

  function switchEditorMode(nextMode: EditorMode) {
    if (nextMode === editorMode) {
      return;
    }
    setOpenColorMenu(null);
    setTablePickerOpen(false);
    if (nextMode === 'plain') {
      const richHtml = draft.htmlBody || richHtmlCacheRef.current || plainTextToHtml(draft.textBody);
      richHtmlCacheRef.current = richHtml;
      setEditorMode('plain');
      setDraft((current) => ({
        ...current,
        textBody: htmlToPlainText(richHtml),
        htmlBody: richHtml,
      }));
      return;
    }
    const nextHtml = richHtmlCacheRef.current || plainTextToHtml(draft.textBody);
    richHtmlCacheRef.current = nextHtml;
    setEditorMode('rich');
    setDraft((current) => ({
      ...current,
      htmlBody: nextHtml,
      textBody: htmlToPlainText(nextHtml),
    }));
  }

  function runEditorCommand(command: string, value?: string) {
    restoreSelection();
    if (typeof document.execCommand === 'function') {
      document.execCommand(command, false, value);
    }
    syncFromEditor();
  }

  function applyInlineStyle(property: string, value: string) {
    if (!value) {
      return;
    }
    restoreSelection();
    if (typeof document.execCommand === 'function') {
      document.execCommand('styleWithCSS', false, 'true');
      document.execCommand('foreColor', false, 'inherit');
      document.execCommand('fontName', false, 'inherit');
      document.execCommand('fontSize', false, '7');
    }
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0 && editorContainsNode(selection.getRangeAt(0).commonAncestorContainer)) {
      const range = selection.getRangeAt(0);
      const span = document.createElement('span');
      span.style.setProperty(property, value);
      try {
        range.surroundContents(span);
      } catch {
        const contents = range.extractContents();
        span.appendChild(contents);
        range.insertNode(span);
      }
    }
    syncFromEditor();
  }

  function toggleInlineStyle(command: ToggleStyleCommand) {
    restoreSelection();
    if (typeof document.execCommand === 'function') {
      document.execCommand(command, false);
    }
    syncFromEditor();
  }

  function refreshActiveStyles() {
    if (typeof document.queryCommandState !== 'function') {
      return;
    }
    setActiveStyles((current) => ({
      ...current,
      insertOrderedList: document.queryCommandState('insertOrderedList'),
      insertUnorderedList: document.queryCommandState('insertUnorderedList'),
    }));
  }

  function readInlineStylesFromSelection() {
    const range = savedSelectionRef.current;
    if (!range || !editorContainsNode(range.commonAncestorContainer)) {
      return;
    }
    const node = range.startContainer;
    const element = node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
    if (!element) {
      return;
    }
    setActiveStyles((current) => ({
      ...current,
      bold: Boolean(element.closest('strong,b')),
      italic: Boolean(element.closest('em,i')),
      underline: Boolean(element.closest('u,[style*="underline"]')),
      strikeThrough: Boolean(element.closest('s,strike,[style*="line-through"]')),
    }));
  }

  function handleColor(command: 'foreColor' | 'hiliteColor', color: string) {
    restoreSelection();
    if (typeof document.execCommand === 'function') {
      document.execCommand(command, false, color);
    }
    setOpenColorMenu(null);
    syncFromEditor();
  }

  function insertHtmlAtCursor(html: string) {
    const editor = editorRef.current;
    if (!editor) {
      return;
    }
    restoreSelection();
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0) {
      const range = selection.getRangeAt(0);
      if (editorContainsNode(range.commonAncestorContainer)) {
        const template = document.createElement('template');
        template.innerHTML = html;
        const fragment = template.content;
        const lastNode = fragment.lastChild;
        range.deleteContents();
        range.insertNode(fragment);
        if (lastNode) {
          range.setStartAfter(lastNode);
          range.collapse(true);
          selection.removeAllRanges();
          selection.addRange(range);
          savedSelectionRef.current = range.cloneRange();
        }
        syncFromEditor();
        return;
      }
    }
    editor.insertAdjacentHTML('beforeend', html);
    collapseSelectionToEditorEnd();
    syncFromEditor();
  }

  function handleLinkInsert() {
    const rawUrl = window.prompt(translateText('请输入链接地址'), 'https://');
    if (!rawUrl) {
      return;
    }
    const url = rawUrl.trim();
    try {
      const parsed = new URL(url, window.location.origin);
      if (!['http:', 'https:', 'mailto:', 'tel:'].includes(parsed.protocol)) {
        markError(translateText('链接地址无效'));
        return;
      }
      restoreSelection();
      if (typeof document.execCommand === 'function') {
        document.execCommand('createLink', false, parsed.href);
      }
      syncFromEditor();
    } catch {
      markError(translateText('链接地址无效'));
    }
  }

  function handleQuoteInsert() {
    insertHtmlAtCursor('<blockquote><p><br></p></blockquote><p><br></p>');
  }

  function handleInlineImageFiles(files: File[]) {
    files.forEach((file) => {
      if (!file.type.startsWith('image/')) {
        markError(translateText('只能插入图片文件'));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const src = String(reader.result ?? '');
        if (!src) {
          markError(translateText('图片读取失败'));
          return;
        }
        const inlineImageId = createInlineImageId();
        insertHtmlAtCursor(
          `<div class="inline-image" data-inline-image-id="${inlineImageId}" data-inline-image-position="center" style="text-align: center;">` +
            `<img src="${src}" alt="${escapeHtml(file.name)}" style="width: ${INLINE_IMAGE_DEFAULT_WIDTH}px; max-width: 100%; height: auto;" />` +
            `</div><p><br></p>`,
        );
        setSelectedInlineImageId(inlineImageId);
        setSelectedInlineImageWidth(INLINE_IMAGE_DEFAULT_WIDTH);
      };
      reader.readAsDataURL(file);
    });
  }

  function handleInlineImageUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    handleInlineImageFiles(files);
  }

  function insertTable(rows: number, columns: number) {
    insertHtmlAtCursor(buildTableHtml(rows, columns));
    setTablePickerOpen(false);
  }

  function handleEditorInput() {
    syncFromEditor();
  }

  function handleEditorBlur() {
    saveSelection();
    syncFromEditor();
  }

  function handleEditorKeyUp(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      setOpenColorMenu(null);
      setTablePickerOpen(false);
    }
    saveSelection();
    readInlineStylesFromSelection();
    refreshActiveStyles();
  }

  function handleEditorMouseUp() {
    saveSelection();
    readInlineStylesFromSelection();
    refreshActiveStyles();
  }

  function getSelectedInlineImageElements() {
    const editor = editorRef.current;
    if (!editor || !selectedInlineImageId) {
      return null;
    }
    const wrapper = Array.from(editor.querySelectorAll<HTMLElement>('.inline-image')).find(
      (item) => item.getAttribute('data-inline-image-id') === selectedInlineImageId,
    );
    if (!wrapper) {
      return null;
    }
    const image = wrapper.querySelector('img') as HTMLImageElement | null;
    if (!image) {
      return null;
    }
    return { wrapper, image };
  }

  function setSelectedInlineImageSize(width: number) {
    const elements = getSelectedInlineImageElements();
    if (!elements) {
      return;
    }
    const nextWidth = clampInlineImageWidth(width);
    elements.image.style.width = `${nextWidth}px`;
    elements.image.style.maxWidth = '100%';
    elements.image.style.height = 'auto';
    elements.image.removeAttribute('width');
    elements.image.removeAttribute('height');
    setSelectedInlineImageWidth(nextWidth);
    syncFromEditor();
  }

  function selectInlineImage(wrapper: HTMLElement) {
    const image = wrapper.querySelector('img') as HTMLImageElement | null;
    if (!image) {
      return;
    }
    const imageId = wrapper.getAttribute('data-inline-image-id') || createInlineImageId();
    wrapper.setAttribute('data-inline-image-id', imageId);
    wrapper.setAttribute('data-selected', 'true');
    setSelectedInlineImageId(imageId);
    setSelectedInlineImageWidth(parseInlineImageWidth(image));
    saveSelection();
    editorRef.current?.focus();
  }

  function clearInlineImageSelection() {
    setSelectedInlineImageId(null);
    const editor = editorRef.current;
    if (!editor) {
      return;
    }
    editor.querySelectorAll<HTMLElement>('.inline-image[data-selected="true"]').forEach((wrapper) => {
      wrapper.removeAttribute('data-selected');
    });
  }

  function setInlineImageWrapperPosition(wrapper: HTMLElement, position: InlineImagePosition) {
    wrapper.style.textAlign = position;
    wrapper.setAttribute('data-inline-image-position', position);
  }

  function handlePlainTextChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const textBody = event.target.value;
    const htmlBody = plainTextToHtml(textBody);
    setDraftBodies(htmlBody, textBody);
  }

  async function reloadSignatures(preferredId?: string | null) {
    const [listResult, defaultResult] = await Promise.all([fetchSignatures(), fetchDefaultSignature()]);
    const items = (listResult.signatures ?? []).map(normalizeSignature);
    setSignatures(items);
    const nextDefaultId = defaultResult.signature?.id ?? items.find((item) => item.is_default)?.id ?? null;
    setDefaultSignatureId(nextDefaultId);
    const nextSelectedId = preferredId !== undefined
      ? (preferredId && items.some((item) => item.id === preferredId) ? preferredId : preferredId)
      : nextDefaultId ?? items[0]?.id ?? null;
    setSelectedSignatureId(nextSelectedId);
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = draft.name.trim();
    if (!name) {
      markError(translateText('请输入签名名称'));
      return;
    }
    const rawHtml = editorMode === 'rich' ? (editorRef.current?.innerHTML ?? draft.htmlBody) : plainTextToHtml(draft.textBody);
    const htmlBody = sanitizeSignatureHtml(stripSignatureEditorMetadata(rawHtml));
    const payload: SignatureUpsertPayload = {
      name,
      html_body: htmlBody,
      text_body: htmlToPlainText(htmlBody),
    };
    setSaving(true);
    setErrorMessage(null);
    try {
      let preferredId = draft.id;
      if (draft.id) {
        const result = await updateSignature(draft.id, payload);
        preferredId = result.signature?.id ?? draft.id;
      } else {
        const result = await createSignature(payload);
        preferredId = result.signature?.id ?? null;
      }
      await reloadSignatures(preferredId);
    } catch (error) {
      markError((error as Error).message || translateText('签名保存失败'));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(signatureId: string) {
    const target = signatures.find((item) => item.id === signatureId);
    if (!target) {
      return;
    }
    const confirmed = window.confirm(translateText(`确定删除签名「${target.name}」吗？`));
    if (!confirmed) {
      return;
    }
    setDeletingId(signatureId);
    setErrorMessage(null);
    try {
      await deleteSignature(signatureId);
      await reloadSignatures(selectedSignatureId === signatureId ? null : selectedSignatureId);
      if (draft.id === signatureId) {
        resetEditorToBlank();
      }
    } catch (error) {
      markError((error as Error).message || translateText('签名删除失败'));
    } finally {
      setDeletingId(null);
    }
  }

  async function handleSetDefault(signatureId: string) {
    setErrorMessage(null);
    try {
      await setDefaultSignature(signatureId);
      await reloadSignatures(signatureId);
    } catch (error) {
      markError((error as Error).message || translateText('设置默认签名失败'));
    }
  }

  if (!open) {
    return null;
  }

  return (
    <div className="settings-modal-overlay">
      <div className="signature-modal" role="dialog" aria-modal="true" aria-label="签名设置">
        <header className="signature-modal-header">
          <div>
            <h2>签名设置</h2>
            <p>编辑器与写邮件正文保持一致，支持纯文本与富文本切换。</p>
          </div>
          <button type="button" className="signature-close-button" onClick={onClose} aria-label="关闭签名设置">
            ×
          </button>
        </header>

        {errorMessage ? (
          <div className="signature-alert" role="alert">
            {errorMessage}
          </div>
        ) : null}

        {loading ? (
          <div className="signature-loading">正在加载签名...</div>
        ) : (
          <div className="signature-layout">
            <aside className="signature-list-pane">
              <div className="signature-list-head">
                <h3>签名列表</h3>
                <button type="button" className="signature-secondary-button" onClick={resetEditorToBlank}>
                  新建签名
                </button>
              </div>
              <ul className="signature-list" aria-label="签名列表">
                {signatures.length > 0 ? (
                  signatures.map((signature) => {
                    const isSelected = selectedSignatureId === signature.id;
                    const isDefault = defaultSignatureId === signature.id || Boolean(signature.is_default);
                    return (
                      <li key={signature.id} data-selected={isSelected ? 'true' : 'false'}>
                        <button
                          type="button"
                          className="signature-list-item"
                          onClick={() => setSelectedSignatureId(signature.id)}
                          aria-label={`编辑 ${signature.name}`}
                        >
                          <span className="signature-list-item-title">
                            {signature.name}
                            {isDefault ? <span className="signature-default-badge">默认</span> : null}
                          </span>
                          <span
                            className="signature-list-item-preview"
                            dangerouslySetInnerHTML={{ __html: signature.html_body || '<span>空白签名</span>' }}
                          />
                        </button>
                        <div className="signature-item-actions">
                          {isDefault ? null : (
                            <button
                              type="button"
                              className="signature-link-button"
                              onClick={() => handleSetDefault(signature.id)}
                              aria-label={`设为默认 ${signature.name}`}
                            >
                              设为默认
                            </button>
                          )}
                          <button
                            type="button"
                            className="signature-link-button danger"
                            onClick={() => handleDelete(signature.id)}
                            disabled={deletingId === signature.id}
                            aria-label={`删除 ${signature.name}`}
                          >
                            {deletingId === signature.id ? '删除中...' : '删除'}
                          </button>
                        </div>
                      </li>
                    );
                  })
                ) : (
                  <li className="signature-empty">暂无签名，请新建一个。</li>
                )}
              </ul>
            </aside>

            <section className="signature-editor-pane">
              <form className="signature-editor-form" onSubmit={handleSave}>
                <label className="signature-name-field">
                  <span>签名名称</span>
                  <input
                    type="text"
                    value={draft.name}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => updateDraftField('name', event.target.value)}
                    placeholder="例如：默认签名"
                    aria-label="签名名称"
                  />
                </label>

                <div className="compose-toolbar-floating signature-compose-toolbar" role="toolbar" aria-label="签名编辑工具栏">
                  <div className="toolbar-mode-group" role="group" aria-label="编辑模式">
                    <button
                      type="button"
                      className="toolbar-mode-btn"
                      aria-pressed={editorMode === 'rich'}
                      onClick={() => switchEditorMode('rich')}
                    >
                      富文本
                    </button>
                    <button
                      type="button"
                      className="toolbar-mode-btn"
                      aria-pressed={editorMode === 'plain'}
                      onClick={() => switchEditorMode('plain')}
                    >
                      纯文本
                    </button>
                  </div>
                  {editorMode === 'rich' ? (
                    <>
                      <span className="toolbar-divider" aria-hidden="true" />
                      <select className="toolbar-select" aria-label="字体" defaultValue="" onChange={(event) => applyInlineStyle('font-family', event.target.value)}>
                        <option value="" disabled>
                          字体
                        </option>
                        {FONT_FAMILIES.map((font) => (
                          <option key={font} value={font}>
                            {font}
                          </option>
                        ))}
                      </select>
                      <select className="toolbar-select toolbar-size-select" aria-label="字号" defaultValue="" onChange={(event) => applyInlineStyle('font-size', event.target.value)}>
                        <option value="" disabled>
                          字号
                        </option>
                        {FONT_SIZES.map((size) => (
                          <option key={size.value} value={size.value}>
                            {size.label}
                          </option>
                        ))}
                      </select>
                      <span className="toolbar-divider" aria-hidden="true" />
                      <button type="button" className="toolbar-icon-btn" aria-label="加粗" aria-pressed={activeStyles.bold} onMouseDown={(event) => event.preventDefault()} onClick={() => toggleInlineStyle('bold')}>
                        B
                      </button>
                      <button type="button" className="toolbar-icon-btn italic" aria-label="斜体" aria-pressed={activeStyles.italic} onMouseDown={(event) => event.preventDefault()} onClick={() => toggleInlineStyle('italic')}>
                        I
                      </button>
                      <button type="button" className="toolbar-icon-btn underline" aria-label="下划线" aria-pressed={activeStyles.underline} onMouseDown={(event) => event.preventDefault()} onClick={() => toggleInlineStyle('underline')}>
                        U
                      </button>
                      <button type="button" className="toolbar-icon-btn strike" aria-label="删除线" aria-pressed={activeStyles.strikeThrough} onMouseDown={(event) => event.preventDefault()} onClick={() => toggleInlineStyle('strikeThrough')}>
                        S
                      </button>
                      <div className="toolbar-popover-root">
                        <button
                          type="button"
                          className="toolbar-color-button"
                          aria-label="文字颜色"
                          aria-expanded={openColorMenu === 'text'}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => {
                            saveSelection();
                            setTablePickerOpen(false);
                            setOpenColorMenu((current) => (current === 'text' ? null : 'text'));
                          }}
                        >
                          <span className="toolbar-color-letter">A</span>
                          <span className="toolbar-caret">▾</span>
                        </button>
                        {openColorMenu === 'text' ? (
                          <div className="toolbar-popover color-palette" role="menu" aria-label="文字颜色色板">
                            <div className="color-grid">
                              {COLOR_SWATCHES.map((color) => (
                                <button
                                  key={`text-${color}`}
                                  type="button"
                                  className="color-swatch"
                                  style={{ backgroundColor: color }}
                                  aria-label={`文字颜色 ${color}`}
                                  onMouseDown={(event) => {
                                    event.preventDefault();
                                    handleColor('foreColor', color);
                                  }}
                                />
                              ))}
                            </div>
                          </div>
                        ) : null}
                      </div>
                      <div className="toolbar-popover-root">
                        <button
                          type="button"
                          className="toolbar-color-button highlight"
                          aria-label="背景高亮颜色"
                          aria-expanded={openColorMenu === 'highlight'}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => {
                            saveSelection();
                            setTablePickerOpen(false);
                            setOpenColorMenu((current) => (current === 'highlight' ? null : 'highlight'));
                          }}
                        >
                          <span className="toolbar-color-letter">A</span>
                          <span className="toolbar-caret">▾</span>
                        </button>
                        {openColorMenu === 'highlight' ? (
                          <div className="toolbar-popover color-palette" role="menu" aria-label="背景高亮颜色色板">
                            <div className="color-grid">
                              {COLOR_SWATCHES.map((color) => (
                                <button
                                  key={`highlight-${color}`}
                                  type="button"
                                  className="color-swatch"
                                  style={{ backgroundColor: color }}
                                  aria-label={`背景高亮颜色 ${color}`}
                                  onMouseDown={(event) => {
                                    event.preventDefault();
                                    handleColor('hiliteColor', color);
                                  }}
                                />
                              ))}
                            </div>
                          </div>
                        ) : null}
                      </div>
                      <span className="toolbar-divider" aria-hidden="true" />
                      <button type="button" className="toolbar-icon-btn" aria-label="有序列表" aria-pressed={activeStyles.insertOrderedList} onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('insertOrderedList')}>
                        1.
                      </button>
                      <button type="button" className="toolbar-icon-btn" aria-label="无序列表" aria-pressed={activeStyles.insertUnorderedList} onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('insertUnorderedList')}>
                        •
                      </button>
                      <button type="button" className="toolbar-icon-btn" aria-label="减少缩进" onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('outdent')}>
                        ←
                      </button>
                      <button type="button" className="toolbar-icon-btn" aria-label="增加缩进" onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('indent')}>
                        →
                      </button>
                      <button type="button" className="toolbar-icon-btn" aria-label="引用块" onMouseDown={(event) => event.preventDefault()} onClick={handleQuoteInsert}>
                        “”
                      </button>
                      <span className="toolbar-divider" aria-hidden="true" />
                      <button type="button" className="toolbar-icon-btn toolbar-link-btn" aria-label="添加链接" onMouseDown={(event) => event.preventDefault()} onClick={handleLinkInsert}>
                        链接
                      </button>
                      <button type="button" className="toolbar-icon-btn" aria-label="取消链接" onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('unlink')}>
                        Tx
                      </button>
                      <div className="toolbar-popover-root">
                        <button
                          type="button"
                          className="toolbar-icon-btn"
                          aria-label="插入表格"
                          aria-expanded={tablePickerOpen}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => {
                            saveSelection();
                            setOpenColorMenu(null);
                            setTablePickerOpen((current) => !current);
                          }}
                        >
                          表
                        </button>
                        {tablePickerOpen ? (
                          <div className="toolbar-popover table-picker" role="menu" aria-label="表格选择器">
                            <div className="table-picker-size">
                              {tablePickerSize.rows} × {tablePickerSize.columns} 表格
                            </div>
                            <div className="table-picker-grid">
                              {Array.from({ length: TABLE_PICKER_ROWS }).map((_, rowIndex) =>
                                Array.from({ length: TABLE_PICKER_COLUMNS }).map((__, columnIndex) => {
                                  const rows = rowIndex + 1;
                                  const columns = columnIndex + 1;
                                  const selected = rows <= tablePickerSize.rows && columns <= tablePickerSize.columns;
                                  return (
                                    <button
                                      key={`${rows}-${columns}`}
                                      type="button"
                                      className="table-picker-cell"
                                      data-selected={selected ? 'true' : 'false'}
                                      aria-label={`${rows} × ${columns} 表格`}
                                      onMouseEnter={() => setTablePickerSize({ rows, columns })}
                                      onMouseDown={(event) => event.preventDefault()}
                                      onClick={() => insertTable(rows, columns)}
                                    />
                                  );
                                }),
                              )}
                            </div>
                          </div>
                        ) : null}
                      </div>
                      <button type="button" className="toolbar-icon-btn" aria-label="插入图片" onMouseDown={(event) => event.preventDefault()} onClick={() => imageInputRef.current?.click()}>
                        图
                      </button>
                      <input
                        ref={imageInputRef}
                        className="hidden-file-input"
                        type="file"
                        accept="image/*"
                        onChange={handleInlineImageUpload}
                        aria-label="插入图片"
                      />
                      {selectedInlineImageId ? (
                        <>
                          <span className="toolbar-divider" aria-hidden="true" />
                          <div className="toolbar-inline-image-group" role="group" aria-label="签名图片调整">
                            <button
                              type="button"
                              className="toolbar-icon-btn"
                              aria-label="图片左对齐"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => {
                                const selected = getSelectedInlineImageElements();
                                if (!selected) {
                                  return;
                                }
                                setInlineImageWrapperPosition(selected.wrapper, 'left');
                                syncFromEditor();
                              }}
                            >
                              ←
                            </button>
                            <button
                              type="button"
                              className="toolbar-icon-btn"
                              aria-label="图片居中"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => {
                                const selected = getSelectedInlineImageElements();
                                if (!selected) {
                                  return;
                                }
                                setInlineImageWrapperPosition(selected.wrapper, 'center');
                                syncFromEditor();
                              }}
                            >
                              ↔
                            </button>
                            <button
                              type="button"
                              className="toolbar-icon-btn"
                              aria-label="图片右对齐"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => {
                                const selected = getSelectedInlineImageElements();
                                if (!selected) {
                                  return;
                                }
                                setInlineImageWrapperPosition(selected.wrapper, 'right');
                                syncFromEditor();
                              }}
                            >
                              →
                            </button>
                            <label className="toolbar-inline-image-size" aria-label="图片大小">
                              <span className="visually-hidden">图片大小</span>
                              <input
                                type="range"
                                min={INLINE_IMAGE_MIN_WIDTH}
                                max={INLINE_IMAGE_MAX_WIDTH}
                                step={INLINE_IMAGE_STEP}
                                value={selectedInlineImageWidth}
                                onChange={(event) => setSelectedInlineImageSize(Number(event.target.value))}
                              />
                            </label>
                          </div>
                        </>
                      ) : null}
                    </>
                  ) : null}
                </div>

                {editorMode === 'rich' ? (
                  <div
                    ref={editorRef}
                    className="body-input rich-body-input signature-rich-editor"
                    contentEditable
                    role="textbox"
                    aria-label="签名正文"
                    aria-multiline="true"
                    data-placeholder="在这里编辑签名内容"
                    data-empty={draft.htmlBody.trim() ? 'false' : 'true'}
                    onMouseDown={(event) => {
                      const target = event.target as HTMLElement | null;
                      const wrapper = target?.closest('.inline-image') as HTMLElement | null;
                      if (wrapper) {
                        event.preventDefault();
                        selectInlineImage(wrapper);
                        return;
                      }
                      clearInlineImageSelection();
                    }}
                    onInput={handleEditorInput}
                    onBlur={handleEditorBlur}
                    onMouseUp={handleEditorMouseUp}
                    onKeyUp={handleEditorKeyUp}
                    onPaste={() => window.setTimeout(syncFromEditor, 0)}
                  />
                ) : (
                  <textarea
                    ref={plainTextRef}
                    className="body-input plain-body-input signature-plain-editor"
                    aria-label="签名正文"
                    value={draft.textBody}
                    onChange={handlePlainTextChange}
                    placeholder="在这里编辑签名内容"
                  />
                )}

                <div className="signature-editor-footer">
                  <button type="button" className="signature-secondary-button" onClick={resetEditorToBlank}>
                    清空
                  </button>
                  <button type="submit" className="signature-primary-button" disabled={saving}>
                    {saving ? '保存中...' : '保存签名'}
                  </button>
                </div>
              </form>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
