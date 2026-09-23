"""Generate static/theme_light.css from static/app.css.

app.css was written dark-only with ~450 hard-coded colours, so a hand-written light
theme would drift the moment app.css changes. This script derives one instead:

- every declaration that contains a colour is re-emitted under
  :root[data-theme="light"] with the colour's lightness mirrored (dark surfaces
  become light, light text becomes dark; hue is kept, so money/paper/live colours
  keep their meaning and get darker for contrast on white);
- shadows stay dark but get lighter (a mirrored shadow would glow white);
- custom properties on :root are mirrored the same way.

Re-run after editing app.css:  .venv\\Scripts\\python tools\\gen_light_theme.py
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static" / "app.css"
OUT = ROOT / "static" / "theme_light.css"
LIGHT = ':root[data-theme="light"]'

COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3,4}\b"
    r"|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?\s*(?:,\s*[\d.]+%?\s*)?\)"
    r"|\b(?:white|black)\b"
)
SHADOW_PROPS = ("box-shadow", "text-shadow")


def _parse(c: str) -> tuple[float, float, float, float] | None:
    c = c.strip().lower()
    if c == "white":
        return 255, 255, 255, 1.0
    if c == "black":
        return 0, 0, 0, 1.0
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return r, g, b, a
    m = re.match(r"rgba?\((.*)\)", c)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",")]
    vals = []
    for p in parts[:3]:
        vals.append(float(p[:-1]) * 2.55 if p.endswith("%") else float(p))
    a = 1.0
    if len(parts) > 3:
        p = parts[3]
        a = float(p[:-1]) / 100 if p.endswith("%") else float(p)
    return vals[0], vals[1], vals[2], a


def _fmt(r: float, g: float, b: float, a: float) -> str:
    r, g, b = (max(0, min(255, round(x))) for x in (r, g, b))
    if a >= 0.999:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r}, {g}, {b}, {round(a, 3)})"


def mirror(c: str, *, text: bool = False) -> str:
    p = _parse(c)
    if p is None:
        return c
    r, g, b, a = p
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    # Mirror lightness, compressed a little so pure white text doesn't become pure
    # black and near-black panels land on soft off-white rather than glare.
    l2 = 0.975 - 0.94 * l
    # Saturated colours (money, accents, badges) carry meaning: keep them in a
    # readable mid-dark band on white instead of mirroring to near-black or pastel.
    if s > 0.35 and a > 0.5 and l2 < 0.7:
        l2 = min(0.46, max(0.3, l2))
    if text and a > 0.5:
        # Text on a light page must stay dark enough to read (~4.5:1 on white).
        l2 = min(l2, 0.4 if s > 0.25 else 0.42)
    s2 = s * (0.85 if l2 > 0.8 else 1.0)
    r2, g2, b2 = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l2)), max(0.0, min(1.0, s2)))
    return _fmt(r2 * 255, g2 * 255, b2 * 255, a)


def soften_shadow(c: str) -> str:
    p = _parse(c)
    if p is None:
        return c
    r, g, b, a = p
    _, l, _ = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if l < 0.2:  # dark shadow: keep dark, much lighter on a white page
        return _fmt(16, 24, 40, a * 0.35)
    return mirror(c)  # coloured glow: same treatment as other colours


def transform_value(prop: str, value: str) -> str:
    p = prop.strip().lower()
    if p in SHADOW_PROPS:
        return COLOR_RE.sub(lambda m: soften_shadow(m.group(0)), value)
    is_text = p in ("color", "-webkit-text-fill-color", "caret-color")
    return COLOR_RE.sub(lambda m: mirror(m.group(0), text=is_text), value)


COLORISH = re.compile(r"^(color|background(-color|-image)?|border(-[a-z]+)*|outline(-color)?|box-shadow|fill|stroke)$")
LAYER_MARKER = "Review redesign layer"


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def split_blocks(css: str):
    """Yield (prelude, body) for top-level blocks; body is raw text inside braces."""
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j == -1:
            return
        prelude = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        yield prelude, css[j + 1 : k - 1]
        i = k


def prefix_selector(sel: str) -> str:
    sel = sel.strip()
    if not sel:
        return sel
    if sel.startswith(":root"):
        return LIGHT + sel[len(":root"):]
    if re.match(r"html\b", sel):
        return 'html[data-theme="light"]' + sel[4:]
    return f"{LIGHT} {sel}"


def split_decls(body: str) -> list[tuple[str, str]]:
    out, buf, depth = [], "", 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == ";" and depth == 0:
            out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf)
    decls = []
    for d in out:
        if ":" not in d:
            continue
        prop, _, val = d.partition(":")
        decls.append((prop.strip(), val.strip()))
    return decls


def convert_rules(css: str, *, boost_tokens: bool = False) -> list[str]:
    """boost_tokens: also re-emit colour declarations that use var(--token) so the
    token-based redesign rules keep beating older rules once both are prefixed."""
    out: list[str] = []
    for prelude, body in split_blocks(css):
        if prelude.startswith("@keyframes") or prelude.startswith("@-webkit-keyframes") or prelude.startswith("@font-face"):
            continue
        if prelude.startswith("@media") or prelude.startswith("@supports"):
            inner = convert_rules(body, boost_tokens=boost_tokens)
            if inner:
                out.append(prelude + " {\n" + "\n".join("  " + r for r in inner) + "\n}")
            continue
        if prelude.startswith("@"):
            continue
        decls = [
            (p, transform_value(p, v))
            for p, v in split_decls(body)
            if (
                COLOR_RE.search(v)
                or (boost_tokens and COLORISH.match(p.lower()) and "var(--" in v)
            )
        ]
        if not decls:
            continue
        sels = ", ".join(prefix_selector(s) for s in prelude.split(","))
        body_out = " ".join(f"{p}: {v};" for p, v in decls)
        out.append(f"{sels} {{ {body_out} }}")
    return out


# Hand-tuned tokens that carry meaning (checked for >= 4.5:1 contrast on white).
MANUAL = """
/* ---- hand-tuned (generator output above is automatic) ---- */
:root[data-theme="light"] {
  --bg: #f4f6f9; --panel: #ffffff; --panel2: #eef2f7; --border: #d3dbe6;
  --text: #15202c; --muted: #566579; --text-3: #5d6b7e; --accent: #1f6f93;
  --gain: #1b7a4b; --gain-bg: #e6f4ec;
  --loss: #b3363a; --loss-bg: #fbeaea;
  --paper: #6b4fa3; --paper-bg: #f1ecfb; --paper-line: #c9b8ec;
  --live: #c2410c; --live-bg: #fff0e6;
  --warn: #8a5a00; --warn-bg: #fdf4e1;
  --green: var(--gain); --red: var(--loss);
  --focus-ring: 0 0 0 3px rgba(31, 111, 147, 0.4);
}
:root[data-theme="light"] body,
:root[data-theme="light"] body.ui-simple { background-color: var(--bg); background-image: none; }
:root[data-theme="light"] body .chrome-badge.paper.live-endpoint,
:root[data-theme="light"] body .chrome-badge.broker.live,
:root[data-theme="light"] .btn.danger,
:root[data-theme="light"] #btn-approve-confirm.danger { color: #ffffff; background: var(--live); border-color: var(--live); }
:root[data-theme="light"] .modal-card, :root[data-theme="light"] .sheet { background: #ffffff; }
:root[data-theme="light"] .toast { background: #15202c; color: #f4f6f9; }
"""


def main() -> None:
    raw = SRC.read_text(encoding="utf-8")
    cut = raw.find(LAYER_MARKER)
    if cut != -1:
        cut = raw.rfind("/*", 0, cut)
    base, layer = (raw[:cut], raw[cut:]) if cut != -1 else (raw, "")
    rules = convert_rules(strip_comments(base)) + convert_rules(strip_comments(layer), boost_tokens=True)
    header = (
        "/* GENERATED by tools/gen_light_theme.py from app.css — do not edit by hand.\n"
        "   Re-run the script after changing app.css. */\n"
        f'{LIGHT} {{ color-scheme: light; }}\n'
    )
    OUT.write_text(header + "\n".join(rules) + "\n" + MANUAL, encoding="utf-8")
    print(f"wrote {OUT.name}: {len(rules)} rules")


if __name__ == "__main__":
    main()
