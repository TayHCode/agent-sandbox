import base64
import json
import re


def shell_argv(command):
    command = command.strip()
    if "\n" in command or "\t" in command:
        encoded = base64.b64encode(command.encode()).decode()
        command = f'eval "$(printf %s {encoded} | base64 -d)"'
    return ["/bin/bash", "-lc", command]


def parse_action(value):
    """Turn whatever a model returned into an argv list, or None when it is finished."""
    if isinstance(value, str):
        text = value.strip()
        try:
            return parse_action(json.loads(text))
        except (ValueError, TypeError):
            pass
        blocks = re.findall(r"```(?:[\w+-]*)\s*\n(.*?)```", text, re.S)
        for block in blocks:
            try:
                return parse_action(json.loads(block))
            except (ValueError, TypeError):
                pass
        decoder = json.JSONDecoder()
        for match in re.finditer(r"[\{\[]", text):
            try:
                parsed, _ = decoder.raw_decode(text[match.start():])
                return parse_action(parsed)
            except (ValueError, TypeError):
                pass
        if blocks:
            return shell_argv("\n".join(blocks))
        if not text or re.match(r"(?i)^(done|finish|finished|complete|completed)\b", text):
            return None
        if re.match(r"(?i)^(?:I|We|The|This|That|Here|It|All|No|Unable|Blocked|Successfully|Success)\b", text):
            return None
        return shell_argv(text.removeprefix("$ "))
    if isinstance(value, list):
        if all(isinstance(v, str) for v in value):
            return value or None
        return parse_action(value[0]) if value else None
    if not isinstance(value, dict):
        raise ValueError("no executable model action")
    for key in ("tool_calls", "function_call", "tool_call", "function", "arguments", "parameters", "args", "tool_input", "input", "action"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)) or (key in ("arguments", "parameters", "args", "input") and isinstance(nested, str)):
            return parse_action(nested)
    if value.get("done") is True or value.get("kind", value.get("type")) in ("finish", "done", "final", "complete"):
        return None
    if isinstance(value.get("argv"), list):
        return value["argv"] or None
    for key in ("command", "proposed_command", "cmd", "shell", "script", "bash", "code"):
        command = value.get(key)
        if isinstance(command, list):
            return command or None
        if isinstance(command, str) and command.strip():
            return shell_argv(command)
    for key in ("content", "text", "result"):
        if isinstance(value.get(key), (str, dict, list)):
            return parse_action(value[key])
    raise ValueError("no command or finish in model action")


RETURN_WITH_MAIN_PROCESS = (
    'o=$(mktemp); e=$(mktemp); "$@" </dev/null >"$o" 2>"$e" & wait $!; c=$?; '
    'head -c 1048576 "$o"; head -c 1048576 "$e" >&2; rm -f "$o" "$e"; exit $c'
)


def returning_argv(argv):
    return ["/bin/bash", "-c", RETURN_WITH_MAIN_PROCESS, "_", *argv]
