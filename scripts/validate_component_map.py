from __future__ import annotations

import ast
import fnmatch
import importlib
from collections import defaultdict
from importlib.util import resolve_name
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "docs" / "components" / "component-map.yml"

REQUIRED_COMPONENT_FIELDS = {
    "id",
    "parent",
    "purpose",
    "owns",
    "does_not_own",
    "inputs",
    "outputs",
    "public_contracts",
    "public_exports",
    "dependencies",
    "state_recovery",
    "current_code",
    "target_code",
    "current_tests",
    "target_tests",
    "authoritative_docs",
    "adrs",
    "gaps",
}
REQUIRED_TOP_LEVEL_FIELDS = {
    "version",
    "status",
    "authority",
    "target_layout",
    "inventory",
    "layers",
    "target_documentation",
    "architecture_lint",
    "dependency_exceptions",
    "components",
    "structural_status",
}
NON_EMPTY_COMPONENT_TEXT_FIELDS = {"purpose", "state_recovery"}
NON_EMPTY_COMPONENT_LIST_FIELDS = {"owns", "does_not_own", "inputs", "outputs"}
COMPONENT_LIST_FIELDS = {
    "owns",
    "does_not_own",
    "inputs",
    "outputs",
    "public_contracts",
    "dependencies",
    "current_code",
    "target_code",
    "current_tests",
    "target_tests",
    "authoritative_docs",
    "adrs",
    "gaps",
}


class ComponentMapError(ValueError):
    pass


def _reject_duplicate_mapping_keys(node: Node, path: str = "$") -> None:
    if isinstance(node, MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                raise ComponentMapError(f"{path}: component map keys must be scalar")
            key = key_node.value
            if key in seen:
                raise ComponentMapError(f"{path}: duplicate YAML key {key!r}")
            seen.add(key)
            _reject_duplicate_mapping_keys(value_node, f"{path}.{key}")
    elif isinstance(node, SequenceNode):
        for index, value_node in enumerate(node.value):
            _reject_duplicate_mapping_keys(value_node, f"{path}[{index}]")


def _load_component_map_text(text: str) -> dict[str, Any]:
    root = yaml.compose(text)
    if root is None:
        raise ComponentMapError("component map must not be empty")
    _reject_duplicate_mapping_keys(root)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ComponentMapError("component map must be a mapping")
    return data


def load_component_map() -> dict[str, Any]:
    return _load_component_map_text(MAP_PATH.read_text(encoding="utf-8"))


def _source_paths(patterns: list[str]) -> set[str]:
    paths: set[str] = set()
    for pattern in patterns:
        matches = [path for path in ROOT.glob(pattern) if path.is_file()]
        if not matches:
            raise ComponentMapError(f"inventory source pattern has no files: {pattern}")
        paths.update(path.relative_to(ROOT).as_posix() for path in matches)
    return paths


def _test_paths() -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
        if path.is_file()
    }


def _matches(path: str, pattern: str) -> bool:
    return path == pattern or fnmatch.fnmatch(path, pattern)


def _existing_matches(pattern: str) -> list[Path]:
    if any(character in pattern for character in "*?["):
        return [path for path in ROOT.glob(pattern) if path.is_file()]
    path = ROOT / pattern
    return [path] if path.is_file() else []


def _validate_public_export(reference: str) -> None:
    module_name, separator, symbol = reference.partition(":")
    if not separator or not module_name or not symbol:
        raise ComponentMapError(f"invalid public export reference: {reference}")
    source_path = _source_path_for_module(module_name)
    if source_path is not None:
        names = _dunder_all_names(source_path)
        if names is not None and symbol in names:
            return
    module = importlib.import_module(module_name)
    if not hasattr(module, symbol):
        raise ComponentMapError(f"missing public export: {reference}")


def _source_path_for_module(module_name: str) -> Path | None:
    relative = Path(*module_name.split("."))
    module_path = ROOT / "src" / relative.with_suffix(".py")
    if module_path.is_file():
        return module_path
    package_path = ROOT / "src" / relative / "__init__.py"
    return package_path if package_path.is_file() else None


def _module_name_for_source_path(path: str) -> str | None:
    prefix = "src/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    parts = list(Path(path[len(prefix) :]).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _dunder_all_names(path: str | Path) -> tuple[str, ...] | None:
    absolute_path = path if isinstance(path, Path) else ROOT / path
    tree = ast.parse(
        absolute_path.read_text(encoding="utf-8"),
        filename=str(path),
    )
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            if value is None:
                raise ComponentMapError(f"{path}: __all__ must be a literal string list/tuple")
            try:
                names = ast.literal_eval(value)
            except (TypeError, ValueError) as exc:
                raise ComponentMapError(
                    f"{path}: __all__ must be a literal string list/tuple"
                ) from exc
            if not isinstance(names, (list, tuple)) or any(
                not isinstance(name, str) or not name for name in names
            ):
                raise ComponentMapError(f"{path}: __all__ must be a literal string list/tuple")
            return tuple(names)
    return None


def _public_facade_exports(source_paths: set[str]) -> set[str]:
    exports: set[str] = set()
    for path in sorted(source_paths):
        module_name = _module_name_for_source_path(path)
        if module_name is None:
            continue
        names = _dunder_all_names(path)
        if names is None:
            continue
        exports.update(f"{module_name}:{name}" for name in names)
    return exports


def _internal_module_paths(
    source_paths: set[str], architecture_lint: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    pattern = architecture_lint["source_pattern"]
    lint_paths = _source_paths([pattern])
    module_paths: dict[str, str] = {}
    path_modules: dict[str, str] = {}
    for path in sorted(source_paths):
        if path not in lint_paths:
            continue
        module_name = _module_name_for_source_path(path)
        if module_name is None:
            raise ComponentMapError(f"architecture lint source is not Python: {path}")
        module_paths[module_name] = path
        path_modules[path] = module_name
    if not module_paths:
        raise ComponentMapError("architecture lint source pattern matched no modules")
    return module_paths, path_modules


def _resolve_internal_module(
    module_name: str,
    *,
    internal_package: str,
    module_paths: dict[str, str],
) -> str | None:
    if module_name != internal_package and not module_name.startswith(f"{internal_package}."):
        return None
    if module_name in module_paths:
        return module_name
    raise ComponentMapError(f"unknown internal import target: {module_name}")


def _component_edge_is_allowed(
    source_owners: list[str],
    target_owners: list[str],
    components: dict[str, Any],
) -> bool:
    return any(
        source_owner == target_owner or target_owner in components[source_owner]["dependencies"]
        for source_owner in source_owners
        for target_owner in target_owners
    )


def _facade_reexport_is_allowed(
    *,
    source_path: str,
    facade_reference: str | None,
    target_owners: list[str],
    public_export_owners: dict[str, list[str]],
    formal_facade_paths: set[str],
) -> bool:
    return (
        source_path in formal_facade_paths
        and facade_reference in public_export_owners
        and any(owner in target_owners for owner in public_export_owners[facade_reference])
    )


def _resolved_imports(node: ast.AST, *, package: str) -> list[tuple[str, str | None, str | None]]:
    if isinstance(node, ast.Import):
        return [(alias.name, None, alias.asname) for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        base_module = (
            resolve_name(
                "." * node.level + (node.module or ""),
                package,
            )
            if node.level
            else (node.module or "")
        )
        return [(base_module, alias.name, alias.asname or alias.name) for alias in node.names]
    return []


def _lint_internal_imports(
    *,
    architecture_lint: dict[str, Any],
    source_paths: set[str],
    owners: dict[str, list[str]],
    components: dict[str, Any],
    public_export_owners: dict[str, list[str]],
    formal_facade_paths: set[str],
) -> int:
    module_paths, path_modules = _internal_module_paths(source_paths, architecture_lint)
    internal_package = architecture_lint["internal_package"]
    declared_exceptions: dict[tuple[str, str, str | None], str] = {}
    for item in architecture_lint["current_import_exceptions"]:
        if not isinstance(item, dict) or set(item) not in (
            {"from", "to", "reason"},
            {"from", "to", "symbol", "reason"},
        ):
            raise ComponentMapError(
                "architecture current import exceptions require from/to/optional-symbol/reason"
            )
        source = item["from"]
        target = item["to"]
        symbol = item.get("symbol")
        reason = item["reason"]
        if (
            not isinstance(source, str)
            or source not in path_modules
            or not isinstance(target, str)
            or target not in path_modules
            or (symbol is not None and (not isinstance(symbol, str) or not symbol))
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ComponentMapError(
                "architecture current import exceptions require mapped Python "
                "from/to paths, an optional non-empty symbol, and a reason"
            )
        declared_exceptions[(source, target, symbol)] = reason
    if len(declared_exceptions) != len(architecture_lint["current_import_exceptions"]):
        raise ComponentMapError(
            "architecture current import exceptions require unique from/to/symbol and reasons"
        )
    used_exceptions: set[tuple[str, str, str | None]] = set()
    violations: list[str] = []
    import_count = 0

    for source_path, source_module in sorted(path_modules.items()):
        source_owners = owners[source_path]
        package = (
            source_module
            if source_path.endswith("/__init__.py")
            else source_module.rpartition(".")[0]
        )
        tree = ast.parse(
            (ROOT / source_path).read_text(encoding="utf-8"),
            filename=source_path,
        )
        for node in ast.walk(tree):
            for base_module, symbol, bound_name in _resolved_imports(node, package=package):
                line_number = getattr(node, "lineno", 0)
                candidate_module = base_module
                if (
                    symbol is not None
                    and isinstance(node, ast.ImportFrom)
                    and f"{base_module}.{symbol}" in module_paths
                ):
                    candidate_module = f"{base_module}.{symbol}"
                target_module = _resolve_internal_module(
                    candidate_module,
                    internal_package=internal_package,
                    module_paths=module_paths,
                )
                if target_module is None:
                    continue
                target_path = module_paths[target_module]
                if target_path == source_path:
                    continue
                import_count += 1

                target_owners = owners[target_path]
                public_reference = f"{base_module}:{symbol}" if symbol is not None else None
                if public_reference in public_export_owners:
                    target_owners = public_export_owners[public_reference]

                facade_reference = (
                    f"{source_module}:{bound_name}" if bound_name is not None else None
                )
                if _facade_reexport_is_allowed(
                    source_path=source_path,
                    facade_reference=facade_reference,
                    target_owners=target_owners,
                    public_export_owners=public_export_owners,
                    formal_facade_paths=formal_facade_paths,
                ):
                    continue

                if _component_edge_is_allowed(source_owners, target_owners, components):
                    continue

                exception_key = (source_path, target_path, symbol)
                if exception_key in declared_exceptions:
                    used_exceptions.add(exception_key)
                    continue
                violations.append(
                    f"{source_path}:{line_number} imports "
                    f"{base_module}{':' + symbol if symbol else ''} -> {target_path}; "
                    f"source owners={source_owners}, target owners={target_owners}"
                )

    problems: list[str] = []
    if violations:
        problems.append("forbidden internal component imports:\n- " + "\n- ".join(violations))
    unused_exceptions = sorted(declared_exceptions.keys() - used_exceptions)
    if unused_exceptions:
        problems.append(f"unused current import exceptions: {unused_exceptions}")
    if problems:
        raise ComponentMapError("\n".join(problems))
    return import_count


def _validate_string_list(
    component_id: str, field: str, value: object, *, non_empty: bool = False
) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ComponentMapError(f"{component_id}: {field} must be a list of strings")
    if non_empty and not value:
        raise ComponentMapError(f"{component_id}: {field} must be non-empty")
    return value


def _find_dependency_cycle(components: dict[str, Any]) -> list[str] | None:
    completed: set[str] = set()
    active_positions: dict[str, int] = {}
    path: list[str] = []

    def visit(component_id: str) -> list[str] | None:
        if component_id in active_positions:
            start = active_positions[component_id]
            return [*path[start:], component_id]
        if component_id in completed:
            return None

        active_positions[component_id] = len(path)
        path.append(component_id)
        for dependency in components[component_id]["dependencies"]:
            cycle = visit(dependency)
            if cycle is not None:
                return cycle
        path.pop()
        active_positions.pop(component_id)
        completed.add(component_id)
        return None

    for component_id in components:
        cycle = visit(component_id)
        if cycle is not None:
            return cycle
    return None


def validate_component_map(data: dict[str, Any]) -> dict[str, int]:
    if set(data) != REQUIRED_TOP_LEVEL_FIELDS:
        raise ComponentMapError(
            "component map top-level fields differ: "
            f"missing={sorted(REQUIRED_TOP_LEVEL_FIELDS - data.keys())}, "
            f"unexpected={sorted(data.keys() - REQUIRED_TOP_LEVEL_FIELDS)}"
        )
    if data.get("version") != 1:
        raise ComponentMapError("component map version must be 1")

    layers = data.get("layers")
    inventory = data.get("inventory")
    components = data.get("components")
    structural = data.get("structural_status")
    target_docs = data.get("target_documentation")
    architecture_lint = data.get("architecture_lint")
    dependency_exceptions = data.get("dependency_exceptions")
    if not isinstance(layers, dict) or not layers:
        raise ComponentMapError("layers must be a non-empty mapping")
    if not isinstance(inventory, dict):
        raise ComponentMapError("inventory must be a mapping")
    source_patterns = inventory.get("current_source_patterns")
    if not isinstance(source_patterns, list) or not source_patterns:
        raise ComponentMapError("inventory current_source_patterns must be non-empty")
    if not isinstance(components, dict) or not components:
        raise ComponentMapError("components must be a non-empty mapping")
    if not isinstance(structural, dict):
        raise ComponentMapError("structural_status must be a mapping")
    if not isinstance(target_docs, dict) or target_docs.get("required_leaf_files") != [
        "design.md",
        "testing.md",
    ]:
        raise ComponentMapError("target documentation must require design.md and testing.md")
    expected_architecture_lint = {
        "internal_package",
        "source_root",
        "source_pattern",
        "symbol_owner_precedence",
        "split_candidate_policy",
        "formal_facade_policy",
        "current_import_exceptions",
    }
    if (
        not isinstance(architecture_lint, dict)
        or set(architecture_lint) != expected_architecture_lint
        or architecture_lint.get("internal_package") != "imagent"
        or architecture_lint.get("source_root") != "src"
        or architecture_lint.get("source_pattern") != "src/imagent/**/*.py"
        or architecture_lint.get("symbol_owner_precedence") != "exact-current-public-export"
        or architecture_lint.get("split_candidate_policy") != "require-one-explicit-owner-edge"
        or architecture_lint.get("formal_facade_policy") != "declared-reexports-only"
        or not isinstance(architecture_lint.get("current_import_exceptions"), list)
    ):
        raise ComponentMapError("invalid architecture_lint configuration")
    if not isinstance(dependency_exceptions, list):
        raise ComponentMapError("dependency_exceptions must be a list")
    allowed_exceptions = {
        (item.get("from"), item.get("to"))
        for item in dependency_exceptions
        if isinstance(item, dict) and item.get("reason")
    }
    if len(allowed_exceptions) != len(dependency_exceptions):
        raise ComponentMapError("dependency exceptions require unique from/to edges and reasons")

    source_paths = _source_paths(source_patterns)
    test_paths = _test_paths()
    owners: dict[str, list[str]] = defaultdict(list)
    test_owners: dict[str, list[str]] = defaultdict(list)
    public_contract_owners: dict[str, list[str]] = defaultdict(list)
    public_export_owners: dict[str, list[str]] = defaultdict(list)
    used_dependency_exceptions: set[tuple[str, str]] = set()

    for component_id, component in components.items():
        if not isinstance(component, dict):
            raise ComponentMapError(f"{component_id}: component must be a mapping")
        missing = REQUIRED_COMPONENT_FIELDS - component.keys()
        if missing:
            raise ComponentMapError(f"{component_id}: missing fields {sorted(missing)}")
        unexpected = component.keys() - REQUIRED_COMPONENT_FIELDS
        if unexpected:
            raise ComponentMapError(f"{component_id}: unexpected fields {sorted(unexpected)}")
        if component["id"] != component_id:
            raise ComponentMapError(f"{component_id}: id does not match mapping key")
        for field in NON_EMPTY_COMPONENT_TEXT_FIELDS:
            value = component[field]
            if not isinstance(value, str) or not value.strip():
                raise ComponentMapError(f"{component_id}: {field} must be non-empty text")
        for field in COMPONENT_LIST_FIELDS:
            _validate_string_list(
                component_id,
                field,
                component[field],
                non_empty=field in NON_EMPTY_COMPONENT_LIST_FIELDS,
            )
        parent = component["parent"]
        layer = component_id.split(".", 1)[0]
        expected_parent, separator, _ = component_id.rpartition(".")
        if (
            not separator
            or layer not in layers
            or not isinstance(parent, str)
            or parent != expected_parent
        ):
            raise ComponentMapError(f"{component_id}: invalid layer/parent {parent}")
        for contract in component["public_contracts"]:
            public_contract_owners[contract].append(component_id)

        for dependency in component["dependencies"]:
            if dependency not in components:
                raise ComponentMapError(f"{component_id}: unknown dependency {dependency}")
            allowed_layers = set(layers[layer]["may_depend_on"]) | {layer}
            if (
                dependency.split(".", 1)[0] not in allowed_layers
                and (component_id, dependency) not in allowed_exceptions
            ):
                raise ComponentMapError(
                    f"{component_id}: dependency {dependency} violates layer dependencies"
                )
            if (component_id, dependency) in allowed_exceptions:
                used_dependency_exceptions.add((component_id, dependency))

        exports = component["public_exports"]
        if not isinstance(exports, dict) or set(exports) != {"current", "target"}:
            raise ComponentMapError(f"{component_id}: public_exports needs current and target")
        _validate_string_list(component_id, "public_exports.current", exports["current"])
        _validate_string_list(component_id, "public_exports.target", exports["target"])
        for reference in exports["current"]:
            _validate_public_export(reference)
            public_export_owners[reference].append(component_id)
        current_symbols = {reference.partition(":")[2] for reference in exports["current"]}
        target_symbols = {reference.partition(":")[2] for reference in exports["target"]}
        contracts = set(component["public_contracts"])
        if component["current_code"] and current_symbols != contracts:
            raise ComponentMapError(
                f"{component_id}: current exports do not exactly bind public contracts: "
                f"exports={sorted(current_symbols)}, contracts={sorted(contracts)}"
            )
        if target_symbols != contracts:
            raise ComponentMapError(
                f"{component_id}: target exports do not exactly bind public contracts: "
                f"exports={sorted(target_symbols)}, contracts={sorted(contracts)}"
            )

        for field in ("current_code", "current_tests", "authoritative_docs", "adrs"):
            values = component[field]
            if not isinstance(values, list):
                raise ComponentMapError(f"{component_id}: {field} must be a list")
            for pattern in values:
                if not _existing_matches(pattern):
                    raise ComponentMapError(
                        f"{component_id}: {field} path has no current file: {pattern}"
                    )

        for pattern in component["current_code"]:
            for source_path in source_paths:
                if _matches(source_path, pattern):
                    owners[source_path].append(component_id)
        for pattern in component["current_tests"]:
            for test_path in test_paths:
                if _matches(test_path, pattern):
                    test_owners[test_path].append(component_id)

        for field in ("target_code", "target_tests"):
            if not isinstance(component[field], list) or not component[field]:
                raise ComponentMapError(f"{component_id}: {field} must be a non-empty list")

    dependency_cycle = _find_dependency_cycle(components)
    if dependency_cycle is not None:
        raise ComponentMapError(f"component dependency cycle: {' -> '.join(dependency_cycle)}")

    actual_orphans = sorted(path for path in source_paths if not owners[path])
    declared_orphans = structural.get("orphan_paths")
    if actual_orphans != declared_orphans:
        raise ComponentMapError(
            f"orphan paths differ: actual={actual_orphans}, declared={declared_orphans}"
        )
    orphan_tests = sorted(path for path in test_paths if not test_owners[path])
    if orphan_tests:
        raise ComponentMapError(f"unmapped current tests: {orphan_tests}")
    duplicate_contracts = {
        contract: contract_owners
        for contract, contract_owners in public_contract_owners.items()
        if len(contract_owners) > 1
    }
    if duplicate_contracts:
        raise ComponentMapError(f"public contracts have multiple owners: {duplicate_contracts}")
    duplicate_exports = {
        reference: export_owners
        for reference, export_owners in public_export_owners.items()
        if len(export_owners) > 1
    }
    if duplicate_exports:
        raise ComponentMapError(f"public exports have multiple owners: {duplicate_exports}")
    facade_exports = _public_facade_exports(source_paths)
    unmapped_facade_exports = sorted(facade_exports - public_export_owners.keys())
    if unmapped_facade_exports:
        raise ComponentMapError(f"unmapped __all__ public exports: {unmapped_facade_exports}")
    if used_dependency_exceptions != allowed_exceptions:
        raise ComponentMapError(
            "unused dependency exceptions: "
            f"{sorted(allowed_exceptions - used_dependency_exceptions)}"
        )

    actual_multi = {path: value for path, value in owners.items() if len(value) > 1}
    declared_multi = structural.get("split_candidates")
    if not isinstance(declared_multi, dict):
        raise ComponentMapError("split_candidates must be a mapping")
    if set(actual_multi) != set(declared_multi):
        raise ComponentMapError(
            "split candidates differ: "
            f"actual={sorted(actual_multi)}, declared={sorted(declared_multi)}"
        )
    for path, actual_owners in actual_multi.items():
        declaration = declared_multi[path]
        if declaration.get("owners") != actual_owners or not declaration.get("reason"):
            raise ComponentMapError(f"{path}: split candidate owners/reason do not match")

    facades = structural.get("formal_facades")
    if not isinstance(facades, list):
        raise ComponentMapError("formal_facades must be a list")
    for facade in facades:
        path = facade.get("path")
        owner = facade.get("owner")
        if path not in source_paths or owners[path] != [owner] or not facade.get("rationale"):
            raise ComponentMapError(f"invalid formal facade declaration: {facade}")

    internal_imports = _lint_internal_imports(
        architecture_lint=architecture_lint,
        source_paths=source_paths,
        owners=owners,
        components=components,
        public_export_owners=public_export_owners,
        formal_facade_paths={facade["path"] for facade in facades},
    )

    return {
        "components": len(components),
        "source_paths": len(source_paths),
        "test_paths": len(test_paths),
        "split_candidates": len(actual_multi),
        "orphans": len(actual_orphans),
        "public_facade_exports": len(facade_exports),
        "internal_imports": internal_imports,
    }


def main() -> None:
    summary = validate_component_map(load_component_map())
    print(
        "Validated component map: "
        f"{summary['components']} leaves, {summary['source_paths']} source paths, "
        f"{summary['test_paths']} tests, {summary['split_candidates']} split candidates, "
        f"{summary['orphans']} orphans, "
        f"{summary['public_facade_exports']} facade exports."
        f" {summary['internal_imports']} internal imports checked."
    )


if __name__ == "__main__":
    main()
