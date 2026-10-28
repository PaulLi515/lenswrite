from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.models import StoryState
from app.agents.ensure_setup import ensure_setup_node
from app.agents.prompt_expander import prompt_expander_node
from app.agents.plot_weaver import plot_weaver_node, lock_core_node
from app.agents.literary_renderer import literary_renderer_node
from app.agents.story_evaluator import story_evaluator_node


def should_expand_prompt(state: StoryState) -> str:
    """Default ON. Set expand_prompt=False to skip AI outline expansion."""
    flag = state.get("expand_prompt")
    if flag is False:
        print("    [prompt_expand] skipped (expand_prompt=False)")
        return "skip"
    return "expand"


def should_continue_or_end(state: StoryState) -> str:
    iteration_count = state.get("iteration_count") or 0
    max_iterations = state.get("max_iterations") or 6
    revision_notes = state.get("revision_notes") or []

    if not revision_notes or iteration_count >= max_iterations:
        print(
            f"    [循环判断] 停止 | iter={iteration_count}/{max_iterations} "
            f"notes={len(revision_notes)}"
        )
        return "end"

    print(
        f"    [循环判断] 仅重渲染 | 第 {iteration_count} 轮 → 第 {iteration_count + 1} 轮"
    )
    return "revise"


def build_lenswrite_graph():
    """Internal quality pipeline (no mid-page user card edit).

    ensure_setup → prompt_expand → plot_weaver → lock_core
    → literary_renderer (segmented scenes + length expand/trim)
    ⇄ story_evaluator → END
    """
    workflow = StateGraph(StoryState)

    workflow.add_node("ensure_setup", ensure_setup_node)
    workflow.add_node("prompt_expand", prompt_expander_node)
    workflow.add_node("plot_weaver", plot_weaver_node)
    workflow.add_node("lock_core", lock_core_node)
    workflow.add_node("literary_renderer", literary_renderer_node)
    workflow.add_node("story_evaluator", story_evaluator_node)

    workflow.set_entry_point("ensure_setup")
    workflow.add_conditional_edges(
        "ensure_setup",
        should_expand_prompt,
        {
            "expand": "prompt_expand",
            "skip": "plot_weaver",
        },
    )
    workflow.add_edge("prompt_expand", "plot_weaver")
    workflow.add_edge("plot_weaver", "lock_core")
    workflow.add_edge("lock_core", "literary_renderer")
    workflow.add_edge("literary_renderer", "story_evaluator")

    workflow.add_conditional_edges(
        "story_evaluator",
        should_continue_or_end,
        {
            "revise": "literary_renderer",
            "end": END,
        },
    )

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


lenswrite_graph = build_lenswrite_graph()
