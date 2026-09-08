"""Version selection for shared durable execution; no wire conversion or fallback."""
from .contracts_v5 import parse_v5_command, parse_v5_event
from .contracts_v6 import parse_v6_command, parse_v6_event


def parse_core_command(value):
    if isinstance(value,dict) and value.get('contractVersion')=='core_chat_collaboration_v6':
        return parse_v6_command(value)
    return parse_v5_command(value)


def parse_core_event(value):
    if isinstance(value,dict) and value.get('contractVersion')=='core_chat_collaboration_v6':
        return parse_v6_event(value)
    return parse_v5_event(value)
