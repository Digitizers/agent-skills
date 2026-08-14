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
| **shadcn/ui CLI** (`npx shadcn@latest add <name>`) | copy-in registry, code lands in the repo | local | free | standard primitives in React/Tailwind — button, dialog, table, form, dropdown… |
| **shadcn-ui MCP** | MCP serving the same registry (source + demos + metadata) | MCP | free | inspecting a component's source/demo before adding; block/pattern lookup |
| **magic-mcp** (21st.dev) | MCP generating full sections from the community library | MCP | metered | complete sections/patterns — pricing, hero, landing blocks — or "find me a component like X" community search |
| **Ant Design** (`antd`) | full component library, imported as a dependency | local | free | projects already on antd / enterprise-style data-heavy UIs that chose it |

## Decision order

Stop at the first match:

0. **Does the project already have a component system?** Existing
   `components/ui/` with shadcn conventions → shadcn. `antd` in
   `package.json` → Ant Design. Another system (MUI, Chakra, Mantine,
   project-internal design system) → **use that system**; this router adds
   nothing and must not introduce a second one. The existing stack always
   wins — a "better" component from another system is never a reason to mix.
1. **Standard primitive in a React/Tailwind project** (button, dialog,
   table, form, tabs, dropdown, toast…)? → **shadcn**. Use the shadcn-ui
   MCP to inspect source/demos when it answers; the add itself goes through
   `npx shadcn@latest add <name>` — the free path that works in any session.
2. **Full section or pattern** (pricing section, hero, navbar, dashboard
   shell, landing block) **or a community search** ("something like X")?
   → **magic-mcp** — after the free rungs don't cover it: a standard
   primitive is never a magic-mcp job, and a section shadcn's registry
   already ships goes through shadcn first. magic-mcp is metered — spend
   only for what the free paths can't produce.
3. **Project on Ant Design** (or an explicit antd ask in a project with no
   conflicting system)? → **antd** components via normal imports.
4. **Nothing fits** (non-React stack with no system, unique bespoke need)?
   → **declared custom build**: say explicitly that no source covers this,
   then build following the project's own conventions. A custom build is a
   stated decision, never a silent default.

## Availability rule (inherited from browser-router)

No tool named here is assumed present. **Confirm the chosen tool actually
answers in this session before routing to it** — an MCP can be unloaded, a
key revoked, credits exhausted. A tool that doesn't answer is not a route.
Fall back by layer:

- **shadcn-ui MCP missing** → the CLI path (`npx shadcn@latest add`) is the
  same registry and needs only npx; for source inspection, the registry is
  public — fetch the component page/source directly.
- **magic-mcp missing or out of credits** → compose the section from shadcn
  primitives (free), or declare a custom build (step 4). Never substitute a
  different metered service without saying so.
- **npx/network unavailable** → say so and deliver the component code as a
  patch the user can apply, following the registry's source.

## Design constraints (conditional design-dna link)

If a client **DESIGN.md** exists for the project (see the `design-dna`
skill, when loaded — soft reference, no dependency), its Color/Typography/
Components sections **override any source's default theme**: shadcn's
default palette, magic-mcp's generated styling, and antd's theme tokens are
starting points to be themed, not the final look. Without a DESIGN.md, the
source's defaults stand — but never invent a brand for a real client
(that is `design-dna`'s Extract job, not this router's).

## Rules

- **One system per project.** The stack decision (step 0) is made once and
  respected forever after; adding a second component system needs the
  user's explicit say-so, never the router's initiative.
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
