"""Matplotlib preview of the generated board (documentation, not fabrication data)."""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Rectangle

from .board import BOARD_H, BOARD_W, KEEPOUTS, Board
from .pour import Pour
from .router import Track, Via

SILK = "#f2f2f2"
PAD = "#d9a441"
PAD_THT = "#b8860b"


def _poly_patch(pts, **kw):
    return Polygon(pts, closed=True, **kw)


def plot_board(board, tracks, vias, pours, path: str, title: str = "",
               show_refs: bool = True):
    fig, axes = plt.subplots(1, 2, figsize=(19, 8.2), facecolor="#1b1b1b")
    for ax, layer in zip(axes, ("top", "bottom")):
        ax.set_facecolor("#111111")
        board_rect = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
        ax.add_patch(_poly_patch(board_rect, facecolor="#0d3b26", edgecolor="#69d18b",
                                 lw=1.2, zorder=0))
        pour = pours.get(layer)
        if pour:
            for pp in pour.resolved():
                ax.add_patch(_poly_patch(list(pp.exterior.coords), facecolor="#1e5c3a",
                                         edgecolor="#3f8f60", lw=0.4, alpha=0.75, zorder=1))
                for ring in pp.interiors:
                    ax.add_patch(_poly_patch(list(ring.coords), facecolor="#0d3b26",
                                             edgecolor="#3f8f60", lw=0.3, zorder=2))
        for t in tracks:
            if t.layer != layer:
                continue
            col = "#ff6b52" if t.net in ("3V3", "VCC_SW") else "#f0c060"
            ax.plot([p[0] for p in t.pts], [p[1] for p in t.pts], color=col,
                    lw=t.width * 2.2, solid_capstyle="round", solid_joinstyle="round",
                    zorder=4, alpha=0.95)
        for fp in board.fps.values():
            for p in fp.pads:
                if p.kind == "smd" and ((p.layer == "top") != (layer == "top")):
                    continue
                if p.shape == "circle":
                    ax.add_patch(Circle((p.x, p.y), p.w / 2,
                                        facecolor=PAD_THT if p.is_tht else PAD,
                                        edgecolor="#5a3f10", lw=0.4, zorder=5))
                else:
                    w, h = p.shape_wh()
                    ax.add_patch(Rectangle((p.x - w / 2, p.y - h / 2), w, h,
                                           facecolor=PAD_THT if p.is_tht else PAD,
                                           edgecolor="#5a3f10", lw=0.4, zorder=5))
        for v in vias:
            ax.add_patch(Circle((v.x, v.y), v.dia / 2, facecolor="#9a9a9a",
                                edgecolor="#dddddd", lw=0.3, zorder=6))
            ax.add_patch(Circle((v.x, v.y), v.drill / 2, facecolor="#0a0a0a", zorder=7))
        for (x0, y0, x1, y1) in list(KEEPOUTS) + [k for fp in board.fps.values() for k in fp.keepouts]:
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor="none",
                                   edgecolor="#ff3b6b", lw=0.8, ls="--", zorder=8))
        for fp in board.fps.values():
            if (fp.msilk == "top") != (layer == "top"):
                continue
            for o in fp.silk:
                ax.plot([p[0] for p in o], [p[1] for p in o], color=SILK, lw=0.6,
                        zorder=9, alpha=0.85)
            if show_refs:
                ax.text(fp.ref_pos[0], fp.ref_pos[1], fp.ref, color="#7ee7ff",
                        fontsize=5.2, ha="center", va="center", zorder=10)
        for fp in board.fps.values():
            for (pos, dia) in fp.npth:
                ax.add_patch(Circle(pos, dia / 2, facecolor="#000000",
                                    edgecolor="#888888", lw=0.5, zorder=6))
        ax.set_xlim(-2, BOARD_W + 2)
        ax.set_ylim(BOARD_H + 2, -2)
        ax.set_aspect("equal")
        ax.set_title(f"{title}  -  {layer} copper", color="#dddddd", fontsize=10)
        ax.tick_params(colors="#666666", labelsize=6)
        for s in ax.spines.values():
            s.set_color("#444444")
    fig.tight_layout()
    fig.savefig(path, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path
