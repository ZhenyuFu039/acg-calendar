'use strict';
/* Shared view context. Modules consume host data; game providers never own UI. */
window.PlaytimeWorkspace = (() => {
  const listeners = new Set();
  let data = null, eventOrder = new Map();
  const modules = new Map();
  const api = {
    accept(state) { data = state; eventOrder = new Map((state.event_order || []).map((id,i)=>[id,i])); for (const fn of listeners) fn(state); },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    game(id) { return data?.games.find(g => g.id === id); },
    get view() { return data?.workspace.view; },
    selected() { return data?.workspace.view.selected_games || []; },
    register(id, render) {
      if (modules.has(id)) throw Error('Duplicate module: ' + id);
      modules.set(id, render);
      return () => modules.delete(id);
    },
    renderModules() {
      for (const spec of data?.workspace.layout.modules || []) {
        const node = document.querySelector(`[data-module="${spec.id}"]`);
        if (node) { node.hidden = false; node.style.order = data.workspace.layout.modules.indexOf(spec); }
        modules.get(spec.id)?.({state: data, spec});
        if (node) node.hidden ||= !spec.visible || spec.collapsed;
      }
      const theme = data?.workspace.theme;
      if (theme) {
        document.documentElement.dataset.theme = theme.preset;
        document.documentElement.style.setProperty('--green', theme.accent);
        document.documentElement.style.setProperty('--card-opacity', theme.card_opacity);
      }
    },
    compare(a, b) {
      return (eventOrder.get(a.id) ?? Infinity) - (eventOrder.get(b.id) ?? Infinity) || a.id.localeCompare(b.id);
    }
  };
  return api;
})();
