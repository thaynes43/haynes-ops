"""Server-owned output schemas for local-model inference."""
import json


def object_schema(**properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def string(limit):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def array(items, limit):
    return {"type": "array", "items": items, "minItems": 1, "maxItems": limit}


FRAME = object_schema(
    face={"enum": ["visible", "hidden", "uncertain"]},
    code={"anyOf": [string(64), {"type": "null"}]},
    wardrobe={"enum": ["match", "mismatch", "uncertain"]},
)
DIRECTION = string(512)
PLAN = object_schema(scene_id=string(64), shots=array(object_schema(technical_direction=DIRECTION), 12))
PLAN_TOOL = {"name": "record_plan_v1", "description": "Record one proposed scene plan",
             "parameters": object_schema(scene_id=string(64), technical_direction=DIRECTION,
                                         shot_count={"type": "integer", "minimum": 1, "maximum": 12})}
MODES = {
    "text": {},
    "frame_check_v1": {"schema": object_schema(frames=array(FRAME, 4))},
    "plan_v1": {"schema": PLAN},
    "plan_tool_v1": {"tool": PLAN_TOOL},
}


def matches(schema, value):
    """Validate the subset of JSON Schema used by the definitions above."""
    if "enum" in schema:
        return value in schema["enum"]
    if "anyOf" in schema:
        return any(matches(option, value) for option in schema["anyOf"])
    match schema["type"]:
        case "object":
            fields = schema["properties"]
            return (type(value) is dict and value.keys() == fields.keys()
                    and all(matches(field, value[key]) for key, field in fields.items()))
        case "array":
            return (type(value) is list and schema["minItems"] <= len(value) <= schema["maxItems"]
                    and all(matches(schema["items"], entry) for entry in value))
        case "string":
            return (type(value) is str and schema["minLength"] <= len(value) <= schema["maxLength"]
                    and bool(value.strip()) and all(not 0xD800 <= ord(char) <= 0xDFFF for char in value))
        case "integer":
            return type(value) is int and schema["minimum"] <= value <= schema["maximum"]
        case "null":
            return value is None
    raise ValueError("Unsupported server schema")


def unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate JSON fields")
    return value


def valid_text(mode, text, image_count):
    if mode == "text":
        return True
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except (ValueError, RecursionError):
        return False
    schema = MODES[mode]
    if "tool" in schema:
        tool = schema["tool"]
        return (type(value) is dict and value.keys() == {"name", "arguments"}
                and value["name"] == tool["name"] and matches(tool["parameters"], value["arguments"]))
    return matches(schema["schema"], value) and (mode != "frame_check_v1" or len(value["frames"]) == image_count)
