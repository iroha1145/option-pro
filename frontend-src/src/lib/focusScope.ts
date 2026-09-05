/** Modal scopes share one stack, including owned portals and the notification host. */
type Scope = { root: HTMLElement; modal: boolean; previous: HTMLElement | null; last: HTMLElement | null };
type Portal = { root: HTMLElement; scope: Scope };
type Manager = { scopes: Scope[]; portals: Set<Portal>; refresh: () => void; stop: () => void };
const managers = new WeakMap<Document, Manager>();
const FOCUSABLE = 'a[href],area[href],button,input:not([type="hidden"]),select,textarea,iframe,[contenteditable="true"],[tabindex]';

function visible(element: HTMLElement): boolean {
  if (!element.isConnected || element.closest('[hidden],[inert],[aria-hidden="true"]') || element.matches(':disabled')) return false;
  const style = element.ownerDocument.defaultView?.getComputedStyle(element);
  return element.getClientRects().length > 0 && style?.visibility !== 'hidden' && style?.visibility !== 'collapse';
}
function rootFor(container: HTMLElement): HTMLElement {
  return container.matches('[role="dialog"],[role="alertdialog"]') ? container
    : container.querySelector<HTMLElement>('[role="dialog"],[role="alertdialog"]') ?? container;
}
function allowedRoots(doc: Document, manager: Manager, scope: Scope): HTMLElement[] {
  const roots = [scope.root, ...doc.querySelectorAll<HTMLElement>('[data-focus-allow]')];
  for (const portal of manager.portals) if (portal.scope === scope && portal.root.isConnected) roots.push(portal.root);
  // Descriptive tooltips are portals too, but need no keyboard ownership contract.
  for (const source of [scope.root, ...scope.root.querySelectorAll<HTMLElement>('[aria-describedby]')]) {
    for (const id of (source.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)) {
      const target = doc.getElementById(id);
      if (target) roots.push(target);
    }
  }
  return roots;
}
function contains(roots: HTMLElement[], target: Node | null): boolean {
  return target !== null && roots.some((root) => root === target || root.contains(target));
}
function itemsIn(roots: HTMLElement[]): HTMLElement[] {
  return [...new Set(roots.flatMap((root) => [...root.querySelectorAll<HTMLElement>(FOCUSABLE)]))]
    .filter((element) => element.tabIndex >= 0 && visible(element))
    .sort((a, b) => (a.tabIndex > 0 ? a.tabIndex : Infinity) - (b.tabIndex > 0 ? b.tabIndex : Infinity));
}
function focusInside(doc: Document, manager: Manager, scope: Scope): void {
  const roots = allowedRoots(doc, manager, scope);
  const target = scope.last && visible(scope.last) && contains(roots, scope.last)
    ? scope.last : itemsIn([scope.root])[0] ?? scope.root;
  if (target === scope.root && !target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
  target.focus({ preventScroll: true });
}
function createManager(doc: Document): Manager {
  const inert = new Map<HTMLElement, boolean>();
  const manager: Manager = { scopes: [], portals: new Set(), refresh: () => {}, stop: () => {} };
  let redirecting = false;
  const restore = () => {
    for (const [element, previous] of inert) element.inert = previous;
    inert.clear();
  };
  manager.refresh = () => {
    restore();
    const scope = manager.scopes.at(-1);
    if (!scope?.modal) return;
    const roots = allowedRoots(doc, manager, scope);
    const overlayId = scope.root.closest<HTMLElement>('[data-focus-overlay]')?.dataset.focusOverlay;
    if (overlayId) {
      for (const backdrop of doc.querySelectorAll<HTMLElement>('[data-focus-backdrop]')) {
        if (backdrop.dataset.focusBackdrop === overlayId) roots.push(backdrop);
      }
    }
    const isolate = (parent: HTMLElement) => {
      for (const child of parent.children) {
        if (!(child instanceof HTMLElement) || child.matches('script,style,link')) continue;
        if (roots.includes(child)) continue;
        if (roots.some((root) => child.contains(root))) isolate(child);
        else { inert.set(child, child.inert); child.inert = true; }
      }
    };
    isolate(doc.body);
  };
  const inPortal = (scope: Scope, target: Node | null) => [...manager.portals].some(
    (portal) => portal.scope === scope && contains([portal.root], target),
  );
  const onFocus = (event: FocusEvent) => {
    const scope = manager.scopes.at(-1);
    if (!scope || redirecting || !(event.target instanceof HTMLElement)) return;
    if (visible(event.target) && contains(allowedRoots(doc, manager, scope), event.target)) {
      if (scope.root.contains(event.target)) scope.last = event.target;
      return;
    }
    if (!scope.modal) return;
    redirecting = true;
    focusInside(doc, manager, scope);
    redirecting = false;
  };
  const onKey = (event: KeyboardEvent) => {
    const scope = manager.scopes.at(-1);
    if (!scope || event.key !== 'Tab' || event.defaultPrevented || inPortal(scope, doc.activeElement)) return;
    const items = itemsIn(allowedRoots(doc, manager, scope));
    const index = items.findIndex((item) => item === doc.activeElement);
    // Own every Tab step: native tabbing can otherwise visit controls beneath
    // aria-hidden ancestors that are deliberately absent from the accessible order.
    event.preventDefault();
    if (!items.length) focusInside(doc, manager, scope);
    else {
      const next = index < 0 ? (event.shiftKey ? items.length - 1 : 0)
        : (index + (event.shiftKey ? -1 : 1) + items.length) % items.length;
      items[next].focus({ preventScroll: true });
    }
  };
  const observer = new MutationObserver(() => manager.refresh());
  observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-describedby', 'data-focus-allow'] });
  doc.addEventListener('focusin', onFocus, true);
  doc.addEventListener('keydown', onKey, true);
  manager.stop = () => {
    observer.disconnect();
    doc.removeEventListener('focusin', onFocus, true);
    doc.removeEventListener('keydown', onKey, true);
    restore();
    managers.delete(doc);
  };
  managers.set(doc, manager);
  return manager;
}
export function activateFocusScope(container: HTMLElement, initial?: HTMLElement | null): () => void {
  const doc = container.ownerDocument;
  const manager = managers.get(doc) ?? createManager(doc);
  const scope: Scope = { root: rootFor(container), modal: rootFor(container).getAttribute('aria-modal') === 'true', previous: doc.activeElement instanceof HTMLElement ? doc.activeElement : null, last: null };
  manager.scopes.push(scope);
  manager.refresh();
  if (initial && visible(initial) && scope.root.contains(initial)) initial.focus({ preventScroll: true });
  else focusInside(doc, manager, scope);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const wasTop = manager.scopes.at(-1) === scope;
    manager.scopes.splice(manager.scopes.indexOf(scope), 1);
    for (const portal of manager.portals) if (portal.scope === scope) manager.portals.delete(portal);
    if (manager.scopes.length) manager.refresh();
    else manager.stop();
    if (!wasTop) return;
    const next = manager.scopes.at(-1);
    if (scope.previous && visible(scope.previous) && (!next || contains(allowedRoots(doc, manager, next), scope.previous))) scope.previous.focus({ preventScroll: true });
    else if (next) focusInside(doc, manager, next);
  };
}
/** Called by a portal ref before its library moves focus out of the dialog subtree. */
export function registerFocusPortal(root: HTMLElement, trigger: HTMLElement | null): () => void {
  const manager = managers.get(root.ownerDocument);
  const scope = manager && [...manager.scopes].reverse().find((candidate) => trigger && candidate.root.contains(trigger));
  if (!manager || !scope) return () => {};
  const portal = { root, scope };
  manager.portals.add(portal);
  manager.refresh();
  return () => { manager.portals.delete(portal); manager.refresh(); };
}
export function isTopFocusScope(container: HTMLElement | null): boolean {
  if (!container) return false;
  const manager = managers.get(container.ownerDocument);
  const scope = manager?.scopes.at(-1);
  if (!scope || scope.root !== rootFor(container)) return false;
  return ![...manager!.portals].some((portal) => portal.scope === scope && portal.root.contains(container.ownerDocument.activeElement));
}
