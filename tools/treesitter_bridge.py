import sys
from pathlib import Path

import tree_sitter_language_pack as pack
from tree_sitter import Query, QueryCursor

FUNCTION_NODES = {
    "function_definition", "function_declaration", "function_item",
    "function_signature_item", "method_definition", "method_declaration",
    "constructor_declaration", "function", "method", "FnProto",
}
CONTAINER_NODES = {
    "class_definition", "class_declaration", "class_specifier", "class",
    "struct_item", "struct_declaration", "struct_specifier", "struct_definition", "struct",
    "enum_item", "enum_declaration", "enum_specifier", "enum_definition", "enum",
    "union_item", "union_declaration", "union_specifier", "union_definition", "union",
}


def named_child(node, *fields):
    for field in fields:
        child = node.child_by_field_name(field)
        if child is not None:
            return child
    return None


def name_node(node):
    name = (
        node.child_by_field_name("name")
        or node.child_by_field_name("identifier")
        or node.child_by_field_name("function")
    )
    if name is not None:
        return name
    declarator = node.child_by_field_name("declarator")
    stack = [declarator] if declarator is not None else list(reversed(node.named_children))
    while stack:
        current = stack.pop()
        if current.type.lower() in ("identifier", "field_identifier", "type_identifier", "property_identifier", "word"):
            return current
        stack.extend(reversed(current.named_children))
    return None


def function_name(node, source):
    name = name_node(node)
    if name is None:
        return ""
    return source[name.start_byte:name.end_byte].decode("utf-8", errors="replace")


def is_function_node(node):
    if node.type in FUNCTION_NODES:
        return True
    kind = node.type.lower()
    return any(word in kind for word in ("function", "method", "procedure", "subroutine")) and not any(
        word in kind for word in ("call", "type", "signature")
    )


def is_container_node(node):
    if node.type in CONTAINER_NODES:
        return True
    kind = node.type.lower()
    return any(word in kind for word in ("class", "struct", "enum", "union", "trait", "interface")) and not any(
        word in kind for word in ("field", "variant", "member", "enumerator", "list", "type")
    )


def emit_node(node, depth, field, source, owner, container):
    name = name_node(node)
    body = named_child(node, "body")
    callee = named_child(node, "function", "callee", "method", "name")
    point = node.start_point
    next_owner = owner
    if is_function_node(node):
        next_owner = function_name(node, source)
    next_container = container
    if is_container_node(node):
        next_container = function_name(node, source)
    fields = [
        "N", str(depth), node.type, str(node.start_byte), str(node.end_byte),
        str(point.row + 1), str(point.column + 1), field or "",
        str(name.start_byte) if name else "-1",
        str(name.end_byte) if name else "-1",
        str(body.start_byte) if body else "-1",
        str(body.end_byte) if body else "-1",
        str(callee.start_byte) if callee else "-1",
        str(callee.end_byte) if callee else "-1",
        "1" if node.is_error else "0",
        "1" if node.is_missing else "0",
        next_owner,
        "1" if node.is_named else "0",
        next_container,
    ]
    sys.stdout.write("\t".join(fields) + "\n")
    for index, child in enumerate(node.children):
        if child.is_named or child.is_error or child.is_missing:
            emit_node(child, depth + 1, node.field_name_for_child(index) or "", source, next_owner, next_container)


def tag_records(language, root):
    text = pack.get_tags_query(language)
    if not text:
        return [], False
    try:
        query = Query(pack.get_language(language), text)
        cursor = QueryCursor(query)
        records = []
        for _, captures in cursor.matches(root):
            names = captures.get("name", [])
            for tag, nodes in captures.items():
                if not tag.startswith(("definition.", "reference.")):
                    continue
                for index, node in enumerate(nodes):
                    name = names[min(index, len(names) - 1)] if names else None
                    body = node.child_by_field_name("body")
                    point = node.start_point
                    records.append((
                        tag, node, name, body, point.row + 1, point.column + 1,
                    ))
        method_spans = {
            (node.start_byte, node.end_byte)
            for tag, node, *_ in records
            if tag == "definition.method"
        }
        unique = []
        seen = set()
        for record in records:
            tag, node = record[0], record[1]
            span = (node.start_byte, node.end_byte)
            if tag == "definition.function" and span in method_spans:
                continue
            key = (tag, span)
            if key not in seen:
                seen.add(key)
                unique.append(record)
        return unique, True
    except Exception:
        return [], False


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        for name in pack.manifest_languages():
            print(name)
        return 0
    if len(sys.argv) not in (2, 3):
        print("usage: treesitter_bridge.py FILE [LANGUAGE]", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    language = sys.argv[2] if len(sys.argv) == 3 and sys.argv[2] else pack.detect_language_from_path(str(path))
    if not language or not pack.has_language(language):
        print(f"unsupported or undetected Tree-sitter language: {language or path.suffix}", file=sys.stderr)
        return 3
    pack.init()
    if language not in pack.downloaded_languages():
        result = pack.download([language])
        if result != 1:
            print(f"could not load Tree-sitter grammar: {language}", file=sys.stderr)
            return 4
    source = path.read_bytes()
    parser = pack.get_parser(language)
    tree = parser.parse(source)
    records, has_tags = tag_records(language, tree.root_node)
    print("M\t" + language + "\t" + ("1" if has_tags else "0"))
    emit_node(tree.root_node, 0, "", source, "", "")
    for tag, node, name, body, line, column in records:
        name_start = name.start_byte if name is not None else node.start_byte
        name_end = name.end_byte if name is not None else node.end_byte
        body_start = body.start_byte if body is not None else -1
        print("\t".join((
            "T", tag, str(node.start_byte), str(node.end_byte), str(line), str(column),
            str(name_start), str(name_end), str(body_start), node.type,
        )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
