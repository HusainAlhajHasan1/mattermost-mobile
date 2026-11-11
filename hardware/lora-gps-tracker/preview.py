"""Quick placement preview (no routing)."""
from pcbgen.board import Board
from pcbgen.plot import plot_board

b = Board()
plot_board(b, [], [], {}, "out/placement.png", title="Placement")
print("wrote out/placement.png")
