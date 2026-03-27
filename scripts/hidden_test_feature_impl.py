"""
Hidden unit test for the feature-impl A/B task.

Tests the `choices_display(value)` method on `django.db.models.fields.Field`.

NOT shown to agents during the trial.  Run after each agent implementation to
record pass/fail.

Usage (from the benchmarks/django working copy):
    python /path/to/hidden_test_feature_impl.py

Or from the wumw root (with benchmarks/django in place):
    DJANGO_REPO=benchmarks/django python scripts/hidden_test_feature_impl.py

Exit code 0 = all tests pass; non-zero = failure.
"""

import sys
import os
import unittest

# Locate the Django source tree.  Prefer explicit env override, then default.
DJANGO_REPO = os.environ.get(
    "DJANGO_REPO",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmarks", "django"),
)
sys.path.insert(0, os.path.abspath(DJANGO_REPO))

# Minimal Django settings — only what Field construction needs.
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[],  # No apps needed; we only test Field methods directly.
        USE_TZ=True,
    )
    # Do NOT call django.setup() — INSTALLED_APPS is empty and Field tests
    # don't require the app registry or ORM machinery.


# Import Field classes directly; they work without a fully populated registry.
from django.db.models.fields import Field, CharField, IntegerField


class ChoicesDisplayTests(unittest.TestCase):
    """Tests for Field.choices_display(value)."""

    # ------------------------------------------------------------------
    # Basic flat choices (list of 2-tuples)
    # ------------------------------------------------------------------

    def test_flat_known_value_returns_label(self):
        """Returns the human-readable label for a known flat choice."""
        f = CharField(max_length=20, choices=[("fr", "French"), ("en", "English"), ("de", "German")])
        self.assertEqual(f.choices_display("fr"), "French")
        self.assertEqual(f.choices_display("en"), "English")
        self.assertEqual(f.choices_display("de"), "German")

    def test_flat_unknown_value_returns_value(self):
        """Falls back to the raw value when it is not in choices."""
        f = CharField(max_length=20, choices=[("fr", "French"), ("en", "English")])
        self.assertEqual(f.choices_display("zz"), "zz")

    def test_dict_choices(self):
        """Works when choices is passed as a plain dict."""
        f = CharField(max_length=20, choices={"y": "Yes", "n": "No"})
        self.assertEqual(f.choices_display("y"), "Yes")
        self.assertEqual(f.choices_display("n"), "No")

    # ------------------------------------------------------------------
    # Grouped / optgroup choices
    # ------------------------------------------------------------------

    def test_grouped_known_value(self):
        """Returns the correct label from inside a named option group."""
        choices = [
            ("Fruit", [("apple", "Apple"), ("banana", "Banana")]),
            ("Veg",   [("carrot", "Carrot")]),
        ]
        f = CharField(max_length=20, choices=choices)
        self.assertEqual(f.choices_display("apple"), "Apple")
        self.assertEqual(f.choices_display("carrot"), "Carrot")

    def test_grouped_unknown_value_returns_value(self):
        choices = [("Fruit", [("apple", "Apple")])]
        f = CharField(max_length=20, choices=choices)
        self.assertEqual(f.choices_display("mango"), "mango")

    # ------------------------------------------------------------------
    # Integer choices (non-string values)
    # ------------------------------------------------------------------

    def test_integer_known_value(self):
        f = IntegerField(choices=[(1, "One"), (2, "Two"), (3, "Three")])
        self.assertEqual(f.choices_display(1), "One")
        self.assertEqual(f.choices_display(3), "Three")

    def test_integer_unknown_value_returns_value(self):
        f = IntegerField(choices=[(1, "One"), (2, "Two")])
        self.assertEqual(f.choices_display(99), 99)

    # ------------------------------------------------------------------
    # No choices set on the field
    # ------------------------------------------------------------------

    def test_no_choices_returns_value_unchanged(self):
        """When the field has no choices the raw value is returned without error."""
        f = CharField(max_length=20)  # choices=None
        self.assertEqual(f.choices_display("anything"), "anything")
        self.assertEqual(f.choices_display(42), 42)

    # ------------------------------------------------------------------
    # Method presence
    # ------------------------------------------------------------------

    def test_method_exists_on_field(self):
        """Field must expose a callable choices_display() method."""
        f = Field()
        self.assertTrue(
            callable(getattr(f, "choices_display", None)),
            "Field does not have a callable choices_display() method",
        )

    # ------------------------------------------------------------------
    # Enum-style choices (IntegerChoices / TextChoices)
    # ------------------------------------------------------------------

    def test_integer_choices_enum(self):
        from django.db.models import IntegerChoices

        class Status(IntegerChoices):
            DRAFT = 1, "Draft"
            PUBLISHED = 2, "Published"

        f = IntegerField(choices=Status)
        # Enum members compare equal to their integer value.
        self.assertEqual(f.choices_display(Status.DRAFT), "Draft")
        self.assertEqual(f.choices_display(Status.PUBLISHED), "Published")
        # Plain int values should also work.
        self.assertEqual(f.choices_display(1), "Draft")

    def test_text_choices_enum(self):
        from django.db.models import TextChoices

        class Color(TextChoices):
            RED = "R", "Red"
            GREEN = "G", "Green"

        f = CharField(max_length=1, choices=Color)
        self.assertEqual(f.choices_display("R"), "Red")
        self.assertEqual(f.choices_display("G"), "Green")
        self.assertEqual(f.choices_display("B"), "B")  # unknown → raw value


if __name__ == "__main__":
    unittest.main()
