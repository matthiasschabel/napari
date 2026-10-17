# Upstream issue draft: dock panel sizing behavior

**Status:** Active (draft; not filed)
**Last updated:** 2026-10-16
**Scope:** proposed upstream napari issue condensing `dock_sizing_design.md`. Template: Feature
Request. Paste-ready below the rule; the user edits and files it.

---

Title: Proposal: agree on how docked panels should size before changing the code again

## 🚀 Feature

Agree on the target sizing behavior for docked panels (napari's own and plugin widgets) in the
side dock areas, independent of how it is implemented, and then implement against it.

## Motivation

We have changed dock sizing three times in two releases, one symptom at a time:

- #9393 removed the auto-added bottom stretch, which made stacked short forms "socially distance", and capped docked widgets at their size hint instead.
- That made the layer list leave empty space (#9447) and made the layer controls and plugin panels non-resizable (#9483), so #9393 and #9447 were reverted in 0.9.1 (#9484).
- #9462 tried to keep the cap only where nothing could grow. On top of the revert, the left docks still can't be resized, and narrower versions of the cap break floating panels (can't be made taller), tab groups (the group shrinks while a compact tab is active) and forms that gain content after docking.

Every one of these came from two things. A sizing preference was expressed through a Qt mechanism that acts as a hard limit everywhere (a spacer, a maximum height), and it was decided once at docking instead of tracking the content. I think we'd do better to agree on the behavior first.

## Pitch

The core idea, which AppKit uses (`automaticMaximumThickness`): **a panel's role steers only the sizes napari picks automatically; it never limits what the user can do.**

- **Role.** Panel content is *growable* (lists, plots, text: more height shows more) or *compact* (forms, button rows: more height can only be blank). The role follows the content as it changes; it is never decided once.
- **Spare height** goes to growable panels. Compact panels stay at their preferred height. If nothing in a column can grow, panels pack at the top with blank space after the last one.
- **User resizing always works.** Any boundary can be dragged within the panels' minimums, including making a compact panel taller (content stays at the top, blank below). Drags cascade past a panel at its minimum, as in Qt and VS Code. A lone panel can be enlarged by its bottom edge, and every panel has keyboard grow, shrink and reset.
- **User sizes are preferences, not locks**, with defined fallbacks: when the column shrinks, panels nobody sized give way first; when height comes back, user-sized panels recover first.
- **Floating panels** are ordinary windows, freely resizable. **Tab groups** keep the same height whichever tab is active.
- **Overflow scrolls**: content larger than its panel scrolls inside it; if the panels' minimums don't fit the column, the column scrolls. Nothing is squeezed below its minimum or clipped.
- **Side columns keep their width** on window resize; the canvas absorbs it. Adding, hiding or removing a panel leaves sizes the user chose alone, and user sizes persist across sessions.

### Decisions for the group

1. **How does undeclared content get its role?**
   - (a) Read Qt's size policies, live over the content's layout tree. Qt documents its default `Preferred` as "no advantage to it being larger than sizeHint()" and `Expanding` as "can make use of extra space", so this reads the author's existing intent. It fixes the #9393 reproducer without special-casing magicgui, and a wrong role only costs a drag, since roles never cap. Risk: a custom-painted widget left at the default policy starts at its size hint.
   - (b) Treat undeclared content as growable. No existing panel changes size, but #9393's reproducer keeps its gaps unless the author declares compact.
2. **What does a drag record on a compact panel?**
   - (a) Only the blank space the user added above its preferred height. Nudging the boundary next to the layer controls doesn't stop them following layer-type changes.
   - (b) Its absolute height, as for growable panels. Simpler, but any nearby drag makes a compact panel stop following its content.
3. **How are roles declared publicly**: an `add_dock_widget` argument, a property on the widget, or both?
4. **Is blank space after the last panel wanted** when nothing can grow, or is Qt's built-in behavior (stretch the last dock) acceptable? This is likely the one behavior that needs real layout work.
5. **Collapse**: should dragging a panel below its minimum collapse it to its title bar (VS Code), or just stop?
6. **Layer controls**: compact (follow each layer type's height) or growable (stable height, blank space under short layer types)?

## Alternatives

- Infer the role once at docking (#9393, #9462): goes stale when content changes (#9483).
- Express compactness as a maximum height: Qt applies it to floating windows and to whole tab groups, and blocks the user's divider.
- Decide by widget class (cap only magicgui forms): magicgui containers can hold growable widgets and change content.
- A spacer inside each compact panel (the pre-#9393 behavior): the spacer is itself growable, so compact panels compete for spare height, which is the "social distancing".
- User sizes as hard locks: no valid layout once the window shrinks.

## Additional context

Out of scope for now: top/bottom dock areas (roles may need to be per axis), side-by-side splits inside a column, and moving panels between areas.

The nested-content fix in #9462 (no stretch beside a growable widget nested in containers) is consistent with this and can go in independently.

<details>
<summary>How other applications handle this</summary>

- **VS Code** splitview (`src/vs/base/browser/ui/splitview/splitview.ts`): views declare min, max and priority; spare space is distributed by priority and left empty when every view is at its max; views report constraint changes live (`onDidChange`); the view scrolls when the sum of minimums exceeds its size; the sidebar keeps its width on window resize.
- **Qt** (`qdockarealayout.cpp`): spare height goes to docks whose layouts expand; when nothing can take it, the last dock is stretched; a tab group's maximum is the *intersection* of its tabs' maximums, so one capped tab caps the group. The [QDockWidget docs](https://doc.qt.io/qt-6/qdockwidget.html) say constraints "change depending on whether it is docked".
- **Qt Advanced Docking System**: tab-area minimum is the largest tab minimum, maximums are ignored; content is wrapped in a scroll area by default; floating opens at the docked size.
- **AppKit**: `NSSplitViewItem.automaticMaximumThickness` caps automatic sizing only; "the user can still resize the item ... by dragging the divider".
- **3D Slicer** puts a spacer below short module panels; **JetBrains** remembers each tool window's size; **GTK** `GtkPaned` has per-child resize/shrink flags.

</details>

<details>
<summary>Scenarios to check in a live window</summary>

1. Two `@magicgui` forms in one column: packed at the top, blank below.
2. Form above a list: the list takes all spare height.
3. Drag between layer controls and layer list: works both ways.
4. Plugin panel that fills its content after docking: resizable throughout.
5. Compact panel floated and resized to 400 px: window is 400 px.
6. Compact and growable tabs in one group: switching tabs moves nothing.
7. Form gains a text box after docking: it can take spare height.
8. Window narrowed: side columns keep their width until the canvas hits its minimum.
9. Column shorter than the panels' minimums: the column scrolls.
10. Two panels the user sized to 300 px, column 600 to 500 to 600 px: 250/250, then 300/300.
11. Three panels with the middle at its minimum, drag the top boundary: top grows, bottom shrinks.
12. Restart with one monitor fewer: sizes restored, floating windows on a visible screen.

</details>
