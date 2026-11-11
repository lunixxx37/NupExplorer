// Virtualized, lazily loaded tree (handles folders with 20,000+ entries).
import { formatSize } from './api.js';
import { icon, TYPES } from './icons.js';

const ROW = 28;

export class Tree {
  constructor(container, { load, onSelect, onContext, onOpenInTree }) {
    this.el = container;
    this.load = load;
    this.onSelect = onSelect;
    this.onContext = onContext;
    this.onOpenInTree = onOpenInTree; // search results: expanding a folder/archive opens it in the normal tree
    this.roots = [];
    this.rows = [];
    this.flat = null;           // search results mode
    this.selected = null;
    this.el.classList.add('tree');
    this.spacer = document.createElement('div');
    this.spacer.className = 'tree-spacer';
    this.layer = document.createElement('div');
    this.layer.className = 'tree-layer';
    this.el.append(this.spacer, this.layer);
    this.el.addEventListener('scroll', () => this.render());
    new ResizeObserver(() => this.render()).observe(this.el);
    this.el.addEventListener('keydown', (e) => this.onKey(e));
    this.el.addEventListener('click', (e) => this.onClick(e));
    this.el.addEventListener('dblclick', (e) => this.onDblClick(e));
    this.el.addEventListener('contextmenu', (e) => {
      const n = this.nodeFromEvent(e);
      if (!n) return;
      e.preventDefault();
      this.select(n, false);
      this.onContext && this.onContext(n, e);
    });
  }

  static makeNode(item, depth, parent) {
    const expandable = item.kind === 'dir' || item.type === 'archive';
    return { ...item, depth, parent, expandable, expanded: false, loaded: false, loading: false, children: [] };
  }

  async setRoot(path = '') {
    this.flat = null;
    const items = await this.load(path);
    this.roots = items.map((it) => Tree.makeNode(it, 0, null));
    this.refresh();
  }

  setFlat(items, label) {
    this.flat = items.map((it) => ({ ...Tree.makeNode(it, 0, null), showPath: true }));
    this.flatLabel = label;
    this.el.scrollTop = 0;
    this.refresh();
  }

  clearFlat() {
    this.flat = null;
    this.refresh();
    if (this.selected) this.scrollTo(this.selected);
  }

  refresh() {
    if (this.flat) {
      this.rows = this.flat;
    } else {
      const rows = [];
      const walk = (nodes) => {
        for (const n of nodes) {
          rows.push(n);
          if (n.expanded) walk(n.children);
        }
      };
      walk(this.roots);
      this.rows = rows;
    }
    this.spacer.style.height = `${this.rows.length * ROW}px`;
    this.render(true);
  }

  render() {
    const top = this.el.scrollTop;
    const h = this.el.clientHeight || 600;
    const first = Math.max(0, Math.floor(top / ROW) - 8);
    const last = Math.min(this.rows.length, Math.ceil((top + h) / ROW) + 8);
    this.layer.style.transform = `translateY(${first * ROW}px)`;
    const frag = document.createDocumentFragment();
    for (let i = first; i < last; i++) frag.append(this.renderRow(this.rows[i], i));
    this.layer.replaceChildren(frag);
  }

  renderRow(n, i) {
    const row = document.createElement('div');
    row.className = 'tree-row' + (n === this.selected ? ' selected' : '');
    row.dataset.index = i;
    row.style.paddingLeft = `${6 + n.depth * 16}px`;
    const tw = document.createElement('span');
    tw.className = 'twisty' + (n.expanded ? ' open' : '') + (n.loading ? ' busy' : '');
    if (n.expandable) tw.append(icon(n.loading ? 'spark' : 'chevron'));
    const type = n.kind === 'dir' ? 'dir' : (TYPES[n.type] ? n.type : 'other');
    const ic = icon(TYPES[type].icon, `t-${type}`);
    const nm = document.createElement('span');
    nm.className = 'name';
    // Koei Tecmo files are only named by hashes: a real name (title) replaces the hash, a description follows it
    nm.textContent = n.title || (n.showPath ? n.path : n.name);
    const extra = n.title ? (n.showPath ? n.path : n.name) : n.desc;
    if (extra) {
      const d = document.createElement('span');
      d.className = 'desc';
      d.textContent = extra;
      nm.append(d);
    }
    nm.title = n.path + (n.desc ? `\n${n.desc}` : '');
    const meta = document.createElement('span');
    meta.className = 'meta';
    meta.textContent = n.kind === 'dir' ? (n.count ?? '') : formatSize(n.size);
    row.append(tw, ic, nm);
    if (n.upd) {
      const b = document.createElement('span');
      b.className = 'badge-upd';
      b.title = 'From the update';
      b.textContent = 'UPD';
      row.append(b);
    }
    row.append(meta);
    return row;
  }

  nodeFromEvent(e) {
    const row = e.target.closest('.tree-row');
    if (!row) return null;
    return this.rows[Number(row.dataset.index)] || null;
  }

  onClick(e) {
    const n = this.nodeFromEvent(e);
    if (!n) return;
    if (e.target.closest('.twisty') && n.expandable) {
      this.toggle(n);
      return;
    }
    this.select(n, true);
  }

  onDblClick(e) {
    const n = this.nodeFromEvent(e);
    if (n && n.expandable) this.toggle(n);
  }

  async toggle(n, force) {
    if (!n.expandable) return;
    const want = force ?? !n.expanded;
    if (this.flat) {
      // search results have no children: open the folder/archive in the normal tree instead
      if (want && this.onOpenInTree) this.onOpenInTree(n);
      return;
    }
    if (want && !n.loaded) {
      n.loading = true;
      this.render();
      try {
        const items = await this.load(n.path);
        n.children = items.map((it) => Tree.makeNode(it, n.depth + 1, n));
        n.loaded = true;
      } catch (err) {
        n.loading = false;
        this.render();
        throw err;
      }
      n.loading = false;
    }
    n.expanded = want;
    this.refresh();
  }

  select(n, notify = true) {
    this.selected = n;
    // only toggle classes: replacing the row elements would break double-clicks
    for (const row of this.layer.children) {
      row.classList.toggle('selected', this.rows[Number(row.dataset.index)] === n);
    }
    if (notify && this.onSelect) this.onSelect(n);
  }

  scrollTo(n) {
    const i = this.rows.indexOf(n);
    if (i < 0) return;
    const top = i * ROW;
    if (top < this.el.scrollTop || top > this.el.scrollTop + this.el.clientHeight - ROW) {
      this.el.scrollTop = Math.max(0, top - this.el.clientHeight / 2);
    }
  }

  onKey(e) {
    const i = this.rows.indexOf(this.selected);
    const go = (j) => {
      const n = this.rows[Math.max(0, Math.min(this.rows.length - 1, j))];
      if (n) { this.select(n, true); this.scrollTo(n); }
      e.preventDefault();
    };
    switch (e.key) {
      case 'ArrowDown': go(i + 1); break;
      case 'ArrowUp': go(i - 1); break;
      case 'PageDown': go(i + Math.floor(this.el.clientHeight / ROW)); break;
      case 'PageUp': go(i - Math.floor(this.el.clientHeight / ROW)); break;
      case 'Home': go(0); break;
      case 'End': go(this.rows.length - 1); break;
      case 'ArrowRight':
        if (this.selected?.expandable && !this.selected.expanded && !this.flat) { this.toggle(this.selected, true); e.preventDefault(); }
        else if (this.selected?.expanded) go(i + 1);
        break;
      case 'ArrowLeft':
        if (this.selected?.expanded) { this.toggle(this.selected, false); e.preventDefault(); }
        else if (this.selected?.parent) { const p = this.selected.parent; this.select(p, true); this.scrollTo(p); e.preventDefault(); }
        break;
      case 'Enter':
        if (this.selected?.expandable && !this.flat) { this.toggle(this.selected); e.preventDefault(); }
        break;
      default:
    }
  }

  // Names and descriptions arrived (Koei Tecmo index ready): update the loaded nodes in place.
  async updateLabels() {
    const apply = (nodes, items) => {
      const byPath = new Map(items.map((it) => [it.path, it]));
      for (const n of nodes) {
        const it = byPath.get(n.path);
        if (it) { n.title = it.title; n.desc = it.desc; }
      }
    };
    apply(this.roots, await this.load(''));
    const walk = async (nodes) => {
      for (const n of nodes) {
        if (n.loaded && n.children.length) {
          apply(n.children, await this.load(n.path));
          await walk(n.children);
        }
      }
    };
    await walk(this.roots);
    this.render();
  }

  // Reveal a path in the normal tree (expands parents step by step).
  async reveal(path) {
    this.flat = null;
    const parts = [];
    const [top, ...arcs] = path.split('::');
    const segs = top.split('/');
    for (let i = 1; i <= segs.length; i++) parts.push(segs.slice(0, i).join('/'));
    let level = this.roots;
    let node = null;
    for (const p of parts) {
      node = level.find((n) => n.path === p);
      if (!node) break;
      if (p !== parts[parts.length - 1] || arcs.length) {
        await this.toggle(node, true);
        level = node.children;
      }
    }
    if (node && arcs.length) {
      let base = top;
      for (const inner of arcs) {
        const innerSegs = inner.split('/').filter(Boolean);
        for (let i = 1; i <= innerSegs.length; i++) {
          const isLast = i === innerSegs.length;
          const p = `${base}::${innerSegs.slice(0, i).join('/')}${isLast && !inner.endsWith('/') ? '' : '/'}`;
          const next = level.find((n) => n.path === p);
          if (!next) break;
          node = next;
          if (!isLast) { await this.toggle(node, true); level = node.children; }
        }
        base = `${base}::${inner}`;
      }
    }
    this.refresh();
    if (node) { this.select(node, true); this.scrollTo(node); }
    this.el.focus({ preventScroll: true });
  }
}
