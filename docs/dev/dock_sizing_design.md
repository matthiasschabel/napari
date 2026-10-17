# Dock panel sizing: behavioral concept

**Status:** Active
**Last updated:** 2026-10-16
**Scope:** How docked panels (napari's own and plugin-provided) in the left and right dock columns
take, give up and keep height, including tab groups and floating panels. Behavior only;
implementation is out of scope except for the non-normative feasibility notes at the end. Top
and bottom areas, nested splits inside a column, and moves between areas are listed under
Out of Scope.

## Context

Dock sizing has been changed by implementation, one symptom at a time. An auto-added bottom
stretch kept short forms at the top of their panel but left large empty bands between stacked
panels (upstream #9393). Capping every compact widget at its size hint fixed that and broke
resizing of the layer controls and of plugin panels whose content arrives after docking (#9483),
so it was reverted (#9484). Narrower versions of the cap still broke floating panels, tab groups
and forms that change content (measured on the #9462 branch).

Those regressions came from two things: expressing a sizing *preference* through a Qt mechanism
that acts as a hard limit everywhere (a spacer, a maximum height), and deciding it once instead
of tracking the content. This note states the behavior first, from conventions in mature
docking applications, so that any implementation can be judged against it.

Each behavior is marked **[precedent]** where mature applications agree, or **[napari choice]**
where they disagree or are silent and this note picks one. Choices are proposals for maintainers,
not findings. Two choices are flagged as **decisions needed** because reviewers judged them
product calls (B1 role resolution, B4 compact user sizes).

### Vocabulary

- **Column**: a left or right dock area holding panels stacked vertically.
- **Panel**: one docked unit: title bar plus content area.
- **Tab group**: panels sharing one slot in a column, one visible at a time.
- **Role**: what the content gains from extra height. **Growable** content (lists, trees, plots,
  text, images) shows more. **Compact** content (forms, button rows, a fixed set of controls)
  shows nothing more; extra height can only be blank.
- **Minimum height**: the smallest content area at which the panel is still usable, which may be
  a scrolling viewport onto larger content. It is not the content's full extent.
- **Preferred height**: the height the panel should get when nothing else constrains it. For
  compact content, its full extent. For growable content, a reasonable initial size (a list's
  preferred height is not the height of all its rows). Without a declaration, the toolkit's size
  hint.
- **Panel extent**: title bar plus content height. Minimums and preferences below include the
  title bar.
- **Automatic sizing**: sizes the application chooses (first show, window resize, panels added,
  removed, shown or hidden, content changes). **User sizing**: sizes the user chose by dragging a
  boundary, a window edge, or a keyboard resize action.

## Current Decision

### B1. Role comes from the content, kept current, and never caps anything

**Decision needed.** Recommended resolution order:

1. **Explicit declaration** by the content or the code that built it (a dock-level argument or a
   property on the content; open question 2).
2. **The toolkit's own sizing vocabulary**, read from the content and everything in its layout,
   re-read whenever the content's layout changes. In Qt this vocabulary already states the role:
   `Expanding`, `MinimumExpanding` and `Ignored` mean the widget "can make use of extra space";
   `Preferred` (Qt's default), `Minimum`, `Maximum` and `Fixed` mean there is "no advantage to it
   being larger than sizeHint()" ([QSizePolicy](https://doc.qt.io/qt-6/qsizepolicy.html#Policy-enum)).
   Content is growable if anything in it can use extra space, otherwise compact.

The host never decides a role from the content's class or toolkit (magicgui or not), never
decides it once and keeps it, and never changes the content's own size constraints to enforce
it. A role only steers automatic sizing (B2); it never limits what the user can do (B3).

Why this is safe where #9393 and #9462 were not: those turned the role into a maximum height,
so a wrong guess left a panel that could not be resized; and they decided once, so content that
arrived later was misjudged (#9483, E4). Here a wrong role costs a panel its share of spare
height until the user drags it, and the role follows the content.

What it covers: #9393's reproducer (two `@magicgui` forms, no declaration) packs without napari
special-casing magicgui. What it risks: a custom-painted widget left at Qt's default `Preferred`
that would benefit from height (a hand-drawn plot) is treated as compact and starts at its size
hint; its author fixes it by declaring growable or setting `Expanding`, and the user can enlarge
it meanwhile.

**Alternative (compatibility default):** treat undeclared content as growable, as every panel
behaves today. No existing panel changes size, but #9393's reproducer keeps its blank bands unless
its author declares compact.

Evidence: VS Code views and AppKit split items each declare min, max and priority; Qt's
documentation puts size constraints on the content, not on the dock. A binary role is this note's
simplification of those priority schemes.

### B2. Automatic sizing: who gets spare height

When a column is taller than its panels' preferred heights:

- Spare height goes to growable panels [precedent: priority-based distribution in VS Code and
  AppKit], shared in proportion to their current heights [napari choice; VS Code distributes
  sequentially by priority instead].
- Compact panels stay at their preferred height.
- If no growable panel is visible, the leftover is blank column space **after the last panel**,
  with panels packed at the top in order [napari choice; VS Code leaves leftover empty, Qt
  stretches the last dock, 3D Slicer puts a spacer inside the panel]. With B1's recommended
  resolution this is what #9393 asked for. It is the one behavior here likely to need special
  layout work (feasibility notes), so maintainers should confirm it is wanted (open question 3).

When the column is shorter than the preferred heights but fits every minimum, see B4.

### B3. Compact limits automatic sizing only; the user can always resize

Every boundary between panels can be dragged within the panels' minimums. A user may make a
compact panel taller than its preferred height; its content stays top-aligned and the extra shows
as blank space at the bottom of that panel. The same applies to content that declares a maximum:
the content stops at its maximum and the panel shows blank space below. Role and content maximums
never cap the panel itself, so they cannot cap a tab group (B9) or a floating window (B6).

[precedent] AppKit `automaticMaximumThickness` caps automatic sizing while "the user can still
resize the item ... by dragging the divider". Brisvag's review of #9462 (left docks could no
longer be resized) is the failure this prevents.

Interaction details:

- A drag cascades: when the panel next to a boundary reaches its minimum, the drag continues into
  the panels beyond it [precedent: Qt separator moves and VS Code sash drags both cascade].
- When blank space follows the last panel (B2), the last panel's bottom edge is a resize boundary
  into that space. Shrinking it back returns the space.
- Every panel offers keyboard-accessible "grow", "shrink" and "reset size" actions (for example in
  its title-bar menu). Reset returns the panel to automatic sizing.
- Dragging below a minimum stops at the minimum. Collapse-on-drag is open question 4.
- A boundary that cannot move shows no resize cursor.

### B4. User sizes are preferences with defined fallbacks

**What a drag records** (decision needed for compact panels). A drag records a user size on every
growable panel whose height it changed. For a compact panel it records the **blank space the user
added** above its preferred height, not an absolute height, and records nothing if the panel ends
at its preferred height. So nudging the boundary next to compact layer controls does not freeze
them: they still follow their content (B7), plus any blank space the user deliberately added.
Alternative: record absolute heights on every panel a drag changed, which is simpler but makes a
compact panel stop following its content after any nearby drag.

A user size is a preference, not a lock [precedent: VS Code rescales sized views proportionally;
Qt relaxes `KeepSize` under constraint]. When the column's height or the panels' needs change:

1. **Recovery first.** Height that becomes available (window grows, a panel closes or shrinks)
   first goes back to panels pushed below their user size by an earlier shortage, in the reverse
   of the order in rule 3.
2. **Then extra height** goes to growable panels without a user size; if every growable panel has
   one, to all growable panels in proportion to their user sizes, which are updated accordingly;
   if there are no growable panels, after the last panel (B2).
3. **Shortage** (window shrinks, a panel is added or grows, while every minimum still fits):
   panels give up height toward their minimum in this order: growable panels without a user size,
   then compact panels down to their preferred height, then user-sized panels in proportion to
   their excess over their minimum, then compact panels toward their minimum.

Worked example: two user-sized growable panels at 300 px with 100 px minimums, column shrinks to
500 px. Rule 3 shrinks both in proportion to their excess (200 px each), giving 250 px each. When
the column returns to 600 px, rule 1 restores both to their user size of 300 px.

### B5. Side columns keep their width

A column starts at the largest preferred width among its panels, within a default range
[napari choice]. Resizing the main window changes the central canvas; side columns keep their
width [precedent: VS Code sidebar low priority and editor high; Qt-ADS stretch only on the
central branch; AppKit holding priority]. When the canvas would fall below its own minimum,
columns give up width toward their minimums, proportionally to their excess, and recover in
reverse when the window grows.

### B6. Floating panels are ordinary windows

A floated panel opens at its last floating size, or at its docked size the first time [precedent:
Qt-ADS floats at the docked size; napari choice for the order]. The user can resize it freely
above its minimum; role does not limit it (B3). Docking it back restores the user size it had when
it was last docked; a panel that had no user size returns to automatic sizing. Floating and
re-docking never creates a user size by itself.

Evidence: Qt documents that dock constraints "change depending on whether it is docked".
Measured failure E3: a capped form could not be enlarged once floating.

### B7. Content changes are handled live

Role, minimum and preferred height may change at any time (a form gains a text box, a layer
switches type, a histogram is shown or hidden). The column re-applies B2 and B4 with the new
values [precedent: VS Code views emit `onDidChange` and the split view re-lays out]:

- A compact panel follows its preferred height, plus any blank space the user added (B4).
- A growable panel keeps its height or share.
- A panel whose minimum now exceeds its height grows, taking height by B4 rule 3.
- A growable panel with a user size keeps it unless its minimum forces a change.

Measured failures: E4 (a form gaining a text box stayed capped) and the layer controls (#9483),
both from deciding once.

### B8. Overflow scrolls at two levels

- **Content larger than its panel** scrolls inside the panel. The panel's minimum is its smallest
  usable viewport, not the content's extent [precedent: Qt-ADS wraps content in a scroll area and
  keeps dock minimums small; 3D Slicer scrolls its module panel].
- **Panels larger than their column**: when the sum of panel minimums (title bars included)
  exceeds the column's height, the column itself scrolls, and every panel keeps its minimum
  [precedent: VS Code's split view scrolls when the sum of minimums exceeds its size].

Content is never compressed below its minimum or clipped without a way to reach it. Accepted cost
[precedent, same in VS Code]: when the column scrolls, the wheel over a scrolling panel scrolls
that panel first; the column scrolls once the panel reaches its end, or from title bars and blank
space.

### B9. A tab group is one slot whose height does not depend on the active tab

A tab group behaves as a single panel. Its minimum is the largest of its tabs' minimums; its
preferred height is the largest of its tabs' preferred heights; it is growable if any tab is
growable, otherwise compact. Switching tabs never changes the group's height or moves another
boundary. A shorter tab is top-aligned with blank space below [precedent: Qt-ADS combines
minimums and ignores maximums; Qt intersects maximums, which is the failure: measured E6, a group
collapsed to 70 px whenever a capped tab was active].

### B10. Adding, hiding and removing panels disturbs as little as possible

A newly docked compact panel starts at its preferred height. A newly docked growable panel gets an
equal share of the height held by growable panels without a user size; if there are none, its
preferred height. Height it needs is taken by B4 rule 3. User-sized panels are not re-split
[napari choice; Qt-ADS splits all areas equally by default, which discards sizes the user chose].
A closed or hidden panel's height is redistributed by B4 rules 1 and 2. Re-showing a hidden panel
restores its previous height as far as B4 allows [precedent: Qt `KeepSize`].

### B11. Sizes persist as preferences

User sizes (B4), column widths (B5) and floating geometry (B6) are saved with the window layout
and restored at the next session [precedent: VS Code per workspace, Qt `saveState`, AppKit
`autosaveName`, JetBrains "Remember size for each tool window"]. On restore they are treated as
preferences: re-validated against current minimums and roles, and floating windows that would
open off-screen (a monitor removed, a different scale factor) are moved and clamped onto a visible
screen.

## Acceptance scenarios

Each should hold in a live window and doubles as a test plan. "Compact" and "growable" in a
precondition mean the role resolved by B1, whether declared or read from size policies.

| # | Scenario | Preconditions | Expected |
|---|---|---|---|
| S1 | Two `@magicgui` forms, column otherwise empty (#9393) | No declaration; both resolve compact (B1 recommended) | Both at preferred height, packed at the top, blank below (B2) |
| S2 | Form above a list | Form compact, list growable | List takes all spare height; form at preferred height (B2) |
| S3 | Drag the boundary between layer controls and layer list, both ways | | Moves within minimums; with controls compact, the list keeps a user size and the controls record only added blank space (B3, B4) |
| S4 | Plugin panel fills its content after docking (#9483) | No declaration | Role follows the content as it arrives; panel resizable throughout (B1, B7) |
| S5 | Compact panel floated, window resized to 400 px | | Window is 400 px; content at top, blank below (B6) |
| S6 | Compact tab and growable tab in one group; switch tabs | | Group height and all boundaries unchanged (B9) |
| S7 | Form gains a text box after docking | Text box uses `Expanding` (Qt's default for text edits), or owner re-declares growable | Panel becomes growable and takes spare height (B1, B7) |
| S8 | Growable widget nested inside containers | Content's own layout lets that widget grow | Panel takes spare height; no blank band inside the content (B2) |
| S9 | Window narrowed and widened | | Column widths unchanged until the canvas hits its minimum, then recover (B5) |
| S10 | Column shorter than the sum of panel minimums | | Column scrolls; every panel reachable at its minimum (B8) |
| S11 | Two user-sized 300 px growable panels, column 600 to 500 to 600 px | 100 px minimums | 250 and 250, then 300 and 300 (B4) |
| S12 | Lone compact panel with blank space below; enlarge it | Panel compact | Drag its bottom edge, or use the keyboard grow action (B3) |
| S13 | User sizes panels, restarts with one monitor fewer | | Docked sizes restored within constraints; floating windows on a visible screen (B11) |
| S14 | Layer selection switches the layer controls to a taller layer type | Controls compact; user added no blank space | Controls follow the new preferred height; the list gives up the height (B7) |
| S15 | Three panels, middle at its minimum; drag the top boundary down | | Drag cascades: top grows, bottom shrinks, middle unchanged (B3) |
| S16 | Growable plugin panel docked beside a 700 px automatic layer list | Panel growable | Each gets 350 px (B10) |
| S17 | Scroll wheel over the layer list while the column scrolls | Column overflowing | List scrolls to its end, then the column scrolls (B8) |

## Alternatives Considered

- **Decide the role once, when docking** (#9393, #9462). Rejected: the decision goes stale as soon
  as content changes (#9483, E4).
- **Express a role as a maximum height on the panel or its content.** Rejected: toolkits apply
  a maximum everywhere the panel goes. In Qt that caps floating windows (E3), whole tab groups
  (E6) and the user's boundary (brisvag's review).
- **Decide the role from the content's class or toolkit** (the #9462 rework capped only magicgui
  forms). Rejected: magicgui containers can hold growable widgets and change content; the class
  says nothing about the role.
- **A spacer inside each compact panel** (napari before #9393, 3D Slicer). Rejected: the spacer
  is itself growable, so compact panels compete for spare height and show it as blank bands
  between stacked panels.
- **Stretch the last panel when nothing can grow** (Qt's built-in behavior). Rejected for B2, but
  it is the cheapest fallback if maintainers decide trailing blank space is not worth the work.
- **Re-split all panels equally when one is added** (Qt-ADS default). Rejected for B10: it
  discards sizes the user chose.
- **User sizes as hard locks.** Rejected: no feasible allocation once the column shrinks (B4
  worked example).
- **Per-panel scrolling only.** Rejected: cannot fit panels whose minimums exceed the column (B8).

## Open Questions

1. **B1 role resolution** (decision needed): read Qt's size-policy vocabulary for undeclared
   content (recommended; fixes #9393 out of the box), or treat undeclared content as growable
   (compatibility; no panel changes size).
2. **B4 compact user sizes** (decision needed): record only blank space added to compact panels
   (recommended), or absolute heights on every panel a drag changed.
3. **How roles and preferred heights are declared publicly**: a dock-level argument, a property on
   the content, or both.
4. **Trailing blank space (B2)**: wanted, or is Qt's stretch-the-last-panel acceptable?
5. **Collapse**: whether dragging below a minimum collapses a panel to its title bar, as in
   VS Code, or stops (the baseline here).
6. **Role of napari's own panels**: with B1 as recommended, the layer controls resolve compact and
   follow each layer type's preferred height (S14); declaring them growable instead gives a stable
   height across layer selections with blank space under short layer types.

## Out of Scope

- **Top and bottom areas.** The same rules likely apply on the horizontal axis, but roles here are
  for height only; content can be compact vertically and growable horizontally. A follow-up
  should decide whether roles are per axis.
- **Nested splits within a column** (panels side by side inside a side column).
- **Moving a panel between a side column and a top or bottom area**, beyond treating it as a fresh
  insertion (B10).

## Deferred Work

- Persistence format and migration (B11).
- The developer panel-layout inspector (`panel_layout_info_design.md`) would make scenarios cheap
  to verify by eye.
- Maintainers have mentioned upcoming work on scrolling dock widgets; B8 should be checked
  against it when it appears.

## Next Steps

1. Agree on B1 to B11 and the open questions with napari maintainers before any implementation.
2. Turn S1 to S17 into live-window checks (the probes used for #9462 cover S1, S3, S5, S6, S7).
3. Design the implementation against this note; the feasibility notes below are a starting point.

## Review History

Reviewed in two sequential passes on 2026-10-16: Codex (gpt-6-astra, high effort), then Claude
(claude-fable-5-1, high effort). The second pass confirmed that the first pass's findings were
resolved and re-verified the cited sources. All non-blocking findings from both passes were
incorporated. The two product decisions they raised are open questions 1 and 2.

## Evidence

- VS Code: `src/vs/base/browser/ui/splitview/splitview.ts` (`distributeEmptySpace`,
  `LayoutPriority`, view `minimumSize`/`maximumSize`, `onDidChange`, proportional layout, sash
  drag cascade, scrolling); `src/vs/workbench/browser/layout.ts`;
  `src/vs/workbench/browser/parts/sidebar/sidebarPart.ts`;
  `src/vs/workbench/services/views/common/viewContainerModel.ts`.
- Qt: [QDockWidget](https://doc.qt.io/qt-6/qdockwidget.html) ("Size constraints should not be
  set on the QDockWidget itself, because they change depending on whether it is docked");
  [QSizePolicy::Policy](https://doc.qt.io/qt-6/qsizepolicy.html#Policy-enum);
  `qtbase/src/widgets/widgets/qdockarealayout.cpp` (`QDockAreaLayoutInfo::fitItems`,
  `minimumSize`, `maximumSize`, separator move, `saveState`, `KeepSize`);
  [QMainWindow::resizeDocks](https://doc.qt.io/qt-6/qmainwindow.html#resizeDocks).
- Qt Advanced Docking System: `src/DockContainerWidget.cpp`, `src/DockAreaWidget.cpp`
  (`updateMinimumSizeHint`), `src/FloatingDockContainer.cpp`, `src/DockWidget.h`
  (`eInsertMode::AutoScrollArea`, minimum size hint modes).
- AppKit: `NSSplitViewItem.holdingPriority`, `automaticMaximumThickness`; `NSSplitView.autosaveName`.
- Apple Human Interface Guidelines, split views.
- GTK4 [GtkPaned](https://docs.gtk.org/gtk4/class.Paned.html) `resize-*-child`, `shrink-*-child`.
- 3D Slicer: `Base/QTGUI/Resources/UI/qSlicerModulePanel.ui`, `qSlicerModulePanel.cxx`.
- JetBrains: [Tool windows](https://www.jetbrains.com/help/idea/manipulating-the-tool-windows.html).
- Blender manual: tabs and panels; list views.
- Not verified (sources unavailable): Adobe Photoshop and Illustrator panels, Fiji, QuPath,
  Microsoft Fluent, KDE HIG.
- napari measurements (live window, 1400x900, macOS, PyQt6) from the #9462 rework: brisvag's two
  review points and edge cases E1 to E6.

## Feasibility notes for Qt (non-normative)

Hypotheses to verify in a live window, not decisions:

- **Role to dock behavior.** Make a dock's layout expand vertically only when its content resolves
  growable (B1), without touching the content's own policy and without any maximum height.
  `QDockAreaLayoutInfo::fitItems` gives spare height to docks whose layouts expand, which would
  implement B2 for mixed columns, and B3, B6 and B9 hold by construction because nothing is capped.
- **Live role.** Re-resolve the role on the content's layout-change events; Qt already delivers
  `LayoutRequest` to the dock, which napari handles since #9532.
- **Top alignment without a spacer.** Keep compact content at the top of a taller panel with
  layout alignment rather than an expanding spacer, so the panel stays non-expanding.
- **All-compact columns.** Qt stretches the last dock when nothing expands, which conflicts with
  B2. Options: a trailing filler in the column, or accepting Qt's behavior (open question 4).
- **User sizes.** Qt keeps a dock's size once set, which is close to B4 but has no notion of
  user-sized versus automatic panels, blank space added to compact panels, or the B4 ordering;
  that needs napari-side state, with `resizeDocks` to apply sizes and `saveState`/`restoreState`
  to persist them.
- **Column scrolling (B8)** has no QMainWindow equivalent and would need a different column
  container. Per-panel scroll areas, as Qt-ADS does, are straightforward.
