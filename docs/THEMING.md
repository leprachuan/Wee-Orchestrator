# Wee Orchestrator — Theming Guide

Wee Orchestrator supports a CSS theming system that lets you customize the
look and feel of the WebUI. Choose from built-in themes or create your own.

---

## Built-in Themes

| Theme | Description | `data-theme` value |
|-------|-------------|-------------------|
| **Emerald** | Default glassmorphism (dark green + gold) | _(none / default)_ |
| **Midnight** | Deep blue ocean-inspired | `midnight` |
| **Sunrise** | Warm light mode | `sunrise` |
| **Cyberpunk** | Neon pink & cyan | `cyberpunk` |

Switch themes via the 🎨 Theme button in the sidebar toolbar.

---

## Creating a Custom Theme

### Step 1 — Copy the template

```bash
cp webui/themes/custom.css.template webui/themes/my-cool-theme.css
```

The filename becomes the theme name (without `.css`).

### Step 2 — Edit CSS variables

Open your new file and replace `my-theme` with your filename (minus `.css`):

```css
[data-theme="my-cool-theme"] {
  --accent: #6366f1;
  /* ... */
}
```

### Step 3 — Restart the API service

```bash
systemctl restart agent-manager-api-dev.service
```

Your theme will appear in the theme picker automatically.

---

## CSS Variable Reference

These variables control the entire UI. Override any of them in your
`[data-theme="your-theme"]` block.

### Colors

| Variable | Purpose | Default (Emerald) |
|----------|---------|-------------------|
| `--accent` | Primary accent (buttons, links, focus) | `#3ecf8e` |
| `--accent-hover` | Accent hover state | `#34b87a` |
| `--accent-glow` | Accent glow effect | `rgba(62,207,142,0.30)` |
| `--accent-rgb` | Accent as RGB triplet (for rgba()) | `62, 207, 142` |
| `--gold` | Secondary accent (tags, badges) | `#f5c542` |
| `--gold-hover` | Gold hover state | `#e0b23a` |
| `--gold-glow` | Gold glow effect | `rgba(245,197,66,0.25)` |
| `--danger` | Error/danger color | `#ff5252` |
| `--danger-hover` | Danger hover state | `#e04848` |

### Backgrounds & Surfaces

| Variable | Purpose | Default (Emerald) |
|----------|---------|-------------------|
| `--bg-primary` | Main background | `#0a0e1a` |
| `--bg-secondary` | Secondary background | `#121828` |
| `--bg-tertiary` | Tertiary background | `#1a2236` |
| `--surface` | Card/panel surface | `rgba(18,24,40,0.70)` |
| `--surface-raised` | Elevated surface | `rgba(62,207,142,0.06)` |
| `--surface-sunken` | Depressed surface | `rgba(4,6,14,0.50)` |
| `--surface-overlay` | Modal/overlay surface | `rgba(10,14,26,0.85)` |

### Text

| Variable | Purpose | Default (Emerald) |
|----------|---------|-------------------|
| `--text-primary` | Primary text | `#e2e8f0` |
| `--text-secondary` | Secondary text | `#94a3b8` |
| `--text-muted` | Muted/disabled text | `#64748b` |
| `--text-inverse` | Text on light backgrounds | `#0f172a` |

### Glassmorphism

| Variable | Purpose | Default (Emerald) |
|----------|---------|-------------------|
| `--glass-bg` | Glass panel background | `rgba(18,24,40,0.55)` |
| `--glass-border` | Glass panel border | `rgba(62,207,142,0.12)` |
| `--glass-shadow` | Glass panel shadow | _(complex multi-layer)_ |
| `--glass-blur` | Backdrop filter | `blur(28px) saturate(180%)` |

### Borders & Dividers

| Variable | Purpose | Default (Emerald) |
|----------|---------|-------------------|
| `--border-subtle` | Subtle border | `rgba(62,207,142,0.10)` |
| `--border-default` | Default border | `rgba(62,207,142,0.18)` |
| `--border-strong` | Strong/focus border | `rgba(62,207,142,0.30)` |
| `--divider` | Section divider | `rgba(62,207,142,0.08)` |

### Scrollbar

| Variable | Purpose |
|----------|---------|
| `--scrollbar-track` | Scrollbar track background |
| `--scrollbar-thumb` | Scrollbar thumb color |
| `--scrollbar-hover` | Scrollbar thumb hover |

---

## Themes API

### List themes

```
GET /api/v1/themes
Authorization: Bearer <token>
```

Response:
```json
{
  "themes": [
    {"name": "emerald", "label": "Emerald", "description": "Default glassmorphism", "builtin": true},
    {"name": "midnight", "label": "Midnight", "description": "Deep blue ocean", "builtin": true},
    {"name": "leprachaun-glassmorphism", "label": "Leprachaun Glassmorphism", "description": "Custom theme", "builtin": false}
  ],
  "count": 5
}
```

### Get custom theme CSS

```
GET /api/v1/themes/{name}/css
Authorization: Bearer <token>
```

Returns the raw CSS content for a custom theme (from `webui/themes/{name}.css`).

Built-in themes are served via the static `themes.css` file and are not
available through this endpoint.

---

## Tips

- **Body background**: Override `html[data-theme="your-theme"] body` for
  custom radial gradients.
- **Light mode**: Set `--bg-primary` to a light color and override
  `::selection`, `.blob-*`, and code block backgrounds for a full light theme.
- **Test quickly**: Open browser DevTools, add `data-theme="your-theme"` to
  the `<html>` element, then tweak CSS variables in real time.
- **Highlight.js**: The system auto-switches to `github.min.css` for the
  `sunrise` theme. Custom light themes should add similar logic or override
  code block backgrounds.

---

## Security

Theme names are validated against `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$` and
path-traversal checked via `resolve()` to prevent directory escape. Only
`.css` files in the `webui/themes/` directory are served.
