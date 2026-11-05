"""Tables whose columns fit the pane: fixed columns keep their width, one column takes the rest, and a
cell longer than its column ends with an ellipsis instead of widening the table."""
from typing import Iterable, List, Optional, Sequence, Tuple

from rich.text import Text
from textual.widgets import DataTable

# (label, key, width); width None is the column that takes the remaining space.
ColumnSpec = Tuple[str, str, Optional[int]]
# Room kept for a vertical scrollbar, so that one appearing does not add a horizontal one.
SCROLLBAR = 2
MIN_FLEX = 8


def flex_width(total: int, columns: Sequence[ColumnSpec], padding: int) -> int:
    fixed = sum(w for _, _, w in columns if w is not None)
    return max(total - SCROLLBAR - fixed - 2 * padding * len(columns), MIN_FLEX)


def cell(value: str, width: int) -> Text:
    text = Text(value, no_wrap=True)
    text.truncate(width, overflow="ellipsis")
    return text


def fill(table: DataTable, columns: Sequence[ColumnSpec], rows: Iterable[Tuple[Optional[str], List[str]]]) -> None:
    """Rebuild `table` with `columns` sized to its current width and `rows` of (key, cells)."""
    flex = flex_width(table.size.width, columns, table.cell_padding)
    widths = [flex if w is None else w for _, _, w in columns]
    table.clear(columns=True)
    for (label, key, _), width in zip(columns, widths):
        table.add_column(label, width=width, key=key)
    for key, cells in rows:
        table.add_row(*(cell(c, w) for c, w in zip(cells, widths)), key=key)
