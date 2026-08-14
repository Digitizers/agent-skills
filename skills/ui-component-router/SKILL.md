---
name: ui-component-router
description: Router for sourcing UI components — picks ONE source among the shadcn/ui registry (MCP or CLI), 21st.dev magic-mcp, and Ant Design, so a component need lands on one system instead of a hand-rolled lookalike or a mixed component zoo. Read this FIRST whenever a task needs a UI component or section — a button, dialog, data table, form, pricing section, hero, navbar, dashboard widget — or when the user asks to "add a component", "build a section", "use shadcn", "find a component for", "תוסיף קומפוננטה", "תבנה סקשן", "צריך טבלה/טופס/דיאלוג", "חפש קומפוננטה". Not for designing a brand or page from scratch (that is design work, not component sourcing), not for choosing the project's framework, and not for installing/configuring the tools themselves.
compatibility: General principle — the shadcn CLI (`npx shadcn@latest add`) needs only npx and works in any React/Tailwind project; the shadcn-ui MCP and magic-mcp are optional accelerators that may not be connected, and magic-mcp is metered. No tool named here is assumed present — probe before routing (see the availability rule).
---

# UI Component Router

One decision, made once: **which component system serves this need.** Never
hand-roll a lookalike of a component a system already ships, and never mix
component systems in one project.

## The sources

| Source | What it is | Local/Cloud | Cost | Use for |
|--------|-----------|-------------|------|---------|
| **shadcn/ui CLI** (`npx shadcn@latest add <name>`) | copy-in registry, code lands in the repo | local | free | primitives and registry blocks in React/Tailwind — button, dialog, table, form, login, sidebar, dashboard… |
| **shadcn-ui MCP** | MCP serving the same registry (source + demos + metadata) | MCP | free | inspecting a component's source/demo before adding; block/pattern lookup |
| **magic-mcp** (21st.dev) | MCP generating full sections from the community library | MCP | metered | complete sections/patterns — pricing, hero, landing blocks — or "find me a component like X" community search |
| **Ant Design** (`antd`) | full component library, imported as a dependency | local | free | projects already on antd / enterprise-style data-heavy UIs that chose it |

## Decision order

Stop at the first match:

0. **Did the user explicitly choose a source** — an instruction or stated
   preference ("use antd for this dialog", "add the shadcn table", "pull it
   from 21st.dev")? → **that source.** Explicit user choice beats every
   generic rule below — a chosen system is never re-routed to a "better"
   one. **Naming candidates is not choosing**: a question that merely
   mentions sources ("should we use shadcn or antd here?") selects nothing
   — it runs through the generic order below and the router answers with
   its pick and the step that chose it. When the chosen source differs from
   the project's existing component system, the ask itself is the explicit
   say-so the one-system rule requires, but **announce the mix** (name both
   systems and the consequence) before adding. **Honoring is bounded by
   feasibility**: when the chosen source's output cannot run in the
   project's stack (a React-emitting source in a Vue/Svelte project;
   shadcn without Tailwind), do not add unusable code and do not silently
   reroute either — report the incompatibility and hand the decision back.
   Say-so licenses mixing systems; it cannot make incompatible code run.
1. **Does the project already have a component system?** Existing
   `components/ui/` with shadcn conventions → shadcn. `antd` in
   `package.json` → Ant Design. Another system (MUI, Chakra, Mantine,
   project-internal design system) → **use that system**; this router adds
   nothing and must not introduce a second one. The existing stack wins
   over everything below — a "better" component from another system is
   never a reason to mix. **When that system does not cover the need**
   (a specialized component or section it simply doesn't ship), the route
   is a **declared custom build following that system's conventions and
   primitives** — the coverage gap changes the build, never the system;
   steps 2–3 stay closed to a project that already has one.
   *Steps 2–3 are stack-gated: every source below emits React code (shadcn
   additionally assumes Tailwind), so they apply only where that code can
   run. A non-React stack (Vue, Svelte, …) with no component system of its
   own skips them entirely and lands on step 4 — an incompatible source is
   never a match, however well the need's shape fits.*

2. **Anything the shadcn registry ships, in a React/Tailwind project** —
   standard primitives (button, dialog, table, form, tabs, dropdown,
   toast…) **and registry blocks** (login/signup, sidebars, dashboard
   shells, calendars…)? → **shadcn**. For any need that isn't an obvious
   primitive — a section, a pattern, a "something like X" search — this
   step includes an operative check, not an assumption: **look it up in
   the registry first** — the shadcn-ui MCP listing when it answers,
   otherwise the public registry index — and only a miss falls through to
   step 3. The lookup is free; step 3 is not. The add itself goes through
   `npx shadcn@latest add <name>` — the free path that works in any
   session.
3. **A need in a React project that the registry lookup in step 2 missed**
   — a full section or pattern (pricing section, hero, landing block…) or
   a community search ("something like X")? → **magic-mcp**. magic-mcp is
   metered — spend only for what the free paths can't produce; the step-2
   lookup is a prerequisite for every branch of this step, and reaching it
   without that lookup is a routing error.
4. **Nothing fits** (non-React stack with no system, unique bespoke need)?
   → **declared custom build**: say explicitly that no source covers this,
   then build following the project's own conventions. A custom build is a
   stated decision, never a silent default.

*(Ant Design has no generic step of its own: an antd project is caught by
step 1, an explicit antd ask by step 0.)*

## Availability rule (inherited from browser-router)

No tool named here is assumed present. **Confirm the chosen tool actually
answers in this session before routing to it** — an MCP can be unloaded, a
key revoked, credits exhausted. A tool that doesn't answer is not a route.

**Fallbacks apply only to routes the router chose (steps 1+).** When the
unavailable source was the user's explicit choice (step 0), there is no
silent substitute at any layer: report the inability and get their say-so
before rerouting — substituting changes the provenance the user asked for,
and can smuggle in a system they never approved. Otherwise, fall back by
layer:

- **shadcn-ui MCP missing** → the CLI path (`npx shadcn@latest add`) is the
  same registry and needs only npx; for source inspection, the registry is
  public — fetch the component page/source directly.
- **magic-mcp missing or out of credits** → in a React/**Tailwind** project,
  compose the section from shadcn primitives (free); a React project
  without Tailwind gets the declared custom build (step 4) instead — the
  stack gate binds fallbacks too, shadcn assumes Tailwind. Never
  substitute a different metered service without saying so.
- **npx/network unavailable** → say so. Deliver the component as a patch
  **only when the registry source is actually readable** — a cached copy,
  a vendored registry, an MCP that still answers. With no reachable source
  there is nothing to copy from: report the blockage rather than
  reconstructing the component from memory — that fabricates provenance
  and is the hand-rolled lookalike this router exists to prevent.

## Design constraints (conditional design-dna link)

If a client **DESIGN.md** exists for the project (see the `design-dna`
skill, when loaded — soft reference, no dependency), its Color/Typography/
Components sections **override any source's default theme**: shadcn's
default palette, magic-mcp's generated styling, and antd's theme tokens are
starting points to be themed, not the final look. Without a DESIGN.md, the
source's defaults stand — but never invent a brand for a real client
(that is `design-dna`'s Extract job, not this router's).

## Rules

- **One system per project.** The stack decision (step 1) is made once and
  respected forever after; adding a second component system needs the
  user's explicit say-so — an explicit ask for a named source (step 0)
  counts, the router's own initiative never does, and the mix is announced
  either way.
- **Free before metered.** shadcn CLI/registry and antd are free; magic-mcp
  spends credits — reach it only when the free paths don't cover the need.
- **Route, don't build.** This skill picks the source; the actual add /
  generate / import runs through that source's own workflow. Hand-rolling a
  copy of a registry component is a routing failure.
- **Announce the pick.** State the chosen source and the step that chose it
  before adding anything.

## Out of scope

Installing or configuring the tools (each has its own docs), choosing a
framework, page/brand design (see `design-dna` for brand enforcement), and
non-component UI work (layout, routing, animation).
