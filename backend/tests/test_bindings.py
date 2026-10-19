from aigenos_flow.engine.bindings import resolve


def test_resolve_string_substitution():
    assert resolve("Hello {{name}}", {"name": "world"}) == "Hello world"


def test_resolve_dotted_path():
    state = {"order": {"id": "A1", "total": 12}}
    assert resolve("Order {{order.id}} = ${{order.total}}", state) == "Order A1 = $12"


def test_resolve_missing_var_renders_empty():
    assert resolve("Hi {{missing}}", {}) == "Hi "


def test_resolve_full_binding_returns_object():
    items = [{"x": 1}]
    assert resolve("{{items}}", {"items": items}) is items


def test_resolve_dict_recursive():
    state = {"name": "Jane"}
    out = resolve({"greeting": "hi {{name}}", "nested": {"v": "{{name}}!"}}, state)
    assert out == {"greeting": "hi Jane", "nested": {"v": "Jane!"}}


def test_resolve_list_recursive():
    assert resolve(["a", "{{n}}", "c"], {"n": "b"}) == ["a", "b", "c"]


def test_resolve_passthrough_non_string():
    assert resolve(42, {}) == 42
    assert resolve(True, {}) is True
    assert resolve(None, {}) is None
