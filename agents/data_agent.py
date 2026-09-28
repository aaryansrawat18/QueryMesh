import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.llm_pick import pick_llm
from Models.schema import RouterSchema, DataAgentSchema
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from agents.sql_analyst import sql_analyst
from api.policy import assert_can, route_target
from utils.runtime import enqueue_etl
from utils.trace import span


_router = None


def router_llm():
    """Cheap model. Built on first route so importing this module does not need API keys."""
    global _router
    if _router is None:
        _router = pick_llm("low").with_structured_output(RouterSchema)
    return _router


# ---------------------------- QUERYMESH GRAPH ---------------------------- #


def router_node(state:DataAgentSchema):

    message = state.messages[-1].content

    with span("router"):
        route_response_dict = router_llm().invoke(message).model_dump()

    route_response = route_response_dict['answer']

    state.route_response = route_response

    return state

def etl_node(state:DataAgentSchema):
    assert_can(state.role, "etl")
    with span("etl"):
        job_id = enqueue_etl(
            question=state.messages[-1].content,
            user_id="agent",
            tenant_id=state.tenant_id,
            role=state.role,
        )
    state.messages = state.messages + [AIMessage(content=f"Queued ETL job {job_id}")]
    return state

def sql_node(state:DataAgentSchema):
    assert_can(state.role, "sql")
    with span("sql"):
        return _sql_node(state)


def _sql_node(state:DataAgentSchema):

    message = state.messages[-1].content

    input_schema = {
        "messages": [],
        "user_question": f"{message}",
        "curated_ques": "",
        "prompt_query_context": "",
        "generated_sql_query": "",
        "is_safe": "No",
        "comments": "",
        "sql_query_execution_result": "",
        "final_answer": "",
        "tenant_id": state.tenant_id,
        "role": state.role,
    }

    response = sql_analyst.invoke(
        input_schema,
        config={"recursion_limit": int(os.environ.get("MAX_GRAPH_STEPS", "16"))},
    )

    state.messages = state.messages + [response]
    if isinstance(response, dict):
        state.needs_approval = bool(response.get("needs_approval"))

    return state




data_agent_graph = StateGraph(DataAgentSchema)

data_agent_graph.add_node("router_node", router_node)
data_agent_graph.add_node("etl_node", etl_node)
data_agent_graph.add_node("sql_node", sql_node)

data_agent_graph.add_edge(START, "router_node")

def route_edge(state: DataAgentSchema) -> str:
    return route_target(state.route_response, state.role)


data_agent_graph.add_conditional_edges("router_node", route_edge,
                                      {
                                          "sql_node": "sql_node",
                                          "etl_node": "etl_node"
                                      })

data_agent = data_agent_graph.compile()


if __name__ == "__main__":
    from IPython.display import Image
    img = Image(data_agent.get_graph().draw_mermaid_png())
    with open("data_agent_graph.png", "wb") as f:
        f.write(img.data)

    response = data_agent.invoke(
        {"messages":[HumanMessage(content="I want to extract the data from the API endpoint 'https://pokeapi.co/api/v2/pokemon' and save it to data/extract folder in the csv folder")],
         "route_response": "",
         "tenant_id": "local",
         "role": "admin"}
    )

    print(response)




