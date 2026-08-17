# Knowledge Schema v1

This document describes the structured knowledge schema for the AI Knowledge +
Tools Engine. The goal is to externalize knowledge into deterministic,
machine-readable files that tools can query without an AI/LLM present.

## Design goals

- Knowledge is **data, not code**. It lives as plain JSON files.
- **Relationships are first-class data** — not strings buried inside a
  `related` list. Every relationship is a structured object with a `type`
  and a `target` node id, so tools can traverse the graph deterministically.
- No embeddings, no vector database, no AI/LLM. Pure structured retrieval.
- Dependency-light and human-editable.

## File layout

Knowledge is stored as one JSON file per node under `knowledge/`. Files are
organized by node `type` into subfolders for scalability:

```
knowledge/
  technologies/   # type: technology
  concepts/       # type: concept
  entities/       # type: entity
  procedures/     # type: procedure
  rules/          # type: rule
  examples/       # type: example
  dependencies/   # type: dependency
```

The loader walks `knowledge/` recursively, so the exact subfolder layout is
conventional, not required — any `.json` file anywhere under `knowledge/`
is loaded as long as it matches the schema.

## Node types

| Type          | Meaning                                                              |
|---------------|----------------------------------------------------------------------|
| `concept`     | An abstract idea or definition (e.g. "React Component").             |
| `technology`  | A tool, library, language, or framework (e.g. "React").              |
| `entity`      | A concrete, named instance (e.g. a specific package, API, service).  |
| `procedure`   | A step-by-step process or how-to.                                    |
| `rule`        | A constraint, best practice, or guideline that must/may be followed. |
| `example`     | A concrete, worked illustration of another node.                     |
| `dependency`  | A declared dependency between two nodes (with optional constraint).  |

## Node format (required fields)

Every node file is a JSON object with at least:

```json
{
  "id": "unique-string-id",
  "name": "Human-readable name",
  "type": "concept | technology | entity | procedure | rule | example | dependency",
  "description": "Free-text description.",
  "relationships": []
}
```

- `id` (string, required): unique identifier used as the relationship target.
- `name` (string, recommended): human-readable label.
- `type` (string, required): one of the node types above.
- `description` (string, recommended): plain-language summary.
- Type-specific optional fields are allowed (e.g. `technology`, `code`,
  `steps`, `constraint`, `from`, `to`). They are not validated.
- `relationships` (array, optional): see below. Defaults to `[]`.

## Relationships (first-class)

`relationships` is a list of objects. Each relationship is a directed edge
from the current node to a `target` node id:

```json
{
  "type": "relationship-kind",
  "target": "target-node-id",
  "label": "optional human-readable explanation"
}
```

- `type` (string, required): the kind of relationship (see table below).
- `target` (string, required): the `id` of another node.
- `label` (string, optional): human-readable note about the edge.

A `target` may reference a node that is not currently loaded; tools simply
skip unresolved edges rather than failing.

### Relationship kinds (recommended, not enforced)

| Kind           | Meaning                                          |
|----------------|--------------------------------------------------|
| `depends_on`   | This node requires the target to function.       |
| `related_to`   | Loosely associated with the target.              |
| `part_of`      | This node is a part/component of the target.     |
| `instance_of`  | This node is an instance of the target concept.  |
| `implements`   | This node implements the target.                 |
| `extends`      | This node extends/derives from the target.       |
| `uses`         | This node uses the target.                       |
| `example_of`   | This node is an example of the target.           |
| `references`   | This node references the target.                 |

New kinds may be added freely; the schema only requires `type` and `target`
to be present and non-empty.

## Validation rules

A file is rejected (and recorded as an error, not loaded) when:

- It is not valid JSON.
- The top-level value is not a JSON object.
- It has no `id`, or `id` is not a non-empty string.
- It has no `type`, or `type` is not a known node type.
- `relationships` is present but not a list, or any relationship is missing
  `type` or `target`.

Invalid files never crash the loader; they are collected into an error list
so tooling can report them.

## How tools use this schema

Tools load all nodes into an in-memory index keyed by `id`, then:

1. **Search** by keyword over the serialized node (preserving the original
   keyword-retrieval behavior).
2. **Follow** relationships by resolving `target` ids against the index.
3. **Validate** files and surface errors for human correction.

All of this is deterministic and requires no model.
