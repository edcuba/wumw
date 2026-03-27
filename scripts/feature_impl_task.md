# Feature Implementation Task: `Field.choices_display(value)`

## Overview

Add a `choices_display(value)` instance method to `django.db.models.fields.Field` that returns the human-readable label for a given raw value from the field's `choices`.

This is the field-level counterpart to the model-instance helper `get_FOO_display()`.
It is useful when you have a `Field` object (not a model instance) and want to translate a stored value to its display string — for example in serializers, admin list logic, or migration utilities.

## What to implement

Add the following method to the `Field` class in
`django/db/models/fields/__init__.py`:

```python
def choices_display(self, value):
    """Return the human-readable label for *value* from self.choices.

    If *value* is not found in the choices, return *value* unchanged.
    If the field has no choices, return *value* unchanged.
    """
    ...
```

### Behaviour contract

| Situation | Return value |
|-----------|--------------|
| `value` found in flat choices | Corresponding label string |
| `value` found inside an optgroup | Corresponding label string |
| `value` not found in choices | `value` (the raw value, unchanged) |
| `self.choices` is `None` | `value` (the raw value, unchanged) |

### Files you need to read to implement this correctly

1. **`django/db/models/fields/__init__.py`** — where `Field` lives.
   Look at the `Field` class definition, the `choices` property, and the existing `flatchoices` property (around line 1120).  Read how `validate()` iterates over choices (around line 834) to understand the nested optgroup structure.

2. **`django/db/models/base.py`** — contains `Model._get_FIELD_display()` (around line 1335), which performs the same lookup on a model instance.  Your implementation should apply the same logic (use `field.flatchoices`, `make_hashable`, and `force_str`).

### Implementation hints

- `self.flatchoices` (a property on `Field`) already handles both flat and grouped choices — it returns a flat list of `(value, label)` pairs.
- `make_hashable` (from `django.utils.hashable`) ensures tuple values can be used as dict keys.
- `force_str` (from `django.utils.encoding`) coerces lazy translation strings to `str`.
- The fallback when a value is not found should be the raw `value`, not `None` and not an exception.

## Scope

- Add only the `choices_display` method to `Field`.  Do not modify any other class, add new files, or touch tests.
- No migration, no form change, no admin change.
- The method should be no more than ~8 lines.

## Verification

After implementing, run the project's existing test suite to confirm you have not broken anything:

```bash
python -m pytest tests/ -x -q
```

(A separate hidden test will be used by the trial runner to evaluate correctness.)
