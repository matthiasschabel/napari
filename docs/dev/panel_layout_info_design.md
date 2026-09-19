# Developer panel-layout inspector

**Status:** Deferred
**Last updated:** 2026-08-27
**Scope:** `src/napari/_qt` — dock widget sizing diagnostics

## Context

Choosing size-hint, minimum, and maximum values for napari's dock widgets is guesswork.
The dock widget size-policy work on `integration` ("Keep dock widgets resizable when the
widget asks for vertical space") was slowed by having no visibility into what Qt actually
reports per panel: `sizeHint`, `minimumSizeHint`, explicit min/max, current `size()`, and
the size policies in play. A debugger shows one widget at a time and cannot show how
panels compare while a splitter is being dragged.

The idea arrived as a settings gear or info icon in each panel's title bar, doubling as a
place to store per-panel preferences. Both of those were rejected; see below.

## Current Decision

Build it as a **standalone downstream script**, touching zero napari source files.
`viewer.window.add_dock_widget` is public API (`_qt/qt_main_window.py:1229-1253`), so the
diagnostic can register itself. Enumerate panels with `self._qt_window.findChildren(QDockWidget)`,
the same call `Window._add_viewer_dock_widget` already uses (`_qt/qt_main_window.py:1319-1324`).

No env flag, no `QtViewer` property, no `conftest.py` work, no branch decision — none of
that is needed until the tool proves it earns a permanent home.

**Columns must cover both the wrapper and the inner widget.** napari overwrites the very
numbers the tool exists to report (`_qt/widgets/qt_viewer_dock_widget.py:161-174`):

- the wrapper gets `setMinimumHeight(50)` / `setMinimumWidth(50)`;
- the inner widget's policy is *rewritten* to `setSizePolicy(Preferred, vertical_policy)`,
  where `vertical_policy` depends on `_wants_vertical_space(widget_)`.

That second call destroys the widget's original policy. Reading only the wrapper reports
napari's overrides, not what the widget asked for. Capturing the pre-override value means
recording it in `QtViewerDockWidget.__init__` before the `setSizePolicy` call.

Beyond sizing, include dock area, floating state, visibility, and title-bar orientation —
which is why "layout" is the accurate word rather than "sizes".

Refresh with a coalesced ~250ms timer for v1, as `QtPerformance` does
(`_qt/perf/qt_performance.py:141-145`). Event filters per dock were proposed instead; for a
dev-only tool they add add/remove tracking and a self-feedback hazard (the tool's own dock
must be excluded) in exchange for latency nobody is measuring.

## Alternatives Considered

**A gear/info icon per panel in `QtCustomTitleBar`** (`_qt/widgets/qt_viewer_dock_widget.py:382`).
Rejected. The title bar is ~20px (`sizeHint`, line 484) and hides its title entirely in the
vertical orientation (lines 445-461); most panels would open an empty menu; and the tool's
job is cross-panel comparison, which one-panel-at-a-time chrome serves badly.

**Per-panel persisted preferences behind that gear.** Deferred. napari settings are global
pydantic models and there is no stable per-panel identity to key against — plugin
contributions can be renamed, and `add_dock_widget(my_widget)` from a script has no identity
at all. If it resurfaces, the title bar's context menu is cheaper than visible chrome: no
empty-menu problem, and it works in both orientations. Revisit when two concrete panels want
a stored setting.

**Mirroring `dockPerformance` with a lazy `QtViewer` property, factory, and registration.**
Rejected as the v1 shape. `QtPerformance()` needs nothing from the main window
(`_qt/qt_viewer.py:424-432`); a panel inspector must see every dock, including plugin docks
that enter through `Window.add_dock_widget`. Ownership belongs on `Window`/`_QtMainWindow`,
not `QtViewer`. The `dockPerformance` analogy holds only for conditional registration.
Note that `QtViewerDockWidget.__init__` still takes `qt_viewer` first and holds a weakref
(`_qt/widgets/qt_viewer_dock_widget.py:111-125`), so even a Window-owned diagnostic needs a
viewer reference to build its wrapper.

**An inline env-var check.** Wrong: `utils/config.py:1-14` already defines
`_set(env_var)` (`os.getenv(env_var) not in [None, '0']`) as the canonical parser. Use it
if a flag is ever needed.

**Naming.** `dockPanelInfo`/`QtPanelInfo` was the working name; "info" is a grab-bag word and
every field is layout-related. `QtPanelLayoutInfo` with dock title `'panel layout'` is the
current preference — the `Info` suffix stops the class reading as a `QLayout` subclass, and
"panel" matches napari's user-facing vocabulary (title bar tooltips at
`_qt/widgets/qt_viewer_dock_widget.py:417-438`, window actions at
`_qt/_qapp_model/qactions/_window.py:17-29`) where "dock widget" is the API term.
Rejected: `dockWidgetLayout` (collides with `QDockWidget`/`add_dock_widget`), `dockLayout`
(reads as `QLayout`), `dockWindowLayout` (stutters; scope is per-panel).

## Deferred Work

- Whether to upstream at all. No maintainer has asked for a layout inspector. Build it
  downstream, use it on at least two unrelated sizing investigations, and only then open a
  design issue.
- Whether GammaRay's Widget Layout tool already covers this. It was raised as making the
  tool redundant, but attaching GammaRay to a PyQt/PySide app needs an ABI-matched build and
  was **not** verified against napari's supported bindings. Worth a short spike before any
  upstream conversation; not a blocker for a downstream script.
- Capturing pre-override size policies would require a change inside
  `QtViewerDockWidget.__init__`, which is the first thing here that is not purely additive.

## Next Steps

1. Write the script; register via `viewer.window.add_dock_widget`.
2. Use it to pick the size values the size-policy work needs.
3. Reassess: if it is still useful after two investigations, revisit ownership
   (`Window`, per above), gating (`utils/config.py:_set`), and the upstream question.

## Provenance

Design reviewed by Codex (gpt-5.6-sol, reasoning effort xhigh) via `/collaborative-refinement`
on 2026-08-27. Every claim above about napari internals was verified against the source.
Report: `.git/collaborative-refinement/logs/20260827-131943-408659-claude-to-codex-plan-review.md`
(git-local, not committed). Any commit deriving from this note should carry
`Reviewed-By: Codex (gpt-5.6-sol, reasoning effort xhigh)`.
