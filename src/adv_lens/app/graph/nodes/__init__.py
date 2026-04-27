"""LangGraph node functions.

Each node takes the current ``ADVState`` and returns a partial update dict.
LangGraph merges the partials into the state; we keep nodes pure (no
side-effecting state mutation).
"""

from adv_lens.app.graph.nodes.extract_conflicts import extract_conflicts_node
from adv_lens.app.graph.nodes.extract_disciplinary import extract_disciplinary_node
from adv_lens.app.graph.nodes.extract_fee import extract_fee_node
from adv_lens.app.graph.nodes.fetch import fetch_brochure_node
from adv_lens.app.graph.nodes.hitl_gate import hitl_gate_node
from adv_lens.app.graph.nodes.retrieve_peers import retrieve_peers_node
from adv_lens.app.graph.nodes.segment import segment_brochure_node
from adv_lens.app.graph.nodes.write_redline import write_redline_node

__all__ = [
    "extract_conflicts_node",
    "extract_disciplinary_node",
    "extract_fee_node",
    "fetch_brochure_node",
    "hitl_gate_node",
    "retrieve_peers_node",
    "segment_brochure_node",
    "write_redline_node",
]
