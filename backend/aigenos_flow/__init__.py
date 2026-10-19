"""aigenos-flow: the LLM fills fields; your program does everything else.

A skill is a SKILL.md whose ``steps:`` the engine runs. The model turns what
the user types into typed answers. Which step comes next, which tool runs,
with which inputs, and whether it may run at all is decided in code. The
FastAPI widget server (``aigenos_flow.main`` / ``aigenos_flow.agui`` /
``aigenos_flow.integrations``) ships under the ``[server]`` extra.

Quickstart::

    from aigenos_flow import create_agent

    agent = create_agent(llm, "skills/")      # a LangGraph agent that runs your flows

    # Or keep the LangGraph agent you already have and put the flows in front:
    from aigenos_flow import wrap_agent

    safe = wrap_agent(my_agent, skills_dir="skills/", llm=llm)

Public API (stable for v0.x):

    create_agent, wrap_agent   — a LangGraph agent whose workflows are flows
    flow_only                  — keep a governed tool out of the model's reach
    FlowAgent, FlowSession     — the flow runtime on its own; a scripted
                                 conversation for tests
    readonly, Secret           — mark a tool as a lookup; a masked input
    @flow, @action, Context    — a flow written in Python, for what steps cannot say
    FieldSpec                  — a typed answer
    Reply, SetField, Correct,  — what the model may return for one turn
        Cancel, StartFlow,
        Restart, Reopen, Resume,
        OutOfScope, Chitchat
    FlowState                  — the state that threads through every node
    Host                       — the protocol a framework adapter implements

Host adapters live at ``aigenos_flow.hosts`` (``LangGraphHost``, ``TestHost``).
"""
from .agent import FlowAgent, FlowSession
from .context import Context, FlowDone, FlowLink, NeedField, NeedFields
from .declarative import ActionSpec, AuditSink, ToolRegistry, build_declarative_flow
from .decorators import action, flow
from .fields import FieldSpec, FieldValidationError
from .host import Host
from .toolbox import Secret, readonly
from .integration import (
    build_react_with_skills,
    create_agent,
    flow_only,
    governed_write_tools,
    message_text,
    wrap_agent,
)
from .interpret import (
    Cancel,
    Chitchat,
    Correct,
    OutOfScope,
    Reopen,
    Reply,
    Restart,
    Resume,
    SetField,
    StartFlow,
    Turn,
    TurnInterpretation,
)
from .runtime import (
    FlowStackFrame,
    FlowState,
    Message,
    initial_state,
)
from .skills import Skill, SkillFrontmatter, SkillRegistry
# Importable for handlers.py files that notify a Slack channel; not part of the API.
from ._slack_helper import post_to_slack  # noqa: F401
from .connectors import (
    ConnectorAdapter,
    ConnectorManager,
    ConnectorResult,
    ConnectorStore,
    InvocationContext,
    ManagedOperation,
    ParameterSpec,
    SecretResolver,
    ToolDescriptor,
)

__all__ = [
    # building an agent
    "create_agent",
    "wrap_agent",
    "build_react_with_skills",
    "flow_only",
    "governed_write_tools",
    "message_text",
    # the flow runtime
    "FlowAgent",
    "FlowSession",
    "flow",
    "action",
    "Context",
    "Skill",
    "SkillFrontmatter",
    "SkillRegistry",
    "FieldSpec",
    "FieldValidationError",
    # declarative skills
    "ActionSpec",
    "ToolRegistry",
    "AuditSink",
    "build_declarative_flow",
    # managed connectors
    "ConnectorAdapter",
    "ConnectorManager",
    "ConnectorResult",
    "ConnectorStore",
    "InvocationContext",
    "ManagedOperation",
    "ParameterSpec",
    "SecretResolver",
    "ToolDescriptor",
    # the tool library
    "readonly",
    "Secret",
    # what the model may return for one turn
    "Turn",
    "TurnInterpretation",
    "SetField",
    "Correct",
    "StartFlow",
    "Cancel",
    "Restart",
    "Reopen",
    "Resume",
    "OutOfScope",
    "Chitchat",
    "Reply",
    # state
    "FlowState",
    "FlowStackFrame",
    "Message",
    "initial_state",
    # how a flow pauses and ends
    "FlowDone",
    "FlowLink",
    "NeedField",
    "NeedFields",
    # host protocol
    "Host",
]

__version__ = "0.3.1"
